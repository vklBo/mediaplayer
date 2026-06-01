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
#   medienbasis/     → Bilder, die immer eingeblendet werden (flach)
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
import subprocess
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
from kivy.uix.label import Label                                            # noqa: E402
from kivy.clock import Clock                                                # noqa: E402
from kivy.core.window import Window                                         # noqa: E402

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

MEDIA_DIR     = Path.home() / 'media'
BASIS_DIR     = Path.home() / 'medienbasis'
THUMB_DIR     = Path.home() / '.thumbs'
USB_BASE_PATH = Path('/media/taf')

SLIDESHOW_INTERVAL = 5       # Sekunden pro Bild
THUMB_SIZE         = (500, 340)
KURATION_THUMB_SIZE = (200, 150)
TILE_COLS          = 4
GRID_PADDING       = 20
GRID_SPACING       = 16
KURATION_COLS      = 6       # Spalten im Bild-Kurationsmodus

IMAGE_EXTS   = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}
ALLOWED_EXTS = IMAGE_EXTS | {'.mp4', '.avi', '.mkv', '.mov'}
EXCLUDED_FILE = 'excluded.txt'

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


def _copy_flat(src: Path, dst: Path):
    for item in src.rglob('*'):
        if item.is_file() and item.suffix.lower() in ALLOWED_EXTS:
            shutil.copy2(item, dst / item.name)


def _copy_structured(src: Path, dst: Path):
    for saison in src.iterdir():
        if saison.name in ('medienbasis', 'skripte') or not saison.is_dir():
            continue
        target_saison = dst / saison.name
        target_saison.mkdir(exist_ok=True)
        for produktion in saison.iterdir():
            if produktion.is_dir():
                target_prod = target_saison / produktion.name
                target_prod.mkdir(exist_ok=True)
                _copy_flat(produktion, target_prod)


def usb_monitor_loop(app: 'MediaplayerApp'):
    while True:
        time.sleep(5)
        usb_path = _get_usb_path()
        if not usb_path:
            continue
        print(f'USB gefunden: {usb_path}')
        basis_usb = usb_path / 'medienbasis'
        if basis_usb.exists():
            _clear_dir(BASIS_DIR)
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

    def __init__(self, prod_path: Path, excluded: bool, on_drill, on_toggle, **kwargs):
        super().__init__(orientation='vertical', padding=8, spacing=4, **kwargs)
        w, h = tile_size()
        self.size_hint = (None, None)
        self.size = (w, h)
        img_h = h - 100

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
            size_hint=(1, None), height=36,
            halign='center', text_size=(w - 16, None),
            color=(0.5, 0.5, 0.5, 1) if excluded else (1, 1, 1, 1),
        ))

        controls = BoxLayout(size_hint=(1, None), height=48, spacing=8)

        btn_drill = Button(text='Bilder →', size_hint=(1, 1), font_size='14sp')
        btn_drill.bind(on_press=lambda *a: on_drill())

        btn_toggle = Button(
            text='✓ einschließen' if excluded else '✗ ausschließen',
            size_hint=(None, 1), width=170, font_size='14sp',
            background_color=(0.2, 0.7, 0.2, 1) if excluded else (0.7, 0.2, 0.2, 1),
        )
        btn_toggle.bind(on_press=lambda *a: on_toggle())

        controls.add_widget(btn_drill)
        controls.add_widget(btn_toggle)
        self.add_widget(controls)


