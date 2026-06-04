# Interaktiver Mediaplayer für Raspberry Pi 4 mit Touch-Display
# Zeigt Theaterfotografien geordnet nach Spielzeit und Produktion.
# Läuft ohne X-Server direkt über KMS/DRM (SDL2-Backend von Kivy).
#
# Erwartete Verzeichnisstruktur in ~/media:
#   2023-24/
#       Faust/
#           bild1.jpg ...
#       Hamlet/
#           ...
#   2024-25/
#       ...
#
# USB-Stick-Struktur:
#   <root>/          → Saisonen/Produktionen wie oben
#   basismedien/     → Bilder, die immer eingeblendet werden (flach)
#   skripte/         → Python-Dateien, die ins Home-Verzeichnis kopiert werden
#
# Kuration:
#   excluded.txt in einem Produktionsordner: eine Datei pro Zeile = ausgeschlossen
#   Eine Zeile '*' = ganzer Ordner ausgeschlossen
#   Diese Dateien werden per sync_onedrive.py --push-excluded zurück nach OneDrive übertragen.

import os
import threading
import shutil
import time
import socket
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
import json
from datetime import datetime
from pathlib import Path
from PIL import Image as PILImage

# Auf dem Raspberry Pi KMS/DRM nutzen (kein X-Server nötig).
# Auf dem Mac/PC läuft Kivy im nativen Fenster – keine Overrides nötig.
import platform
if platform.system() == 'Linux':
    os.environ.setdefault('KIVY_WINDOW', 'sdl2')
    os.environ.setdefault('SDL_VIDEODRIVER', 'kmsdrm')

from kivy.app import App                                                    # noqa: E402
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition  # noqa: E402
from kivy.uix.gridlayout import GridLayout                                  # noqa: E402
from kivy.uix.scrollview import ScrollView                                  # noqa: E402
from kivy.uix.boxlayout import BoxLayout                                    # noqa: E402
from kivy.uix.button import Button                                          # noqa: E402
from kivy.uix.image import Image as KivyImage                               # noqa: E402
from kivy.uix.popup import Popup                                            # noqa: E402
from kivy.uix.label import Label                                            # noqa: E402
from kivy.clock import Clock                                                # noqa: E402
from kivy.core.window import Window                                         # noqa: E402

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

MEDIA_DIR     = Path.home() / 'media'
BASIS_DIR     = Path.home() / 'basismedien'
THUMB_DIR     = Path.home() / '.thumbs'
USB_BASE_PATH = Path('/media/taf')

KURATION_PIN        = '1313'  # PIN für den Kurationsmodus – hier ändern
AUTOSTART_FILE      = Path.home() / '.mediaplayer_autostart.json'
SLIDESHOW_INTERVAL  = 5       # Sekunden pro Bild
GRUNDSTOCK_INTERVAL = 15       # Jedes N-te Bild in der Diashow ist ein Grundstock-Bild
                               # (0 = Grundstock deaktiviert)
THUMB_SIZE         = (500, 340)
KURATION_THUMB_SIZE = (200, 150)
TILE_COLS          = 4
GRID_PADDING       = 20
GRID_SPACING       = 16
KURATION_COLS      = 6       # Spalten im Bild-Kurationsmodus

IMAGE_EXTS   = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}
ALLOWED_EXTS = IMAGE_EXTS | {'.mp4', '.avi', '.mkv', '.mov'}

# Kurationsentscheidungen werden als JSONL geloggt (für späteres ML-Training).
# Eine Zeile pro Bild und Entscheidung.
# Tipp: ~/kuration_log.jsonl per Syncthing (Send & Receive) auf den Server übertragen,
# damit Entscheidungen aller Pis zentral gesammelt werden.
KURATION_LOG = Path.home() / 'kuration_log.jsonl'


def _log_kuration(entries: list):
    """Hängt Kurationsentscheidungen an ~/kuration_log.jsonl an."""
    if not entries:
        return
    try:
        with KURATION_LOG.open('a', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f'Kuration-Log fehlgeschlagen: {e}')

# ---------------------------------------------------------------------------
# Autostart-Konfiguration
# ---------------------------------------------------------------------------

def load_autostart() -> dict | None:
    """Gibt {'type': 'genre', 'path': '...'} oder {'type': 'folder', 'saison': ..., 'produktion': ...} zurück."""
    if not AUTOSTART_FILE.exists():
        return None
    try:
        return json.loads(AUTOSTART_FILE.read_text(encoding='utf-8'))
    except Exception:
        return None


def save_autostart(config: dict | None):
    if config is None:
        AUTOSTART_FILE.unlink(missing_ok=True)
    else:
        AUTOSTART_FILE.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')


def autostart_label(config: dict | None) -> str:
    if config is None:
        return 'Kein Autostart'
    if config.get('type') == 'genre':
        return f'🎭 Genre: {config["path"]}'
    if config.get('type') == 'folder':
        return f'📁 {config["saison"]} / {config["produktion"]}'
    return 'Kein Autostart'


# ---------------------------------------------------------------------------
# Syncthing-Status
# ---------------------------------------------------------------------------

def _syncthing_api_key() -> str:
    """Liest den Syncthing-API-Key aus der Konfigurationsdatei."""
    config = Path.home() / '.config' / 'syncthing' / 'config.xml'
    if not config.exists():
        return ''
    try:
        tree = ET.parse(config)
        key = tree.find('.//apikey')
        return key.text.strip() if key is not None and key.text else ''
    except Exception:
        return ''


