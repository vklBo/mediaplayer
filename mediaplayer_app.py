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

from kivy.app import App                                        # noqa: E402
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition  # noqa: E402
from kivy.uix.gridlayout import GridLayout                      # noqa: E402
from kivy.uix.scrollview import ScrollView                      # noqa: E402
from kivy.uix.boxlayout import BoxLayout                        # noqa: E402
from kivy.uix.button import Button                              # noqa: E402
from kivy.uix.image import Image as KivyImage                   # noqa: E402
from kivy.uix.label import Label                                # noqa: E402
from kivy.clock import Clock                                    # noqa: E402
from kivy.core.window import Window                             # noqa: E402

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

MEDIA_DIR = Path.home() / 'media'
BASIS_DIR = Path.home() / 'medienbasis'
THUMB_DIR = Path.home() / '.thumbs'
USB_BASE_PATH = Path('/media/taf')
SLIDESHOW_INTERVAL = 5          # Sekunden pro Bild
THUMB_SIZE = (500, 340)         # Pixel für generierte Thumbnails
TILE_COLS = 4                   # Spalten im Kachelraster
GRID_PADDING = 20               # Außenabstand des Rasters
GRID_SPACING = 16               # Abstand zwischen Kacheln


def tile_size() -> tuple:
    """Kachelgröße dynamisch aus der Fensterbreite berechnen."""
    available = Window.width - 2 * GRID_PADDING - (TILE_COLS - 1) * GRID_SPACING
    w = max(int(available / TILE_COLS), 100)
    h = int(w * 0.85)           # leicht hochkant
    return w, h

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}
ALLOWED_EXTS = IMAGE_EXTS | {'.mp4', '.avi', '.mkv', '.mov'}

for _d in (MEDIA_DIR, BASIS_DIR, THUMB_DIR):
    _d.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Thumbnail-Hilfsfunktionen
# ---------------------------------------------------------------------------

def _thumb_path(folder: Path) -> Path:
    return THUMB_DIR / f"{folder.parent.name}__{folder.name}.jpg"


def make_thumbnail(folder: Path) -> str:
    """Gibt Pfad zum Thumbnail zurück; generiert ihn bei Bedarf."""
    dest = _thumb_path(folder)
    if dest.exists():
        return str(dest)

    # Erstes Bild im Ordner (direkt oder rekursiv in Unterordnern)
    candidates = (f for f in sorted(folder.rglob('*')) if f.suffix.lower() in IMAGE_EXTS)
    for img_file in candidates:
        try:
            img = PILImage.open(img_file)
            img.thumbnail(THUMB_SIZE)
            img.convert('RGB').save(str(dest), 'JPEG')
            return str(dest)
        except Exception:
            continue
    return ''


def invalidate_thumbnails():
    for f in THUMB_DIR.iterdir():
        if f.suffix == '.jpg':
            f.unlink(missing_ok=True)


def get_image_files(folder: Path) -> list:
    return sorted(f for f in folder.rglob('*') if f.suffix.lower() in IMAGE_EXTS)


# ---------------------------------------------------------------------------
# USB-Monitor (läuft als Daemon-Thread)
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
    """Kopiert alle Mediendateien aus src (rekursiv) flach nach dst."""
    for item in src.rglob('*'):
        if item.is_file() and item.suffix.lower() in ALLOWED_EXTS:
            shutil.copy2(item, dst / item.name)


def _copy_structured(src: Path, dst: Path):
    """Kopiert Saison/Produktions-Struktur von USB nach dst."""
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
            print('Aktualisiere Medienbasis.')
            _clear_dir(BASIS_DIR)
            _copy_flat(basis_usb, BASIS_DIR)

        skripte_usb = usb_path / 'skripte'
        if skripte_usb.exists():
            print('Aktualisiere Skripte.')
            _copy_flat(skripte_usb, Path.home())

        print(f'Aktualisiere {MEDIA_DIR}.')
        _clear_dir(MEDIA_DIR)
        _copy_structured(usb_path, MEDIA_DIR)

        subprocess.run(['umount', str(usb_path)], check=False)
        print('USB ausgeworfen.')

        invalidate_thumbnails()
        Clock.schedule_once(lambda dt: app.refresh_ui())


# ---------------------------------------------------------------------------
# UI-Widgets
# ---------------------------------------------------------------------------