class BildToggle(BoxLayout):
    """Einzelbild-Thumbnail mit Ein-/Ausschluss-Toggle im Kurationsmodus."""

    def __init__(self, img_path: Path, excluded: bool, on_toggle, **kwargs):
        super().__init__(orientation='vertical', padding=2, spacing=2, **kwargs)
        w, h = kuration_thumb_size()
        self.size_hint = (None, None)
        self.size = (w, h + 28)
        self._excluded = excluded
        self._on_toggle = on_toggle

        thumb = make_image_thumbnail(img_path)
        self._img = KivyImage(
            source=thumb if thumb else '',
            size_hint=(1, None), height=h,
            allow_stretch=True, keep_ratio=True,
            opacity=0.25 if excluded else 1.0,
        )
        self.add_widget(self._img)

        self._btn = Button(
            text='✗ aus' if excluded else '✓ ein',
            size_hint=(1, None), height=26, font_size='12sp',
            background_color=(0.55, 0.55, 0.55, 1) if excluded else (0.2, 0.65, 0.2, 1),
        )
        self._btn.bind(on_press=self._toggle)
        self.add_widget(self._btn)

    def _toggle(self, *args):
        self._excluded = not self._excluded
        self._img.opacity = 0.25 if self._excluded else 1.0
        self._btn.text = '✗ aus' if self._excluded else '✓ ein'
        self._btn.background_color = (
            (0.55, 0.55, 0.55, 1) if self._excluded else (0.2, 0.65, 0.2, 1)
        )
        self._on_toggle(self._excluded)


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
        btn_kuration = Button(text='✏ Kuration', size_hint=(None, 1), width=160, font_size='18sp')
        btn_kuration.bind(on_press=self._enter_kuration)
        header.add_widget(btn_kuration)
        root.add_widget(header)

        scroll = ScrollView()
        grid = make_grid(TILE_COLS)

        saisons = sorted(
            (d for d in MEDIA_DIR.iterdir() if d.is_dir() and d.name != 'medienbasis'),
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
        self.manager.get_screen('kuration_saison').build()
        _go_to(self.manager, 'kuration_saison')


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


class SlideshowScreen(Screen):
    """Ebene 3: Bilder einer Produktion als Diashow."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._images = []
        self._index = 0
        self._timer = None
        self._prod_name = ''

        root = BoxLayout(orientation='vertical')
        self._img = KivyImage(allow_stretch=True, keep_ratio=True)
        root.add_widget(self._img)

        bar = BoxLayout(size_hint=(1, None), height=60)
        btn_back = Button(text='← Zurück', size_hint=(None, 1), width=160, font_size='18sp')
        btn_back.bind(on_press=lambda *a: _go_to(self.manager, 'produktionen', 'right'))
        btn_prev = Button(text='‹', size_hint=(None, 1), width=80, font_size='24sp')
        btn_next = Button(text='›', size_hint=(None, 1), width=80, font_size='24sp')
        btn_prev.bind(on_press=self._prev)
        btn_next.bind(on_press=self._next)
        self._info = Label(font_size='16sp')
        bar.add_widget(btn_back)
        bar.add_widget(btn_prev)
        bar.add_widget(self._info)
        bar.add_widget(btn_next)
        root.add_widget(bar)
        self.add_widget(root)

    def load(self, prod_path: Path):
        self._prod_name = prod_path.name
        self._images = get_image_files(prod_path)
        self._index = 0
        self._show()

    def on_enter(self, *args):
        self._timer = Clock.schedule_interval(self._advance, SLIDESHOW_INTERVAL)

    def on_leave(self, *args):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _show(self):
        if not self._images:
            self._info.text = 'Keine Bilder vorhanden.'
            return
        self._img.source = str(self._images[self._index])
        self._info.text = f'{self._prod_name}  –  {self._index + 1} / {len(self._images)}'

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
            (d for d in MEDIA_DIR.iterdir() if d.is_dir() and d.name != 'medienbasis'),
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
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _toggle(self, prod_path: Path):
        toggle_folder_excluded(prod_path)
        self._build()

    def _drill(self, prod_path: Path):
        self.manager.get_screen('kuration_bilder').load(prod_path)
        _go_to(self.manager, 'kuration_bilder')


class KurationBilderScreen(Screen):
    """Kuration Ebene 3: Einzelne Bilder einer Produktion ein-/ausschließen."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._prod_path = None
        self._excluded = set()

    def load(self, prod_path: Path):
        self._prod_path = prod_path
        # Laden ohne '*'-Eintrag (der gilt für ganzen Ordner, nicht Einzelbilder)
        self._excluded = load_excluded(prod_path) - {'*'}
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

        # Bulk-Aktionen
        bar = BoxLayout(size_hint=(1, None), height=48, spacing=12, padding=(GRID_PADDING, 4))
        all_images = get_all_image_files(self._prod_path)
        n_excl = len(self._excluded)
        bar.add_widget(Label(
            text=f'{len(all_images) - n_excl} von {len(all_images)} eingeschlossen',
            font_size='15sp', size_hint=(1, 1),
        ))
        btn_alle = Button(text='Alle einschließen', size_hint=(None, 1), width=200, font_size='14sp')
        btn_keine = Button(text='Alle ausschließen', size_hint=(None, 1), width=200, font_size='14sp')
        btn_alle.bind(on_press=lambda *a: self._bulk(include=True))
        btn_keine.bind(on_press=lambda *a: self._bulk(include=False))
        bar.add_widget(btn_alle)
        bar.add_widget(btn_keine)
        root.add_widget(bar)

        scroll = ScrollView()
        grid = make_grid(KURATION_COLS)

        for img_file in all_images:
            excl = img_file.name in self._excluded
            grid.add_widget(BildToggle(
                img_path=img_file,
                excluded=excl,
                on_toggle=lambda state, f=img_file: self._on_toggle(f, state),
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _on_toggle(self, img_file: Path, excluded: bool):
        if excluded:
            self._excluded.add(img_file.name)
        else:
            self._excluded.discard(img_file.name)

    def _bulk(self, include: bool):
        if include:
            self._excluded.clear()
        else:
            self._excluded = {f.name for f in get_all_image_files(self._prod_path)}
        self._build()

    def _save(self, *args):
        save_excluded(self._prod_path, self._excluded)
        _go_to(self.manager, 'kuration_prod', 'right')


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
        sm.add_widget(KurationSaisonScreen(name='kuration_saison'))
        sm.add_widget(KurationProdScreen(name='kuration_prod'))
        sm.add_widget(KurationBilderScreen(name='kuration_bilder'))
        return sm

    def on_start(self):
        t = threading.Thread(target=usb_monitor_loop, args=(self,), daemon=True)
        t.start()

    def refresh_ui(self):
        """Wird vom USB-Monitor-Thread via Clock aufgerufen."""
        sm = self.root
        sm.get_screen('spielsaison')._build()
        if sm.current not in ('spielsaison',):
            _go_to(sm, 'spielsaison', 'right')


if __name__ == '__main__':
    MediaplayerApp().run()