def syncthing_completion() -> float | None:
    """
    Gibt den Syncthing-Fortschritt zurück (0–100) oder None wenn nicht erreichbar.
    100 = vollständig synchronisiert.
    """
    api_key = _syncthing_api_key()
    if not api_key:
        return None
    try:
        req = urllib.request.Request(
            'http://localhost:8384/rest/db/completion',
            headers={'X-API-Key': api_key},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            import json
            data = json.loads(resp.read())
            return float(data.get('completion', 100))
    except Exception:
        return None


def syncthing_monitor_loop(app: 'MediaplayerApp'):
    """
    Hintergrund-Thread: prüft alle 15 Sekunden ob Syncthing fertig ist.
    Aktualisiert den Sync-Status-Label in der App und löst
    einen UI-Refresh aus sobald der Sync abgeschlossen ist.
    """
    was_syncing = False
    while True:
        time.sleep(15)
        pct = syncthing_completion()
        if pct is None:
            # Syncthing nicht erreichbar (noch nicht gestartet o.ä.)
            Clock.schedule_once(lambda dt: app.set_sync_status(''))
            continue

        if pct < 100:
            was_syncing = True
            Clock.schedule_once(
                lambda dt, p=pct: app.set_sync_status(f'🔄 Sync {p:.0f} %')
            )
        else:
            Clock.schedule_once(lambda dt: app.set_sync_status(''))
            if was_syncing:
                # Sync gerade abgeschlossen → UI neu laden
                was_syncing = False
                invalidate_thumbnails()
                Clock.schedule_once(lambda dt: app.refresh_ui())
EXCLUDED_FILE = 'excluded.txt'

# Ordner und Dateien, die beim USB-Kopieren übersprungen werden.
# Deckt Papierkörbe von macOS, Windows und Linux ab.
USB_SKIP = {
    '.Trashes', '.Trash', '.trash',
    '$RECYCLE.BIN', 'RECYCLED', 'RECYCLER',
    'System Volume Information',
    '.Spotlight-V100', '.fseventsd', '.TemporaryItems',
}

for _d in (MEDIA_DIR, BASIS_DIR, THUMB_DIR):
    _d.mkdir(exist_ok=True)


def tile_size() -> tuple:
    """Kachelgröße dynamisch aus der Fensterbreite berechnen."""
    available = Window.width - 2 * GRID_PADDING - (TILE_COLS - 1) * GRID_SPACING
    w = max(int(available / TILE_COLS), 100)
    return w, int(w * 0.85)


def kuration_thumb_size() -> tuple:
    """Thumbnail-Größe für den Bild-Kurationsmodus."""
    available = Window.width - 2 * GRID_PADDING - (KURATION_COLS - 1) * GRID_SPACING
    w = max(int(available / KURATION_COLS), 60)
    return w, int(w * 0.75)


# ---------------------------------------------------------------------------
# Exclusion-Helpers (excluded.txt)
# ---------------------------------------------------------------------------

def load_excluded(folder: Path) -> set:
    f = folder / EXCLUDED_FILE
    if not f.exists():
        return set()
    return {l.strip() for l in f.read_text(encoding='utf-8').splitlines() if l.strip()}


def save_excluded(folder: Path, excluded: set):
    path = folder / EXCLUDED_FILE
    if excluded:
        path.write_text('\n'.join(sorted(excluded)), encoding='utf-8')
    elif path.exists():
        path.unlink()


def is_folder_excluded(folder: Path) -> bool:
    return '*' in load_excluded(folder)


def toggle_folder_excluded(folder: Path) -> bool:
    """Schaltet Ordner-Ausschluss um. Gibt neuen Zustand zurück."""
    ex = load_excluded(folder)
    if '*' in ex:
        ex.discard('*')
    else:
        ex.add('*')
    save_excluded(folder, ex)
    return '*' in ex


# ---------------------------------------------------------------------------
# Genres (hierarchisch, genre.txt pro Produktion – zentral am Server gepflegt)
# ---------------------------------------------------------------------------

GENRE_FILE = 'genre.txt'


def load_genres(folder: Path) -> list:
    """Liest genre.txt eines Produktionsordners → Liste hierarchischer Genres."""
    f = folder / GENRE_FILE
    if not f.exists():
        return []
    return [l.strip() for l in f.read_text(encoding='utf-8').splitlines() if l.strip()]


def iter_produktionen():
    """Liefert alle Produktionsordner (Saison/Produktion) aus MEDIA_DIR."""
    for saison in MEDIA_DIR.iterdir():
        if not saison.is_dir() or saison.name == 'basismedien':
            continue
        for prod in saison.iterdir():
            if prod.is_dir():
                yield prod


def build_genre_index() -> dict:
    """
    Baut den Genre→Produktionen-Index aus allen genre.txt-Dateien.
    Gibt dict zurück: vollständiger Genre-Pfad → Liste von Produktionsordnern.
    """
    index = {}
    for prod in iter_produktionen():
        if is_folder_excluded(prod):
            continue
        for genre in load_genres(prod):
            index.setdefault(genre, []).append(prod)
    return index


def genre_kinder(genre_index: dict, prefix: str = '') -> list:
    """
    Gibt die direkten Kind-Knoten unterhalb von 'prefix' im Genre-Baum zurück.
    prefix='' → oberste Ebene. Beispiel: 'JungesEnsemble' → ['Kinder', 'Jugendliche'].
    Rückgabe: sortierte Liste von (kindname, vollpfad).
    """
    tiefe = 0 if not prefix else prefix.count('/') + 1
    kinder = set()
    for genre in genre_index:
        teile = genre.split('/')
        if prefix:
            p_teile = prefix.split('/')
            if teile[:len(p_teile)] != p_teile or len(teile) <= len(p_teile):
                continue
        if len(teile) > tiefe:
            kindname = teile[tiefe]
            vollpfad = '/'.join(teile[:tiefe + 1])
            kinder.add((kindname, vollpfad))
    return sorted(kinder)


def produktionen_fuer_genre(genre_index: dict, genre_prefix: str) -> list:
    """
    Alle Produktionen, deren Genre exakt 'genre_prefix' ist ODER darunter liegt
    (Oberkategorie aggregiert alle Unterkategorien). Ohne Duplikate.
    """
    seen = set()
    result = []
    for genre, prods in genre_index.items():
        if genre == genre_prefix or genre.startswith(genre_prefix + '/'):
            for p in prods:
                if p not in seen:
                    seen.add(p)
                    result.append(p)
    return result


def bilder_fuer_genre(genre_index: dict, genre_prefix: str) -> list:
    """Alle (nicht ausgeschlossenen) Bilder aller Produktionen eines Genres."""
    bilder = []
    for prod in produktionen_fuer_genre(genre_index, genre_prefix):
        bilder.extend(get_image_files(prod))
    return bilder


# ---------------------------------------------------------------------------
# Thumbnail-Hilfsfunktionen
# ---------------------------------------------------------------------------

def make_thumbnail(folder: Path) -> str:
    """Ordner-Thumbnail (erstes Bild im Ordner); gecacht in THUMB_DIR."""
    dest = THUMB_DIR / f"{folder.parent.name}__{folder.name}.jpg"
    if dest.exists():
        return str(dest)
    for img_file in sorted(folder.rglob('*')):
        if img_file.suffix.lower() not in IMAGE_EXTS:
            continue
        try:
            img = PILImage.open(img_file)
            img.thumbnail(THUMB_SIZE)
            img.convert('RGB').save(str(dest), 'JPEG')
            return str(dest)
        except Exception:
            continue
    return ''


def make_image_thumbnail(img_path: Path) -> str:
    """Einzel-Bild-Thumbnail für den Kurationsmodus; gecacht in THUMB_DIR."""
    key = f"img__{img_path.parent.parent.name}__{img_path.parent.name}__{img_path.stem}.jpg"
    dest = THUMB_DIR / key
    if dest.exists():
        return str(dest)
    try:
        img = PILImage.open(img_path)
        img.thumbnail(KURATION_THUMB_SIZE)
        img.convert('RGB').save(str(dest), 'JPEG')
        return str(dest)
    except Exception:
        return ''


def invalidate_thumbnails():
    for f in THUMB_DIR.iterdir():
        if f.suffix == '.jpg':
            f.unlink(missing_ok=True)


def get_image_files(folder: Path) -> list:
    """Alle Bilder im Ordner, ohne ausgeschlossene Dateien / Ordner."""
    excluded = load_excluded(folder)
    if '*' in excluded:
        return []
    return sorted(
        f for f in folder.rglob('*')
        if f.suffix.lower() in IMAGE_EXTS and f.name not in excluded
    )


def get_all_image_files(folder: Path) -> list:
    """Alle Bilder im Ordner – auch ausgeschlossene (für den Kurationsmodus)."""
    return sorted(f for f in folder.rglob('*') if f.suffix.lower() in IMAGE_EXTS)


def load_quality_scores(folder: Path) -> dict:
    """Lädt quality_scores.json aus einem Produktionsordner (vom Server via Syncthing)."""
    score_file = folder / 'quality_scores.json'
    if not score_file.exists():
        return {}
    try:
        import json
        return json.loads(score_file.read_text(encoding='utf-8'))
    except Exception:
        return {}


def get_folder_quality_summary(folder: Path) -> tuple:
    """Gibt (gesamt, flagged) für einen Ordner zurück – rekursiv über Unterordner."""
    scores  = load_quality_scores(folder)
    # Direkte Bilder in diesem Ordner
    direct  = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
    total   = len(direct)
    flagged = sum(1 for f in direct if scores.get(f.name, {}).get('flagged'))
    # Unterordner rekursiv
    for sub in folder.iterdir():
        if sub.is_dir():
            sub_total, sub_flagged = get_folder_quality_summary(sub)
            total   += sub_total
            flagged += sub_flagged
    return total, flagged


def has_subfolders(folder: Path) -> bool:
    """True wenn der Ordner Unterverzeichnisse enthält."""
    return any(f.is_dir() for f in folder.iterdir())


def get_grundstock_images() -> list:
    """Alle Grundstock-Bilder aus BASIS_DIR."""
    if not BASIS_DIR.exists():
        return []
    return sorted(f for f in BASIS_DIR.rglob('*') if f.suffix.lower() in IMAGE_EXTS)


def interleave_grundstock(images: list, grundstock: list, interval: int) -> list:
    """Streut Grundstock-Bilder alle `interval` Bilder in die Liste ein.
    Die Grundstock-Bilder rotieren durch, egal wie lang die Produktion ist."""
    if not grundstock or interval <= 0:
        return images
    result = []
    g_idx = 0
    for i, img in enumerate(images):
        result.append(img)
        if (i + 1) % interval == 0:
            result.append(grundstock[g_idx % len(grundstock)])
            g_idx += 1
    return result


# ---------------------------------------------------------------------------
# USB-Monitor (Daemon-Thread)
# ---------------------------------------------------------------------------

def _get_usb_path() -> Path | None:
    if not USB_BASE_PATH.exists():
        return None
    for device in USB_BASE_PATH.iterdir():
        if device.is_mount():
            return device
    return None


def _clear_dir(directory: Path):
    for item in directory.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)


