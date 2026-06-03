#!/usr/bin/env python3
# Synchronisiert Bilder von OneDrive/SharePoint nach /srv/media (Server).
#
# Pipeline pro Produktionsordner:
#   1. rclone sync → ~/.media_sync_tmp/
#   2. Hochformat-Filter (Höhe > Breite → überspringen)
#   3. Resize auf max. 1920×1080, EXIF entfernen, JPEG optimieren
#   4. Qualitätsanalyse (Schärfe + Helligkeit, theaterangepasst)
#   5. Duplikat-Erkennung (perceptual hash)
#   6. quality_scores.json in Produktionsordner schreiben
#   7. excluded.txt aus vorherigem Sync erhalten
#
# Aufruf:
#   python3 sync_onedrive.py --list-folders   # Ordnerstruktur → excluded_folders.txt
#   python3 sync_onedrive.py --dry-run        # Vorschau
#   python3 sync_onedrive.py                  # Sync + Verarbeitung
#   python3 sync_onedrive.py --push-excluded  # excluded.txt zurück nach OneDrive
#   python3 sync_onedrive.py --no-sync        # Nur lokale Verarbeitung

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image as PILImage

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

RCLONE_REMOTE  = 'onedrive'          # rclone-Konfigurationsname
RCLONE_PATH    = 'Theater/Fotos'     # Pfad in OneDrive/SharePoint
MEDIA_DIR      = Path('/srv/media')  # Ziel (wird per Syncthing verteilt)
STAGING_DIR    = Path('/srv/media_staging')  # Zwischenstufe – wird erst bei Erfolg umgeschaltet
TMP_DIR        = Path.home() / '.media_sync_tmp'

EXCLUDED_FOLDERS_FILE = Path(__file__).parent / 'excluded_folders.txt'

# Lock-Datei: existiert solange der Sync läuft (wird vom Watchdog geprüft)
LOCK_FILE = Path('/tmp/taf_sync_running')

# Bildoptimierung
TARGET_SIZE   = (1920, 1080)
JPEG_QUALITY  = 88               # 0–95, 88 = guter Kompromiss Qualität/Größe

# Qualitätsanalyse (theaterangepasst)
# Schärfe wird nur auf hellen Bildbereichen gemessen (Bühne = beleuchtet).
# Rauschen wird nur in dunklen Bereichen gemessen (Hintergrund/Schatten).
BRIGHT_PIXEL_MIN   = 60          # Mindestwert (0–255) für "hell" (Bühne)
DARK_PIXEL_MAX     = 80          # Maximalwert (0–255) für "dunkel" (Hintergrund)
DARK_IMAGE_RATIO   = 0.05        # < 5% helle Pixel → Bild zu dunkel
SHARPNESS_LOW      = 35          # Laplacian-Varianz < 35 → unscharf (flaggen)
NOISE_THRESHOLD    = 9.0         # Immerkaer-Sigma > 9 in Schattenbereichen → verrauscht
MIN_DARK_PIXELS    = 2000        # Mindestanzahl dunkler Pixel für Rauschanalyse
DUPLICATE_HAMMING  = 8           # Hamming-Distanz für Duplikat-Erkennung

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}

# ---------------------------------------------------------------------------
# Hilfsfunktionen: excluded_folders.txt
# ---------------------------------------------------------------------------

def load_excluded_folders() -> set:
    if not EXCLUDED_FOLDERS_FILE.exists():
        return set()
    return {
        line.strip().strip('/')
        for line in EXCLUDED_FOLDERS_FILE.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.strip().startswith('#')
    }


def is_excluded_folder(saison: str, produktion: str, excluded: set) -> bool:
    return f'{saison}/{produktion}' in excluded or saison in excluded

# ---------------------------------------------------------------------------
# Bildoptimierung
# ---------------------------------------------------------------------------

def is_portrait(path: Path) -> bool:
    try:
        with PILImage.open(path) as img:
            w, h = img.size
            return h > w
    except Exception:
        return False


def optimize_image(src: Path, dst: Path) -> int:
    """Resize auf TARGET_SIZE, EXIF entfernen, als JPEG speichern.
    Gibt die Dateigröße in Bytes zurück."""
    with PILImage.open(src) as img:
        img = img.convert('RGB')
        img.thumbnail(TARGET_SIZE, PILImage.LANCZOS)
        img.save(dst, 'JPEG', quality=JPEG_QUALITY, optimize=True)
    return dst.stat().st_size