class Kachel(BoxLayout):
    """Vorschaukachel: Thumbnail + Beschriftung, touch-sensitiv."""

    def __init__(self, title: str, thumb_path: str, callback, **kwargs):
        super().__init__(orientation='vertical', padding=8, spacing=4, **kwargs)
        self._callback = callback

        w, h = tile_size()
        self.size_hint = (None, None)
        self.size = (w, h)
        img_h = h - 60          # Bildhöhe = Kachel minus Label-Zeile

        if thumb_path and Path(thumb_path).exists():
            self.add_widget(KivyImage(
                source=thumb_path,
                size_hint=(1, None), height=img_h,
                allow_stretch=True, keep_ratio=True,
            ))
        else:
            self.add_widget(Label(
                text='[Kein\nVorschaubild]',
                halign='center',
                size_hint=(1, None), height=img_h,
            ))

        self.add_widget(Label(
            text=title,
            font_size='20sp',
            size_hint=(1, None), height=52,
            halign='center',
            text_size=(w - 16, None),
        ))

    def on_touch_up(self, touch):
        if self.collide_point(*touch.pos):
            self._callback()
            return True
        return super().on_touch_up(touch)


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

class SpielsaisonScreen(Screen):
    """Ebene 1: Alle Spielzeiten als Kacheln."""

    def on_pre_enter(self, *args):
        self._build()

    def _build(self):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')
        root.add_widget(Label(
            text='Spielzeiten',
            size_hint=(1, None), height=70,
            font_size='30sp',
        ))

        scroll = ScrollView()
        grid = GridLayout(cols=TILE_COLS, spacing=GRID_SPACING, padding=GRID_PADDING, size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))

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
        self.manager.transition = SlideTransition(direction='left')
        self.manager.current = 'produktionen'


class ProduktionenScreen(Screen):
    """Ebene 2: Produktionen innerhalb einer Spielzeit."""

    def load(self, saison_path: Path):
        self.clear_widgets()
        root = BoxLayout(orientation='vertical')

        header = BoxLayout(size_hint=(1, None), height=70)
        btn_back = Button(text='← Zurück', size_hint=(None, 1), width=160, font_size='18sp')
        btn_back.bind(on_press=self._go_back)
        header.add_widget(btn_back)
        header.add_widget(Label(text=saison_path.name, font_size='26sp'))
        root.add_widget(header)

        scroll = ScrollView()
        grid = GridLayout(cols=TILE_COLS, spacing=GRID_SPACING, padding=GRID_PADDING, size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))

        for prod in sorted(p for p in saison_path.iterdir() if p.is_dir()):
            grid.add_widget(Kachel(
                title=prod.name,
                thumb_path=make_thumbnail(prod),
                callback=lambda p=prod: self._start_slideshow(p),
            ))

        scroll.add_widget(grid)
        root.add_widget(scroll)
        self.add_widget(root)

    def _go_back(self, *args):
        self.manager.transition = SlideTransition(direction='right')
        self.manager.current = 'spielsaison'

    def _start_slideshow(self, prod_path: Path):
        self.manager.get_screen('slideshow').load(prod_path)
        self.manager.transition = SlideTransition(direction='left')
        self.manager.current = 'slideshow'


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
        btn_back.bind(on_press=self._go_back)
        self._info_label = Label(font_size='16sp')
        btn_prev = Button(text='‹', size_hint=(None, 1), width=80, font_size='24sp')
        btn_next = Button(text='›', size_hint=(None, 1), width=80, font_size='24sp')
        btn_prev.bind(on_press=self._prev)
        btn_next.bind(on_press=self._next)

        bar.add_widget(btn_back)
        bar.add_widget(btn_prev)
        bar.add_widget(self._info_label)
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
            self._info_label.text = 'Keine Bilder gefunden.'
            return
        self._img.source = str(self._images[self._index])
        self._info_label.text = f'{self._prod_name}  –  {self._index + 1} / {len(self._images)}'

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

    def _go_back(self, *args):
        self.manager.transition = SlideTransition(direction='right')
        self.manager.current = 'produktionen'


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
        return sm

    def on_start(self):
        t = threading.Thread(target=usb_monitor_loop, args=(self,), daemon=True)
        t.start()

    def refresh_ui(self):
        """Wird vom USB-Monitor-Thread via Clock aufgerufen."""
        sm = self.root
        # Aktiven Screen neu aufbauen; Produktionen/Slideshow → zurück zur Spielzeiten-Übersicht
        sm.get_screen('spielsaison')._build()
        if sm.current != 'spielsaison':
            sm.transition = SlideTransition(direction='right')
            sm.current = 'spielsaison'


if __name__ == '__main__':
    MediaplayerApp().run()