def _is_usb_skip(path: Path) -> bool:
    """True für Papierkorb-Ordner und versteckte Systemdateien aller OS."""
    name = path.name
    return name in USB_SKIP or name.startswith('.') or name.startswith('$')


def _copy_flat(src: Path, dst: Path):
    """Kopiert Mediendateien flach (nur eine Ebene, für basismedien/skripte)."""
    for item in src.iterdir():
        if _is_usb_skip(item):
            continue
        if item.is_file() and item.suffix.lower() in ALLOWED_EXTS:
            shutil.copy2(item, dst / item.name)


def _copy_recursive(src: Path, dst: Path):
    """Kopiert Mediendateien rekursiv und erhält Unterordner-Struktur.
    Namenskonflikte werden durch Umbenennen aufgelöst (nicht überschrieben)."""
    for item in src.iterdir():
        if _is_usb_skip(item):
            continue
        if item.is_dir():
            sub_dst = dst / item.name
            sub_dst.mkdir(exist_ok=True)
            _copy_recursive(item, sub_dst)
        elif item.is_file() and item.suffix.lower() in ALLOWED_EXTS:
            target = dst / item.name
            if target.exists():
                # Namenskonflikt: Suffix ergänzen
                stem, ext = item.stem, item.suffix
                target = dst / f'{stem}__{item.parent.name}{ext}'
            shutil.copy2(item, target)


def _copy_structured(src: Path, dst: Path):
    for saison in src.iterdir():
        if _is_usb_skip(saison):
            continue
        if saison.name in ('basismedien', 'skripte') or not saison.is_dir():
            continue
        target_saison = dst / saison.name
        target_saison.mkdir(exist_ok=True)
        for produktion in saison.iterdir():
            if produktion.is_dir() and not _is_usb_skip(produktion):
                target_prod = target_saison / produktion.name
                target_prod.mkdir(exist_ok=True)
                _copy_recursive(produktion, target_prod)  # Unterordner erhalten


def usb_monitor_loop(app: 'MediaplayerApp'):
    while True:
        time.sleep(5)
        usb_path = _get_usb_path()
        if not usb_path:
            continue
        print(f'USB gefunden: {usb_path}')
        basis_usb = usb_path / 'basismedien'
        if basis_usb.exists():
            # Merge statt Replace: vorhandene Dateien bleiben erhalten.
            # Syncthing verteilt neue Dateien automatisch an Server + andere Pis.
            _copy_flat(basis_usb, BASIS_DIR)
        skripte_usb = usb_path / 'skripte'
        if skripte_usb.exists():
            _copy_flat(skripte_usb, Path.home())
        _clear_dir(MEDIA_DIR)
        _copy_structured(usb_path, MEDIA_DIR)
        subprocess.run(['umount', str(usb_path)], check=False)
        print('USB ausgeworfen.')
        invalidate_thumbnails()
        Clock.schedule_once(lambda dt: app.refresh_ui())


# ---------------------------------------------------------------------------
# Geteilte Hilfsfunktionen für UI
# ---------------------------------------------------------------------------

def _go_to(manager, target: str, direction: str = 'left'):
    manager.transition = SlideTransition(direction=direction)
    manager.current = target


def make_grid(cols: int) -> GridLayout:
    grid = GridLayout(cols=cols, spacing=GRID_SPACING, padding=GRID_PADDING, size_hint_y=None)
    grid.bind(minimum_height=grid.setter('height'))
    return grid


# ---------------------------------------------------------------------------
# Widgets: normale Ansicht
# ---------------------------------------------------------------------------

class Kachel(BoxLayout):
    """Vorschaukachel mit Thumbnail und Beschriftung."""

    def __init__(self, title: str, thumb_path: str, callback, **kwargs):
        super().__init__(orientation='vertical', padding=8, spacing=4, **kwargs)
        self._callback = callback
        w, h = tile_size()
        self.size_hint = (None, None)
        self.size = (w, h)
        img_h = h - 60

        if thumb_path and Path(thumb_path).exists():
            self.add_widget(KivyImage(
                source=thumb_path, size_hint=(1, None), height=img_h,
                allow_stretch=True, keep_ratio=True,
            ))
        else:
            self.add_widget(Label(
                text='[Kein\nVorschaubild]', halign='center',
                size_hint=(1, None), height=img_h,
            ))
        self.add_widget(Label(
            text=title, font_size='20sp',
            size_hint=(1, None), height=52,
            halign='center', text_size=(w - 16, None),
        ))

    def on_touch_up(self, touch):
        if self.collide_point(*touch.pos):
            self._callback()
            return True
        return super().on_touch_up(touch)


# ---------------------------------------------------------------------------
# Widgets: Kuration
# ---------------------------------------------------------------------------

class KurationOrdnerKachel(BoxLayout):
    """Kachel im Kurationsmodus: Thumbnail + ✗/✓-Button für ganzen Ordner."""

    def __init__(self, prod_path: Path, excluded: bool, on_drill, on_toggle, on_delete, **kwargs):
        super().__init__(orientation='vertical', padding=8, spacing=4, **kwargs)
        w, h = tile_size()
        self.size_hint = (None, None)
        self.size = (w, h)
        img_h = h - 120

        thumb = make_thumbnail(prod_path)
        img = KivyImage(
            source=thumb if (thumb and Path(thumb).exists()) else '',
            size_hint=(1, None), height=img_h,
            allow_stretch=True, keep_ratio=True,
            opacity=0.3 if excluded else 1.0,
        )
        self.add_widget(img)

        self.add_widget(Label(
            text=prod_path.name, font_size='18sp',
            size_hint=(1, None), height=30,
            halign='center', text_size=(w - 16, None),
            color=(0.5, 0.5, 0.5, 1) if excluded else (1, 1, 1, 1),
        ))

        # Qualitätsstatistik
        total, flagged = get_folder_quality_summary(prod_path)
        if total > 0:
            flag_ratio = flagged / total
            if flagged == 0:
                stat_text  = f'✓ {total} Bilder'
                stat_color = (0.5, 0.8, 0.5, 1)
            elif flag_ratio > 0.4:
                stat_text  = f'⚠ {flagged} / {total}  – viele schlecht'
                stat_color = (1.0, 0.5, 0.1, 1)   # orange: ganzen Ordner ausschließen?
            else:
                stat_text  = f'⚠ {flagged} / {total}'
                stat_color = (1.0, 0.85, 0.2, 1)  # gelb
        else:
            stat_text  = 'Keine Bilder'
            stat_color = (0.5, 0.5, 0.5, 1)

        self.add_widget(Label(
            text=stat_text, font_size='14sp',
            size_hint=(1, None), height=24,
            halign='center', color=stat_color,
        ))

        controls = BoxLayout(size_hint=(1, None), height=48, spacing=8)

        btn_drill = Button(text='Bilder →', size_hint=(1, 1), font_size='14sp')
        btn_drill.bind(on_press=lambda *a: on_drill())

        btn_toggle = Button(
            text='✓ einschl.' if excluded else '✗ ausschl.',
            size_hint=(None, 1), width=120, font_size='13sp',
            background_color=(0.2, 0.7, 0.2, 1) if excluded else (0.7, 0.2, 0.2, 1),
        )
        btn_toggle.bind(on_press=lambda *a: on_toggle())

        btn_delete = Button(
            text='🗑', size_hint=(None, 1), width=54, font_size='18sp',
            background_color=(0.5, 0.15, 0.15, 1),
        )
        btn_delete.bind(on_press=lambda *a: on_delete())

        controls.add_widget(btn_drill)
        controls.add_widget(btn_toggle)
        controls.add_widget(btn_delete)
        self.add_widget(controls)