# ---------------------------------------------------------------------------
# Qualitätsanalyse (theaterangepasst)
# ---------------------------------------------------------------------------

def _estimate_noise_dark(gray) -> float:
    """
    Schätzt Rauschpegel in dunklen Bildbereichen (Immerkaer-Methode).
    Dunkle Bereiche = Hintergrund/Zuschauerraum bei Theaterfotos.
    Gibt Noise-Sigma zurück (höher = mehr Rauschen).
    """
    import numpy as np
    dark_mask = gray < DARK_PIXEL_MAX
    n_dark = int(dark_mask.sum())
    if n_dark < MIN_DARK_PIXELS:
        return 0.0

    import cv2
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)
    conv = cv2.filter2D(gray.astype(np.float64), -1, kernel)
    noise = float(np.sum(np.abs(conv[dark_mask])) * (0.5 * 3.14159) ** 0.5 / (6 * n_dark))
    return round(noise, 2)


def analyze_quality(path: Path) -> dict:
    """
    Bewertet Schärfe, Helligkeit und Rauschen eines Theaterfotos.

    Theaterangepasst:
    - Schärfe: nur auf hellen Bildbereichen (beleuchtete Bühne)
    - Rauschen: nur in dunklen Bereichen (Hintergrund, Schatten, hohe ISO)
    - Konservativer Schwellwert für Unschärfe (Bewegung von Darstellern kann gewollt sein)

    Gibt dict zurück: sharpness, brightness, noise, flagged, reason
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {'sharpness': -1, 'brightness': -1, 'noise': -1,
                'flagged': False, 'reason': 'opencv nicht installiert'}

    try:
        img = cv2.imread(str(path))
        if img is None:
            return {'sharpness': 0, 'brightness': 0, 'noise': 0,
                    'flagged': True, 'reason': 'Lesefehler'}

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        bright_mask = gray > BRIGHT_PIXEL_MIN
        bright_ratio = float(bright_mask.sum()) / gray.size

        # Zu dunkles Bild (kaum Bühnenlicht sichtbar)
        if bright_ratio < DARK_IMAGE_RATIO:
            return {
                'sharpness': 0.0,
                'brightness': int(gray.mean()),
                'noise': round(_estimate_noise_dark(gray), 2),
                'flagged': True,
                'reason': 'Zu dunkel',
            }

        # Schärfe auf hellen (Bühnen-)Bereichen messen
        laplacian  = cv2.Laplacian(gray, cv2.CV_64F)
        sharpness  = float(laplacian[bright_mask].var())
        brightness = int(gray[bright_mask].mean())

        # Rauschen in dunklen (Hintergrund-)Bereichen messen
        noise = _estimate_noise_dark(gray)

        # Flagging-Logik – mehrere Gründe möglich
        reasons = []
        if sharpness < SHARPNESS_LOW:
            reasons.append('Unscharf')
        if noise > NOISE_THRESHOLD:
            reasons.append('Verrauscht')

        return {
            'sharpness': round(sharpness, 1),
            'brightness': brightness,
            'noise': noise,
            'flagged': bool(reasons),
            'reason': ' + '.join(reasons),
        }
    except Exception as e:
        return {'sharpness': -1, 'brightness': -1, 'noise': -1,
                'flagged': False, 'reason': str(e)}

# ---------------------------------------------------------------------------
# Duplikat-Erkennung
# ---------------------------------------------------------------------------

def compute_phash(path: Path) -> str:
    """Perceptual Hash für Duplikat-Erkennung. Leer wenn imagehash fehlt."""
    try:
        import imagehash
        with PILImage.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return ''


def hamming(h1: str, h2: str) -> int:
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count('1')
    except Exception:
        return 999


def detect_duplicates(images: list) -> dict:
    """Gibt dict zurück: Dateiname → Name des Originals (wenn Duplikat)."""
    seen = {}     # hash → filename
    dupes = {}
    for img in images:
        h = compute_phash(img)
        if not h:
            continue
        for existing_hash, existing_name in seen.items():
            if hamming(h, existing_hash) < DUPLICATE_HAMMING:
                dupes[img.name] = existing_name
                break
        else:
            seen[h] = img.name
    return dupes

# ---------------------------------------------------------------------------
# Verarbeitung: Struktur erhalten + Optimieren + Analysieren
# ---------------------------------------------------------------------------

def _process_folder(src: Path, dst: Path, stats: dict, dry_run: bool, prev: Path = None):
    """
    Verarbeitet einen Ordner rekursiv und erhält die Unterordner-Struktur.
    Jeder Ordner bekommt seine eigene quality_scores.json.
    Bilder werden pro Ordner analysiert (keine Duplikat-Erkennung über Ordner hinweg).

    prev: entsprechender Ordner in der bestehenden Bibliothek (/srv/media),
          aus dem excluded.txt und quality_scores.json übernommen werden.
    """
    # Unterordner zuerst rekursiv verarbeiten
    for sub in sorted(f for f in src.iterdir() if f.is_dir()):
        dst_sub  = dst / sub.name
        prev_sub = (prev / sub.name) if prev else None
        if not dry_run:
            dst_sub.mkdir(parents=True, exist_ok=True)
        _process_folder(sub, dst_sub, stats, dry_run, prev_sub)

    # Bilder direkt in diesem Ordner (nicht rekursiv)
    images_all = [f for f in sorted(src.iterdir())
                  if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
    portrait   = [f for f in images_all if is_portrait(f)]
    images     = [f for f in images_all if not is_portrait(f)]
    stats['hochformat'] += len(portrait)

    if dry_run:
        if images or portrait:
            print(f'    {src.name}: {len(images)} Bilder, {len(portrait)} Hochformat ignoriert')
        return

    if not images:
        return

    # excluded.txt + quality_scores.json aus der BESTEHENDEN Bibliothek übernehmen
    # (prev zeigt auf den entsprechenden Ordner in /srv/media, nicht ins leere Staging)
    excl_path  = dst / 'excluded.txt'
    score_path = dst / 'quality_scores.json'
    prev_excl   = (prev / 'excluded.txt')        if prev else None
    prev_score  = (prev / 'quality_scores.json') if prev else None
    saved_excl   = prev_excl.read_text('utf-8')  if (prev_excl and prev_excl.exists())  else None
    saved_scores = json.loads(prev_score.read_text('utf-8')) if (prev_score and prev_score.exists()) else {}

    dupes = detect_duplicates(images)
    stats['duplikate'] += len(dupes)
    quality_scores = {}

    for img in images:
        dst_img = dst / img.name
        st      = img.stat()
        stats['original_bytes'] += st.st_size

        # Quell-Fingerabdruck zur Änderungserkennung (mtime + Größe)
        src_mtime = int(st.st_mtime)
        src_size  = st.st_size
        vorh      = saved_scores.get(img.name, {})
        prev_img  = (prev / img.name) if prev else None

        unveraendert = (
            prev_img is not None and prev_img.exists()
            and vorh.get('src_mtime') == src_mtime
            and vorh.get('src_size')  == src_size
        )

        if unveraendert:
            # Bereits optimierte Datei 1:1 übernehmen → bytegleich, Syncthing überträgt nicht
            shutil.copy2(prev_img, dst_img)
            stats['optimiert_bytes'] += dst_img.stat().st_size
            stats['unveraendert'] += 1
            q = vorh
        else:
            # Neu oder geändert → optimieren und analysieren
            try:
                stats['optimiert_bytes'] += optimize_image(img, dst_img)
            except Exception as e:
                shutil.copy2(img, dst_img)
                stats['optimiert_bytes'] += dst_img.stat().st_size
                print(f'    Optimierung fehlgeschlagen ({img.name}): {e}')
            q = analyze_quality(dst_img)
            if img.name in dupes:
                q['flagged'] = True
                q['reason'] = f'Duplikat von {dupes[img.name]}'
            q['src_mtime'] = src_mtime
            q['src_size']  = src_size
            stats['neu'] += 1

        stats['kopiert'] += 1
        quality_scores[img.name] = q
        if q.get('flagged'):
            stats['flagged'] += 1

    score_path.write_text(json.dumps(quality_scores, ensure_ascii=False, indent=2), 'utf-8')
    if saved_excl is not None:
        excl_path.write_text(saved_excl, 'utf-8')


def process(src: Path, dst: Path, dry_run: bool = False, prev_root: Path = None) -> dict:
    """Liest Saison/Produktion-Struktur aus src, schreibt nach dst.
    Unterordner unterhalb der Produktionsebene werden erhalten.

    prev_root: bestehende Bibliothek (/srv/media), aus der Kurations- und
               Qualitätsdaten übernommen werden (Staging-Konzept)."""
    excluded = load_excluded_folders()
    stats = {
        'saisons': 0, 'produktionen': 0, 'kopiert': 0,
        'hochformat': 0, 'konflikte': 0, 'uebersprungen': 0,
        'flagged': 0, 'duplikate': 0,
        'neu': 0, 'unveraendert': 0,
        'original_bytes': 0, 'optimiert_bytes': 0,
    }

    for saison_dir in sorted(src.iterdir()):
        if not saison_dir.is_dir():
            continue
        stats['saisons'] += 1

        for prod_dir in sorted(saison_dir.iterdir()):
            if not prod_dir.is_dir():
                continue

            if is_excluded_folder(saison_dir.name, prod_dir.name, excluded):
                stats['uebersprungen'] += 1
                if dry_run:
                    print(f'  SKIP  {saison_dir.name}/{prod_dir.name}')
                continue

            stats['produktionen'] += 1
            if dry_run:
                print(f'  SYNC  {saison_dir.name}/{prod_dir.name}:')

            target_prod = dst / saison_dir.name / prod_dir.name
            prev_prod = (prev_root / saison_dir.name / prod_dir.name) if prev_root else None
            if not dry_run:
                target_prod.mkdir(parents=True, exist_ok=True)

            _process_folder(prod_dir, target_prod, stats, dry_run, prev_prod)

    return stats

# ---------------------------------------------------------------------------
# rclone: Ordner auflisten → excluded_folders.txt
# ---------------------------------------------------------------------------

def list_folders_and_write_exclude():
    print(f'Lese Ordnerstruktur von {RCLONE_REMOTE}:{RCLONE_PATH} …')
    result = subprocess.run(
        ['rclone', 'lsd', '--recursive', f'{RCLONE_REMOTE}:{RCLONE_PATH}'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print('Fehler:', result.stderr, file=sys.stderr)
        print('Ist rclone konfiguriert? → rclone config', file=sys.stderr)
        sys.exit(1)

    folders = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            folders.append(parts[4].strip())

    two_level = sorted({
        '/'.join(f.split('/')[:2])
        for f in folders
        if '/' in f
    })

    if not two_level:
        print('Keine Unterordner gefunden. Stimmt RCLONE_PATH?')
        sys.exit(1)

    lines = [
        '# excluded_folders.txt',
        '# Zeile löschen = Ordner WIRD synchronisiert',
        '# Saison alleine (ohne /Produktion) schließt ganze Spielzeit aus.',
        f'# {len(two_level)} Produktionen gefunden – alle zunächst ausgeschlossen.',
        '',
    ]
    current_saison = None
    for folder in two_level:
        saison = folder.split('/')[0]
        if saison != current_saison:
            if current_saison is not None:
                lines.append('')
            lines.append(f'# --- {saison} ---')
            current_saison = saison
        lines.append(folder)

    EXCLUDED_FOLDERS_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'✓ {len(two_level)} Produktionen in {EXCLUDED_FOLDERS_FILE.name} eingetragen.')
    print('→ Zeilen der gewünschten Ordner löschen, dann sync starten.')

# ---------------------------------------------------------------------------
# rclone-Sync
# ---------------------------------------------------------------------------

def run_rclone_sync(dry_run: bool = False):
    excluded = load_excluded_folders()
    cmd = [
        'rclone', 'sync',
        f'{RCLONE_REMOTE}:{RCLONE_PATH}', str(TMP_DIR),
        '--read-only',   # Schreibzugriff auf OneDrive verweigern
        '--progress',
        '--filter', '+ *.jpg', '--filter', '+ *.jpeg',
        '--filter', '+ *.png', '--filter', '+ *.bmp',
        '--filter', '+ *.gif',
        '--filter', '+ excluded.txt',
        '--filter', '- *',
    ]
    for folder in excluded:
        cmd += ['--exclude', f'{folder}/**']
    if dry_run:
        cmd.append('--dry-run')

    print(f'rclone sync {RCLONE_REMOTE}:{RCLONE_PATH} → {TMP_DIR}')
    if excluded:
        print(f'  ({len(excluded)} Ordner werden übersprungen)')
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print('Fehler beim rclone-Sync.', file=sys.stderr)
        sys.exit(1)


def push_excluded_back():
    """Deaktiviert – rclone ist auf Lesezugriff konfiguriert (read-only)."""
    print('Hinweis: --push-excluded ist deaktiviert (rclone read-only).')
    print('excluded.txt-Dateien bleiben lokal auf dem Server.')

# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='OneDrive → /srv/media Sync + Optimierung')
    parser.add_argument('--list-folders',  action='store_true',
                        help='Ordner auflisten und excluded_folders.txt erstellen')
    parser.add_argument('--dry-run',       action='store_true',
                        help='Nur anzeigen, nicht kopieren')
    parser.add_argument('--no-sync',       action='store_true',
                        help='rclone überspringen')
    parser.add_argument('--push-excluded', action='store_true',
                        help='excluded.txt zurück nach OneDrive')
    args = parser.parse_args()

    if args.list_folders:
        list_folders_and_write_exclude()
        return

    if args.push_excluded:
        push_excluded_back()
        return

    # Lock-Datei setzen – Watchdog wartet damit auf Fertigstellung
    LOCK_FILE.touch()
    try:
        _run_sync(args)
    finally:
        LOCK_FILE.unlink(missing_ok=True)


def _swap_staging_to_media():
    """
    Ersetzt den Inhalt von MEDIA_DIR durch STAGING_DIR.
    Tauscht die Saison-Ordner einzeln per os.rename (atomar pro Ordner),
    damit der Syncthing-Ordner-Root erhalten bleibt und das Zeitfenster
    eines inkonsistenten Zustands minimal ist.
    """
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    neue_namen = {p.name for p in STAGING_DIR.iterdir()}

    # 1. Ordner, die es im Staging nicht mehr gibt, aus MEDIA_DIR entfernen
    for item in MEDIA_DIR.iterdir():
        if item.name not in neue_namen:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    # 2. Jeden Staging-Ordner an seinen Platz schieben (alten vorher weg)
    for src in STAGING_DIR.iterdir():
        ziel = MEDIA_DIR / src.name
        if ziel.exists():
            if ziel.is_dir():
                shutil.rmtree(ziel)
            else:
                ziel.unlink()
        os.replace(str(src), str(ziel))   # atomar auf demselben Dateisystem

    # 3. Staging-Reste entfernen
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)


def _run_sync(args):
    if not args.no_sync:
        TMP_DIR.mkdir(exist_ok=True)
        run_rclone_sync(dry_run=args.dry_run)

    if args.dry_run:
        print(f'\nVorschau: {TMP_DIR} → {MEDIA_DIR}')
        process(TMP_DIR, MEDIA_DIR, dry_run=True)
        return

    # --- Staging-Konzept: erst vollständig aufbauen, dann atomar umschalten ---
    # Reste eines früheren abgebrochenen Laufs entfernen
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    print(f'\nVerarbeite {TMP_DIR} → {STAGING_DIR} (Staging) …')
    # In Staging aufbauen, Kurations-/Qualitätsdaten aus bestehender Bibliothek übernehmen
    stats = process(TMP_DIR, STAGING_DIR, prev_root=MEDIA_DIR)

    # Erst JETZT (nach erfolgreichem Aufbau) die Live-Bibliothek ersetzen.
    # Bei einem Absturz oben bleibt MEDIA_DIR unangetastet und die Pis zeigen weiter an.
    print(f'Schalte Staging → {MEDIA_DIR} um …')
    _swap_staging_to_media()

    saved_mb    = (stats['original_bytes'] - stats['optimiert_bytes']) / 1_000_000
    original_mb = stats['original_bytes'] / 1_000_000
    optimiert_mb = stats['optimiert_bytes'] / 1_000_000

    print(f'\nErgebnis:')
    print(f'  {stats["saisons"]} Spielzeiten, {stats["produktionen"]} Produktionen')
    if stats['uebersprungen']:
        print(f'  {stats["uebersprungen"]} Produktionen übersprungen')
    print(f'  {stats["kopiert"]} Bilder gesamt'
          f'  ({stats["neu"]} neu/geändert, {stats["unveraendert"]} unverändert übernommen)')
    print(f'  → nur die {stats["neu"]} geänderten Bilder werden an die Pis übertragen')
    print(f'  Größe: {original_mb:.0f} MB → {optimiert_mb:.0f} MB '
          f'(−{saved_mb:.0f} MB gespart)')
    print(f'  {stats["hochformat"]} Hochformat-Bilder ignoriert')
    if stats['duplikate']:
        print(f'  {stats["duplikate"]} Duplikate gefunden (in quality_scores.json markiert)')
    if stats['flagged']:
        print(f'  {stats["flagged"]} Bilder zur Prüfung markiert (⚠ in Kuration sichtbar)')
    if stats['konflikte']:
        print(f'  {stats["konflikte"]} Namenskonflikte umbenannt')


if __name__ == '__main__':
    main()