class BildToggle(BoxLayout):
    """Einzelbild-Thumbnail mit Ausschluss- und Löschen-Button."""

    def __init__(self, img_path: Path, excluded: bool, marked_delete: bool,
                 on_toggle, on_delete_toggle, quality_info=None, **kwargs):
        super().__init__(orientation='vertical', padding=2, spacing=2, **kwargs)
        w, h = kuration_thumb_size()
        quality_row = 20 if (quality_info and quality_info.get('flagged')) else 0
        self.size_hint = (None, None)
        self.size = (w, h + 52 + quality_row)
        self._excluded = excluded
        self._marked_delete = marked_delete
        self._on_toggle = on_toggle
        self._on_delete_toggle = on_delete_toggle

        thumb = make_image_thumbnail(img_path)
        opacity = 0.15 if marked_delete else (0.3 if excluded else 1.0)
        self._img = KivyImage(
            source=thumb if thumb else '',
            size_hint=(1, None), height=h,
            allow_stretch=True, keep_ratio=True,
            opacity=opacity,
        )
        self.add_widget(self._img)

        self._btn_excl = Button(
            text='✗ aus' if excluded else '✓ ein',
            size_hint=(1, None), height=24, font_size='11sp',
            background_color=(0.55, 0.55, 0.55, 1) if excluded else (0.2, 0.65, 0.2, 1),
        )
        self._btn_excl.bind(on_press=self._toggle_excl)
        self.add_widget(self._btn_excl)

        self._btn_del = Button(
            text='↩ behalten' if marked_delete else '🗑 löschen',
            size_hint=(1, None), height=24, font_size='11sp',
            background_color=(0.6, 0.3, 0.1, 1) if marked_delete else (0.35, 0.35, 0.35, 1),
        )
        self._btn_del.bind(on_press=self._toggle_del)
        self.add_widget(self._btn_del)

        # Qualitäts-Badge (vom Server berechnet)
        if quality_info and quality_info.get('flagged'):
            reason = quality_info.get('reason', '⚠')
            self.add_widget(Label(
                text=f'⚠ {reason}',
                size_hint=(1, None), height=20, font_size='10sp',
                color=(1, 0.75, 0, 1),
            ))

    def _toggle_excl(self, *args):
        self._excluded = not self._excluded
        self._img.opacity = 0.15 if self._marked_delete else (0.3 if self._excluded else 1.0)
        self._btn_excl.text = '✗ aus' if self._excluded else '✓ ein'
        self._btn_excl.background_color = (
            (0.55, 0.55, 0.55, 1) if self._excluded else (0.2, 0.65, 0.2, 1)
        )
        self._on_toggle(self._excluded)

    def _toggle_del(self, *args):
        self._marked_delete = not self._marked_delete
        self._img.opacity = 0.15 if self._marked_delete else (0.3 if self._excluded else 1.0)
        self._btn_del.text = '↩ behalten' if self._marked_delete else '🗑 löschen'
        self._btn_del.background_color = (
            (0.6, 0.3, 0.1, 1) if self._marked_delete else (0.35, 0.35, 0.35, 1)
        )
        self._on_delete_toggle(self._marked_delete)


# ---------------------------------------------------------------------------
# PIN-Popup
# ---------------------------------------------------------------------------

class PinPopup(Popup):
    """Zahlenpad-Popup. Ruft on_success() bei korrektem PIN auf."""

    def __init__(self, on_success, **kwargs):
        self._on_success = on_success
        self._entered = ''

        # Anzeige-Label
        self._display = Label(text='', font_size='32sp', size_hint=(1, None), height=60)

        # Zahlentasten 1–9, dann 0 + Löschen
        numpad = GridLayout(cols=3, spacing=8, size_hint=(1, 1))
        for digit in '123456789':
            btn = Button(text=digit, font_size='28sp')
            btn.bind(on_press=lambda b, d=digit: self._press(d))
            numpad.add_widget(btn)

        btn_clear = Button(text='⌫', font_size='28sp', background_color=(0.6, 0.3, 0.3, 1))
        btn_clear.bind(on_press=lambda *a: self._clear())
        btn_zero = Button(text='0', font_size='28sp')
        btn_zero.bind(on_press=lambda *a: self._press('0'))
        btn_cancel = Button(text='Abbrechen', font_size='20sp', background_color=(0.4, 0.4, 0.4, 1))
        btn_cancel.bind(on_press=lambda *a: self.dismiss())

        numpad.add_widget(btn_clear)
        numpad.add_widget(btn_zero)
        numpad.add_widget(btn_cancel)

        content = BoxLayout(orientation='vertical', padding=16, spacing=12)
        content.add_widget(Label(text='PIN eingeben', font_size='20sp', size_hint=(1, None), height=40))
        content.add_widget(self._display)
        content.add_widget(numpad)

        super().__init__(
            title='',
            content=content,
            size_hint=(None, None),
            size=(380, 480),
            auto_dismiss=False,
            separator_height=0,
        )

    def _press(self, digit: str):
        if len(self._entered) >= len(KURATION_PIN):
            return
        self._entered += digit
        self._display.text = '●' * len(self._entered)
        if len(self._entered) == len(KURATION_PIN):
            if self._entered == KURATION_PIN:
                self.dismiss()
                self._on_success()
            else:
                self._display.text = 'Falscher PIN'
                Clock.schedule_once(lambda dt: self._reset(), 1.2)

    def _clear(self):
        self._entered = self._entered[:-1]
        self._display.text = '●' * len(self._entered)

    def _reset(self):
        self._entered = ''
        self._display.text = ''


# ---------------------------------------------------------------------------
# Screens: normale Ansicht
# ---------------------------------------------------------------------------

class SpielsaisonScreen(Screen):
    """Ebene 1: Alle Spielzeiten als Kacheln."""

    def on_pre_enter(self, *args):
        self._build()

    def _build(self):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')

        header = BoxLayout(size_hint=(1, None), height=70)
        header.add_widget(Label(text='Spielzeiten', font_size='30sp'))
        app = App.get_running_app()
        app._sync_label = Label(
            text=getattr(getattr(app, '_sync_label', None), 'text', ''),
            font_size='15sp', size_hint=(None, 1), width=180,
            color=(0.4, 0.8, 1, 1),
        )
        header.add_widget(app._sync_label)
        # Genre-Button nur zeigen, wenn überhaupt Genres vergeben sind
        if build_genre_index():
            btn_genre = Button(text='🎭 Genres', size_hint=(None, 1), width=150, font_size='18sp')
            btn_genre.bind(on_press=self._enter_genres)
            header.add_widget(btn_genre)
        btn_kuration = Button(text='✏ Kuration', size_hint=(None, 1), width=160, font_size='18sp')
        btn_kuration.bind(on_press=self._enter_kuration)
        header.add_widget(btn_kuration)
        btn_autostart = Button(text='⚙', size_hint=(None, 1), width=56, font_size='22sp',
                               background_color=(0.25, 0.25, 0.4, 1))
        btn_autostart.bind(on_press=lambda *a: self._enter_autostart())
        header.add_widget(btn_autostart)
        root.add_widget(header)

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        saisons = sorted(
            (d for d in MEDIA_DIR.iterdir() if d.is_dir() and d.name != 'basismedien'),
            reverse=True,
        )
        for saison in saisons:
            grid.add_widget(Kachel(
                title=saison.name,
                thumb_path=make_thumbnail(saison),
                callback=lambda s=saison: self._open(s),
            ))

        if not saisons:
            grid.add_widget(Label(
                text='Noch keine Medien vorhanden.\nUSB-Stick anstecken.',
                halign='center',
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _open(self, saison_path: Path):
        self.manager.get_screen('produktionen').load(saison_path)
        _go_to(self.manager, 'produktionen')

    def _enter_kuration(self, *args):
        def _open():
            self.manager.get_screen('kuration_saison').build()
            _go_to(self.manager, 'kuration_saison')
        PinPopup(on_success=_open).open()

    def _enter_autostart(self, *args):
        self.manager.get_screen('autostart').open()

    def _enter_genres(self, *args):
        self.manager.get_screen('genre').load(prefix='', back_target='spielsaison')
        _go_to(self.manager, 'genre')


class GenreScreen(Screen):
    """Hierarchische Genre-Navigation: Oberkategorien → Unterkategorien → Diashow."""

    def load(self, prefix: str = '', back_target: str = 'spielsaison'):
        self._prefix      = prefix
        self._root_target = back_target   # wohin "Zurück" von der obersten Ebene führt
        self._build()

    def _build(self):
        self.clear_widgets()
        index = build_genre_index()
        root = BoxLayout(orientation='vertical')

        # Header mit Eltern-bewusstem Zurück-Button
        header = BoxLayout(size_hint=(1, None), height=70)
        btn_back = Button(text='← Zurück', size_hint=(None, 1), width=160, font_size='18sp')
        btn_back.bind(on_press=self._zurueck)
        header.add_widget(btn_back)
        titel = f'Genre: {self._prefix}' if self._prefix else 'Genres'
        header.add_widget(Label(text=titel, font_size='24sp'))
        root.add_widget(header)

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        # "Alle anzeigen"-Kachel, wenn wir in einer Oberkategorie mit Treffern sind
        if self._prefix:
            bilder = bilder_fuer_genre(index, self._prefix)
            if bilder:
                grid.add_widget(Kachel(
                    title=f'▶ Alle anzeigen ({len(bilder)})',
                    thumb_path=make_image_thumbnail(bilder[0]),
                    callback=lambda b=bilder: self._play(b, self._prefix),
                ))

        for kindname, vollpfad in genre_kinder(index, self._prefix):
            bilder      = bilder_fuer_genre(index, vollpfad)
            unterkinder = genre_kinder(index, vollpfad)
            thumb       = make_image_thumbnail(bilder[0]) if bilder else ''
            anzahl      = len(bilder)
            if unterkinder:
                grid.add_widget(Kachel(
                    title=f'{kindname}  ▸ ({anzahl})',
                    thumb_path=thumb,
                    callback=lambda v=vollpfad: self._drill(v),
                ))
            else:
                grid.add_widget(Kachel(
                    title=f'{kindname} ({anzahl})',
                    thumb_path=thumb,
                    callback=lambda b=bilder, v=vollpfad: self._play(b, v),
                ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _zurueck(self, *args):
        if not self._prefix:
            _go_to(self.manager, self._root_target, 'right')
        else:
            # Eine Genre-Ebene nach oben
            self._prefix = '/'.join(self._prefix.split('/')[:-1])
            self._build()

    def _drill(self, vollpfad: str):
        self._prefix = vollpfad
        self._build()

    def _play(self, bilder: list, titel: str):
        if not bilder:
            return
        self.manager.get_screen('slideshow').load_images(
            bilder, f'Genre: {titel}', back_target='genre'
        )
        _go_to(self.manager, 'slideshow')


class ProduktionenScreen(Screen):
    """Ebene 2: Produktionen innerhalb einer Spielzeit."""

    def load(self, saison_path: Path):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')
        root.add_widget(make_header(saison_path.name, '← Zurück', 'spielsaison', self.manager))

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        for prod in sorted(p for p in saison_path.iterdir() if p.is_dir()):
            if is_folder_excluded(prod):
                continue
            grid.add_widget(Kachel(
                title=prod.name,
                thumb_path=make_thumbnail(prod),
                callback=lambda p=prod: self._start_slideshow(p),
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _start_slideshow(self, prod_path: Path):
        self.manager.get_screen('slideshow').load(prod_path)
        _go_to(self.manager, 'slideshow')


QUICK_KURATION_TIMEOUT = 60  # Sekunden bis Auto-Deaktivierung


class SlideshowScreen(Screen):
    """Ebene 3: Bilder einer Produktion als Diashow."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._images = []
        self._index = 0
        self._timer = None
        self._prod_name = ''
        self._back_target = 'produktionen'
        self._quick_kuration = False
        self._kuration_timeout = None

        root = BoxLayout(orientation='vertical')
        self._img = KivyImage(allow_stretch=True, keep_ratio=True)
        root.add_widget(self._img)

        bar = BoxLayout(size_hint=(1, None), height=60)
        btn_back = Button(text='← Zurück', size_hint=(None, 1), width=160, font_size='18sp')
        btn_back.bind(on_press=lambda *a: _go_to(self.manager, self._back_target, 'right'))
        btn_prev = Button(text='‹', size_hint=(None, 1), width=80, font_size='24sp')
        btn_next = Button(text='›', size_hint=(None, 1), width=80, font_size='24sp')
        btn_prev.bind(on_press=self._prev)
        btn_next.bind(on_press=self._next)
        self._info = Label(font_size='16sp')
        self._btn_kuration = Button(
            text='✗', size_hint=(None, 1), width=60, font_size='22sp',
            background_color=(0.35, 0.35, 0.35, 1),
        )
        self._btn_kuration.bind(on_press=self._kuration_press)
        bar.add_widget(btn_back)
        bar.add_widget(btn_prev)
        bar.add_widget(self._info)
        bar.add_widget(btn_next)
        bar.add_widget(self._btn_kuration)
        root.add_widget(bar)
        self.add_widget(root)

    def load(self, prod_path: Path):
        """Diashow einer einzelnen Produktion."""
        prod_images = get_image_files(prod_path)
        self.load_images(prod_images, prod_path.name, back_target='produktionen')

    def load_images(self, images: list, title: str, back_target: str = 'produktionen'):
        """Diashow aus einer beliebigen Bilderliste (z.B. genreübergreifend)."""
        self._prod_name    = title
        self._back_target  = back_target
        grundstock         = get_grundstock_images()
        self._images       = interleave_grundstock(list(images), grundstock, GRUNDSTOCK_INTERVAL)
        self._index        = 0
        self._deactivate_quick_kuration()
        self._show()

    def on_enter(self, *args):
        self._timer = Clock.schedule_interval(self._advance, SLIDESHOW_INTERVAL)

    def on_leave(self, *args):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._deactivate_quick_kuration()

    def _show(self):
        if not self._images:
            self._info.text = 'Keine Bilder vorhanden.'
            return
        current = self._images[self._index]
        self._img.source = str(current)
        if current.is_relative_to(BASIS_DIR):
            self._info.text = '★ Unsere Förderer'
        else:
            self._info.text = f'{self._prod_name}  –  {self._index + 1} / {len(self._images)}'
        # Ausblenden-Button nur bei kurationierbaren Bildern zeigen
        self._btn_kuration.opacity = 0 if current.is_relative_to(BASIS_DIR) else 1
        self._btn_kuration.disabled = current.is_relative_to(BASIS_DIR)

    def _advance(self, dt):
        if self._images:
            self._index = (self._index + 1) % len(self._images)
            self._show()

    def _prev(self, *args):
        if self._images:
            self._index = (self._index - 1) % len(self._images)
            self._show()

    def _next(self, *args):
        if self._images:
            self._index = (self._index + 1) % len(self._images)
            self._show()

    # --- Quick-Kuration ---

    def _kuration_press(self, *args):
        if self._quick_kuration:
            self._exclude_current()
        else:
            PinPopup(on_success=self._activate_quick_kuration).open()

    def _activate_quick_kuration(self):
        self._quick_kuration = True
        self._btn_kuration.background_color = (0.75, 0.15, 0.15, 1)
        self._btn_kuration.text = '✗ ausblenden'
        self._btn_kuration.width = 160
        self._reset_kuration_timeout()

    def _deactivate_quick_kuration(self):
        self._quick_kuration = False
        if self._kuration_timeout:
            self._kuration_timeout.cancel()
            self._kuration_timeout = None
        self._btn_kuration.background_color = (0.35, 0.35, 0.35, 1)
        self._btn_kuration.text = '✗'
        self._btn_kuration.width = 60

    def _reset_kuration_timeout(self):
        if self._kuration_timeout:
            self._kuration_timeout.cancel()
        self._kuration_timeout = Clock.schedule_once(
            lambda dt: self._deactivate_quick_kuration(), QUICK_KURATION_TIMEOUT
        )

    def _exclude_current(self):
        if not self._images:
            return
        current = self._images[self._index]
        if current.is_relative_to(BASIS_DIR):
            return
        # In excluded.txt eintragen
        folder = current.parent
        excluded = load_excluded(folder)
        excluded.add(current.name)
        save_excluded(folder, excluded)
        # Aus der laufenden Liste entfernen (alle Vorkommen, z.B. durch Grundstock-Interleave)
        self._images = [img for img in self._images if img != current]
        if not self._images:
            self._deactivate_quick_kuration()
            self._show()
            return
        self._index = self._index % len(self._images)
        self._show()
        self._reset_kuration_timeout()


# ---------------------------------------------------------------------------
# Screens: Kuration
# ---------------------------------------------------------------------------

class KurationSaisonScreen(Screen):
    """Kuration Ebene 1: Spielzeiten – führt zu Produktions-Kuration."""

    def build(self):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')

        header = make_header('Kuration: Spielzeiten', '✕ Schließen', 'spielsaison',
                              self.manager)
        root.add_widget(header)
        root.add_widget(Label(
            text='Spielzeit antippen → Produktionen und Bilder kurationieren',
            size_hint=(1, None), height=36, font_size='14sp',
            color=(0.8, 0.8, 0.4, 1),
        ))

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        saisons = sorted(
            (d for d in MEDIA_DIR.iterdir() if d.is_dir() and d.name != 'basismedien'),
            reverse=True,
        )
        for saison in saisons:
            grid.add_widget(Kachel(
                title=saison.name,
                thumb_path=make_thumbnail(saison),
                callback=lambda s=saison: self._open(s),
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _open(self, saison_path: Path):
        self.manager.get_screen('kuration_prod').load(saison_path)
        _go_to(self.manager, 'kuration_prod')


def make_header(title: str, back_label: str, back_target: str, manager,
                extra_buttons: list = None, back_label_direction: str = 'right') -> BoxLayout:
    header = BoxLayout(size_hint=(1, None), height=70)
    btn = Button(text=back_label, size_hint=(None, 1), width=180, font_size='18sp')
    btn.bind(on_press=lambda *a: _go_to(manager, back_target, back_label_direction))
    header.add_widget(btn)
    header.add_widget(Label(text=title, font_size='22sp'))
    for b in (extra_buttons or []):
        header.add_widget(b)
    return header


class KurationProdScreen(Screen):
    """Kuration Ebene 2: Produktionen ein-/ausschließen oder in Bilder gehen."""

    def load(self, saison_path: Path):
        self._saison_path = saison_path
        self._build()

    def _build(self):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')
        root.add_widget(make_header(
            f'Kuration: {self._saison_path.name}',
            '← Spielzeiten', 'kuration_saison', self.manager,
        ))
        root.add_widget(Label(
            text='"Bilder →" antippen für Einzelbild-Auswahl  |  '
                 '"✗ ausschließen" blendet ganzen Ordner aus',
            size_hint=(1, None), height=34, font_size='13sp',
            color=(0.8, 0.8, 0.4, 1),
        ))

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        for prod in sorted(p for p in self._saison_path.iterdir() if p.is_dir()):
            grid.add_widget(KurationOrdnerKachel(
                prod_path=prod,
                excluded=is_folder_excluded(prod),
                on_drill=lambda p=prod: self._drill(p),
                on_toggle=lambda p=prod: self._toggle(p),
                on_delete=lambda p=prod: self._delete_folder(p),
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _toggle(self, prod_path: Path):
        toggle_folder_excluded(prod_path)
        self._build()

    def _delete_folder(self, prod_path: Path):
        PinPopup(on_success=lambda: self._do_delete_folder(prod_path)).open()

    def _do_delete_folder(self, prod_path: Path):
        shutil.rmtree(prod_path, ignore_errors=True)
        # Thumbnail löschen
        thumb = THUMB_DIR / f"{prod_path.parent.name}__{prod_path.name}.jpg"
        thumb.unlink(missing_ok=True)
        self._build()

    def _drill(self, prod_path: Path):
        if has_subfolders(prod_path):
            self.manager.get_screen('kuration_subordner').load(
                prod_path, back_screen='kuration_prod'
            )
            _go_to(self.manager, 'kuration_subordner')
        else:
            self.manager.get_screen('kuration_bilder').load(prod_path)
            _go_to(self.manager, 'kuration_bilder')


class KurationBilderScreen(Screen):
    """Kuration Ebene 3: Einzelne Bilder einer Produktion ein-/ausschließen oder löschen."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._prod_path = None
        self._excluded = set()
        self._initial_excluded = set()   # Zustand beim Öffnen – für ML-Log
        self._to_delete = set()
        self._scores = {}
        self._show_flagged_only = False

    def load(self, prod_path: Path):
        self._prod_path = prod_path
        self._excluded = load_excluded(prod_path) - {'*'}
        self._initial_excluded = set(self._excluded)  # Snapshot für Log
        self._to_delete = set()
        self._scores = load_quality_scores(prod_path)
        self._show_flagged_only = False
        self._build()

    def _build(self):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')

        btn_save = Button(text='💾 Speichern', size_hint=(None, 1), width=180, font_size='18sp',
                          background_color=(0.2, 0.55, 0.2, 1))
        btn_save.bind(on_press=self._save)
        root.add_widget(make_header(
            self._prod_path.name,
            '← Produktionen', 'kuration_prod', self.manager,
            extra_buttons=[btn_save],
        ))

        all_images = get_all_image_files(self._prod_path)
        n_flagged = sum(1 for f in all_images if self._scores.get(f.name, {}).get('flagged'))
        n_del  = len(self._to_delete)
        n_excl = len(self._excluded - self._to_delete)

        bar = BoxLayout(size_hint=(1, None), height=48, spacing=8, padding=(GRID_PADDING, 4))
        bar.add_widget(Label(
            text=f'{len(all_images) - n_excl - n_del} ein  '
                 f'· {n_excl} aus  · {n_del} löschen'
                 + (f'  · ⚠ {n_flagged}' if n_flagged else ''),
            font_size='13sp', size_hint=(1, 1),
        ))
        btn_alle  = Button(text='Alle ein', size_hint=(None, 1), width=110, font_size='12sp')
        btn_keine = Button(text='Alle aus', size_hint=(None, 1), width=110, font_size='12sp')
        btn_alle.bind(on_press=lambda *a: self._bulk(include=True))
        btn_keine.bind(on_press=lambda *a: self._bulk(include=False))
        bar.add_widget(btn_alle)
        bar.add_widget(btn_keine)

        if n_flagged:
            lbl = '⚠ Nur markierte' if not self._show_flagged_only else '⚠ Alle zeigen'
            btn_flag = Button(text=lbl, size_hint=(None, 1), width=170, font_size='12sp',
                              background_color=(0.6, 0.5, 0.1, 1))
            btn_flag.bind(on_press=self._toggle_flagged_filter)
            bar.add_widget(btn_flag)

        root.add_widget(bar)

        scroll = ScrollView()
        grid = make_grid(KURATION_COLS)

        display_images = all_images
        if self._show_flagged_only:
            display_images = [f for f in all_images
                              if self._scores.get(f.name, {}).get('flagged')]

        for img_file in display_images:
            grid.add_widget(BildToggle(
                img_path=img_file,
                excluded=img_file.name in self._excluded,
                marked_delete=img_file.name in self._to_delete,
                quality_info=self._scores.get(img_file.name),
                on_toggle=lambda state, f=img_file: self._on_toggle(f, state),
                on_delete_toggle=lambda state, f=img_file: self._on_delete(f, state),
            ))
        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _on_toggle(self, img_file: Path, excluded: bool):
        if excluded:
            self._excluded.add(img_file.name)
        else:
            self._excluded.discard(img_file.name)

    def _on_delete(self, img_file: Path, marked: bool):
        if marked:
            self._to_delete.add(img_file.name)
        else:
            self._to_delete.discard(img_file.name)

    def _toggle_flagged_filter(self, *args):
        self._show_flagged_only = not self._show_flagged_only
        self._build()

    def _bulk(self, include: bool):
        if include:
            self._excluded.clear()
        else:
            self._excluded = {f.name for f in get_all_image_files(self._prod_path)}
        self._build()

    def _save(self, *args):
        all_images = get_all_image_files(self._prod_path)

        # Kurationsentscheidungen für ML-Log aufzeichnen
        log_entries = []
        hostname = socket.gethostname()
        timestamp = datetime.now().isoformat(timespec='seconds')

        # Pfad relativ zu MEDIA_DIR für spätere Auswertung
        try:
            rel_path = self._prod_path.relative_to(MEDIA_DIR)
            parts = rel_path.parts
            saison     = parts[0] if len(parts) > 0 else ''
            produktion = parts[1] if len(parts) > 1 else ''
            unterordner = '/'.join(parts[2:]) if len(parts) > 2 else ''
        except ValueError:
            saison = produktion = unterordner = ''

        for img_file in all_images:
            name = img_file.name
            war_ausgeschlossen = name in self._initial_excluded
            ist_ausgeschlossen = name in self._excluded
            wird_geloescht     = name in self._to_delete

            if wird_geloescht:
                aktion = 'geloescht'
            elif not war_ausgeschlossen and ist_ausgeschlossen:
                aktion = 'ausgeschlossen'
            elif war_ausgeschlossen and not ist_ausgeschlossen:
                aktion = 'wiederhergestellt'
            else:
                aktion = 'unveraendert'

            q = self._scores.get(name, {})
            log_entries.append({
                'timestamp':          timestamp,
                'pi':                 hostname,
                'aktion':             aktion,
                'dateiname':          name,
                'saison':             saison,
                'produktion':         produktion,
                'unterordner':        unterordner,
                'war_ausgeschlossen': war_ausgeschlossen,
                # Qualitätsmerkmale (Features für ML)
                'sharpness':          q.get('sharpness', -1),
                'noise':              q.get('noise', -1),
                'brightness':         q.get('brightness', -1),
                'auto_flagged':       q.get('flagged', False),
                'auto_reason':        q.get('reason', ''),
            })

        _log_kuration(log_entries)

        # Dateien löschen
        for name in self._to_delete:
            f = self._prod_path / name
            if f.exists():
                f.unlink()
            thumb = THUMB_DIR / f"img__{self._prod_path.parent.name}__{self._prod_path.name}__{f.stem}.jpg"
            thumb.unlink(missing_ok=True)

        # Ausgeschlossene speichern
        remaining_excluded = self._excluded - self._to_delete
        save_excluded(self._prod_path, remaining_excluded)

        # Ordner-Thumbnail neu generieren
        (THUMB_DIR / f"{self._prod_path.parent.name}__{self._prod_path.name}.jpg").unlink(missing_ok=True)

        _go_to(self.manager, 'kuration_prod', 'right')


class KurationSubordnerScreen(Screen):
    """Kuration: Unterordner einer Produktion – ein-/ausschließen oder in Bilder gehen."""

    def load(self, folder: Path, back_screen: str = 'kuration_prod'):
        self._folder = folder
        self._back_screen = back_screen
        self._build()

    def _build(self):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')
        root.add_widget(make_header(
            f'Kuration: {self._folder.name}',
            '← Zurück', self._back_screen, self.manager,
        ))
        root.add_widget(Label(
            text='Unterordner: "Bilder →" kurationieren  |  "✗" ganzen Unterordner ausschließen',
            size_hint=(1, None), height=34, font_size='13sp',
            color=(0.8, 0.8, 0.4, 1),
        ))

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        for sub in sorted(f for f in self._folder.iterdir() if f.is_dir()):
            grid.add_widget(KurationOrdnerKachel(
                prod_path=sub,
                excluded=is_folder_excluded(sub),
                on_drill=lambda s=sub: self._drill(s),
                on_toggle=lambda s=sub: self._toggle(s),
                on_delete=lambda s=sub: self._delete(s),
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _toggle(self, sub: Path):
        toggle_folder_excluded(sub)
        self._build()

    def _drill(self, sub: Path):
        if has_subfolders(sub):
            # Noch eine Ebene tiefer (rekursiv selber Screen)
            self.manager.get_screen('kuration_subordner').load(
                sub, back_screen='kuration_subordner'
            )
        else:
            self.manager.get_screen('kuration_bilder').load(sub)
            _go_to(self.manager, 'kuration_bilder')

    def _delete(self, sub: Path):
        PinPopup(on_success=lambda: self._do_delete(sub)).open()

    def _do_delete(self, sub: Path):
        shutil.rmtree(sub, ignore_errors=True)
        thumb = THUMB_DIR / f"{sub.parent.name}__{sub.name}.jpg"
        thumb.unlink(missing_ok=True)
        self._build()


# ---------------------------------------------------------------------------
# Autostart-Screen
# ---------------------------------------------------------------------------

class AutostartScreen(Screen):
    """PIN-geschützter Screen zum Konfigurieren des automatischen Show-Starts."""

    # _mode: None | 'genre' | 'folder'
    # _genre_prefix: aktueller Pfad im Genre-Baum
    # _saison_path: ausgewählte Saison im Folder-Modus

    def open(self):
        """Einstieg: PIN-Abfrage, dann Screen anzeigen."""
        def _show():
            self._mode = None
            self._genre_prefix = ''
            self._saison_path = None
            self._build()
            _go_to(self.manager, 'autostart')
        PinPopup(on_success=_show).open()

    def _build(self):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')

        if self._mode is None:
            root.add_widget(self._build_main())
        elif self._mode == 'genre':
            root.add_widget(self._build_genre())
        elif self._mode == 'folder':
            root.add_widget(self._build_folder())

        self.add_widget(root)

    # --- Hauptansicht ---

    def _build_main(self) -> BoxLayout:
        root = BoxLayout(orientation='vertical')
        root.add_widget(make_header(
            'Autostart konfigurieren', '✕ Schließen', 'spielsaison', self.manager,
        ))

        current = load_autostart()
        root.add_widget(Label(
            text=f'Aktuell: {autostart_label(current)}',
            font_size='18sp', size_hint=(1, None), height=50,
            color=(0.6, 0.9, 0.6, 1) if current else (0.6, 0.6, 0.6, 1),
        ))

        pad = BoxLayout(orientation='vertical', padding=30, spacing=20)

        btn_none = Button(
            text='✕  Kein Autostart (normaler Startbildschirm)',
            font_size='18sp', size_hint=(1, None), height=70,
            background_color=(0.35, 0.35, 0.35, 1),
        )
        btn_none.bind(on_press=lambda *a: self._set(None))

        btn_genre = Button(
            text='🎭  Genre auswählen …',
            font_size='18sp', size_hint=(1, None), height=70,
            background_color=(0.2, 0.45, 0.65, 1),
        )
        btn_genre.bind(on_press=lambda *a: self._enter_genre())

        btn_folder = Button(
            text='📁  Produktion auswählen …',
            font_size='18sp', size_hint=(1, None), height=70,
            background_color=(0.3, 0.5, 0.3, 1),
        )
        btn_folder.bind(on_press=lambda *a: self._enter_folder())

        pad.add_widget(btn_none)
        pad.add_widget(btn_genre)
        pad.add_widget(btn_folder)
        root.add_widget(pad)
        return root

    def _set(self, config: dict | None):
        save_autostart(config)
        self._mode = None
        self._build()

    # --- Genre-Modus ---

    def _enter_genre(self):
        self._mode = 'genre'
        self._genre_prefix = ''
        self._build()

    def _build_genre(self) -> BoxLayout:
        root = BoxLayout(orientation='vertical')
        index = build_genre_index()

        header = BoxLayout(size_hint=(1, None), height=70)
        btn_back = Button(text='← Zurück', size_hint=(None, 1), width=160, font_size='18sp')
        btn_back.bind(on_press=self._genre_back)
        header.add_widget(btn_back)
        titel = f'Genre: {self._genre_prefix}' if self._genre_prefix else 'Genre wählen'
        header.add_widget(Label(text=titel, font_size='22sp'))
        root.add_widget(header)

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        # "Diese Kategorie" – auf jeder Ebene mit Treffern wählbar
        bilder_hier = bilder_fuer_genre(index, self._genre_prefix) if self._genre_prefix else []
        if bilder_hier:
            btn_this = Button(
                text=f'▶ Diese Kategorie ({len(bilder_hier)} Bilder)',
                font_size='16sp', size_hint=(1, None), height=60,
                background_color=(0.2, 0.6, 0.25, 1),
            )
            btn_this.bind(on_press=lambda *a: self._set(
                {'type': 'genre', 'path': self._genre_prefix}
            ))
            grid.add_widget(btn_this)

        for kindname, vollpfad in genre_kinder(index, self._genre_prefix):
            bilder = bilder_fuer_genre(index, vollpfad)
            unterkinder = genre_kinder(index, vollpfad)
            thumb = make_image_thumbnail(bilder[0]) if bilder else ''
            if unterkinder:
                label = f'{kindname}  ▸ ({len(bilder)})'
                cb = lambda v=vollpfad: self._genre_drill(v)
            else:
                label = f'{kindname} ({len(bilder)})'
                cb = lambda v=vollpfad: self._set({'type': 'genre', 'path': v})
            grid.add_widget(Kachel(title=label, thumb_path=thumb, callback=cb))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        return root

    def _genre_drill(self, vollpfad: str):
        self._genre_prefix = vollpfad
        self._build()

    def _genre_back(self, *args):
        if not self._genre_prefix:
            self._mode = None
        else:
            self._genre_prefix = '/'.join(self._genre_prefix.split('/')[:-1])
        self._build()

    # --- Folder-Modus ---

    def _enter_folder(self):
        self._mode = 'folder'
        self._saison_path = None
        self._build()

    def _build_folder(self) -> BoxLayout:
        root = BoxLayout(orientation='vertical')

        btn_back = Button(text='← Zurück', size_hint=(None, 1), width=160, font_size='18sp')
        if self._saison_path is None:
            btn_back.bind(on_press=lambda *a: self._folder_back_to_main())
            titel = 'Spielzeit wählen'
        else:
            btn_back.bind(on_press=lambda *a: self._folder_back_to_saison())
            titel = f'Produktion: {self._saison_path.name}'

        header = BoxLayout(size_hint=(1, None), height=70)
        header.add_widget(btn_back)
        header.add_widget(Label(text=titel, font_size='22sp'))
        root.add_widget(header)

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        if self._saison_path is None:
            saisons = sorted(
                (d for d in MEDIA_DIR.iterdir() if d.is_dir() and d.name != 'basismedien'),
                reverse=True,
            )
            for saison in saisons:
                grid.add_widget(Kachel(
                    title=saison.name,
                    thumb_path=make_thumbnail(saison),
                    callback=lambda s=saison: self._folder_drill(s),
                ))
        else:
            for prod in sorted(p for p in self._saison_path.iterdir() if p.is_dir()):
                grid.add_widget(Kachel(
                    title=prod.name,
                    thumb_path=make_thumbnail(prod),
                    callback=lambda p=prod: self._set({
                        'type': 'folder',
                        'saison': self._saison_path.name,
                        'produktion': p.name,
                    }),
                ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        return root

    def _folder_drill(self, saison: Path):
        self._saison_path = saison
        self._build()

    def _folder_back_to_saison(self):
        self._saison_path = None
        self._build()

    def _folder_back_to_main(self):
        self._mode = None
        self._build()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class MediaplayerApp(App):

    def build(self):
        Window.fullscreen = 'auto'
        sm = ScreenManager()
        sm.add_widget(SpielsaisonScreen(name='spielsaison'))
        sm.add_widget(ProduktionenScreen(name='produktionen'))
        sm.add_widget(SlideshowScreen(name='slideshow'))
        sm.add_widget(GenreScreen(name='genre'))
        sm.add_widget(KurationSaisonScreen(name='kuration_saison'))
        sm.add_widget(KurationProdScreen(name='kuration_prod'))
        sm.add_widget(KurationSubordnerScreen(name='kuration_subordner'))
        sm.add_widget(KurationBilderScreen(name='kuration_bilder'))
        sm.add_widget(AutostartScreen(name='autostart'))
        return sm

    def on_start(self):
        self._sync_label = None
        threading.Thread(target=usb_monitor_loop,       args=(self,), daemon=True).start()
        threading.Thread(target=syncthing_monitor_loop, args=(self,), daemon=True).start()
        Clock.schedule_once(self._try_autostart, 0.5)

    def _try_autostart(self, dt):
        config = load_autostart()
        if config is None:
            return
        sm = self.root
        slideshow = sm.get_screen('slideshow')
        if config.get('type') == 'genre':
            index = build_genre_index()
            bilder = bilder_fuer_genre(index, config['path'])
            if bilder:
                slideshow.load_images(bilder, f'Genre: {config["path"]}', back_target='spielsaison')
                _go_to(sm, 'slideshow')
        elif config.get('type') == 'folder':
            prod_path = MEDIA_DIR / config['saison'] / config['produktion']
            if prod_path.is_dir():
                slideshow.load(prod_path)
                _go_to(sm, 'slideshow')

    def set_sync_status(self, text: str):
        """Setzt den Sync-Status-Text im Header (wird vom Monitor-Thread via Clock aufgerufen)."""
        if self._sync_label is not None:
            self._sync_label.text = text

    def refresh_ui(self):
        """Wird vom USB-Monitor- oder Syncthing-Monitor-Thread via Clock aufgerufen."""
        sm = self.root
        sm.get_screen('spielsaison')._build()
        if sm.current not in ('spielsaison',):
            _go_to(sm, 'spielsaison', 'right')


if __name__ == '__main__':
    MediaplayerApp().run()
