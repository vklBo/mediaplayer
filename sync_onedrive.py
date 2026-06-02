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
TMP_DIR        = Path.home() / '.media_sync_tmp'

EXCLUDED_FOLDERS_FILE = Path(__file__).parent / 'excluded_folders.txt'

# Bildoptimierung
TARGET_SIZE   = (1920, 1080)
JPEG_QUALITY  = 88               # 0–95, 88 = guter Kompromiss Qualität/Größe

# Qualitätsanalyse (theaterangepasst)
# Schärfe wird nur auf hellen Bildbereichen gemessen (Bühne = beleuchtet).
BRIGHT_PIXEL_MIN   = 60          # Mindestwert (0–255) für "hell"
DARK_IMAGE_RATIO   = 0.05        # < 5% helle Pixel → Bild zu dunkel
SHARPNESS_LOW      = 35          # Laplacian-Varianz < 35 → unscharf (flaggen)
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

def analyze_quality(path: Path) -> dict:
    """
    Bewertet Schärfe und Helligkeit eines Theaterfotos.

    Besonderheit Theater: Die Bühne ist beleuchtet, Hintergrund dunkel.
    Schärfe wird daher nur über helle Bildbereiche berechnet.
    Bewegungsunschärfe von Darstellern kann gewollt sein → konservativer Schwellwert.

    Gibt dict zurück: sharpness, brightness, flagged, reason
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {'sharpness': -1, 'brightness': -1, 'flagged': False,
                'reason': 'opencv nicht installiert'}

    try:
        img = cv2.imread(str(path))
        if img is None:
            return {'sharpness': 0, 'brightness': 0, 'flagged': True, 'reason': 'Lesefehler'}

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        bright_mask = gray > BRIGHT_PIXEL_MIN
        bright_ratio = float(bright_mask.sum()) / gray.size

        # Zu dunkles Bild (kaum Bühnenlicht sichtbar)
        if bright_ratio < DARK_IMAGE_RATIO:
            return {
                'sharpness': 0.0,
                'brightness': int(gray.mean()),
                'flagged': True,
                'reason': 'Zu dunkel',
            }

        # Schärfe auf hellen (Bühnen-)Bereichen messen
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        sharpness = float(laplacian[bright_mask].var())
        brightness = int(gray[bright_mask].mean())

        flagged = sharpness < SHARPNESS_LOW
        return {
            'sharpness': round(sharpness, 1),
            'brightness': brightness,
            'flagged': flagged,
            'reason': 'Möglicherweise unscharf' if flagged else '',
        }
    except Exception as e:
        return {'sharpness': -1, 'brightness': -1, 'flagged': False, 'reason': str(e)}

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
# Verarbeitung: Flatten + Optimieren + Analysieren
# ---------------------------------------------------------------------------

def collect_images(folder: Path):
    """Alle Querformat-Bilder aus folder (beliebige Tiefe)."""
    result, skipped_portrait = [], 0
    for f in sorted(folder.rglob('*')):
        if f.suffix.lower() not in IMAGE_EXTS:
            continue
        if is_portrait(f):
            skipped_portrait += 1
            continue
        result.append(f)
    return result, skipped_portrait


def process(src: Path, dst: Path, dry_run: bool = False) -> dict:
    excluded = load_excluded_folders()
    stats = {
        'saisons': 0, 'produktionen': 0, 'kopiert': 0,
        'hochformat': 0, 'konflikte': 0, 'uebersprungen': 0,
        'flagged': 0, 'duplikate': 0,
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
            images, portrait_count = collect_images(prod_dir)
            stats['hochformat'] += portrait_count

            if dry_run:
                print(f'  SYNC  {saison_dir.name}/{prod_dir.name}: '
                      f'{len(images)} Bilder, {portrait_count} Hochformat ignoriert')
                continue

            target_prod = dst / saison_dir.name / prod_dir.name
            target_prod.mkdir(parents=True, exist_ok=True)

            # excluded.txt und quality_scores.json bewahren
            excl_path  = target_prod / 'excluded.txt'
            score_path = target_prod / 'quality_scores.json'
            saved_excl   = excl_path.read_text('utf-8')  if excl_path.exists()  else None
            saved_scores = json.loads(score_path.read_text('utf-8')) if score_path.exists() else {}

            # Duplikate erkennen
            dupes = detect_duplicates(images)
            stats['duplikate'] += len(dupes)

            # Bilder kopieren + optimieren + analysieren
            quality_scores = {}
            seen_names: dict = {}

            for img in images:
                name = img.name
                if name in seen_names:
                    rel  = img.relative_to(prod_dir)
                    name = '__'.join(rel.parts)
                    stats['konflikte'] += 1
                seen_names[name] = img

                dst_img = target_prod / name
                stats['original_bytes'] += img.stat().st_size
                try:
                    size = optimize_image(img, dst_img)
                    stats['optimiert_bytes'] += size
                except Exception as e:
                    shutil.copy2(img, dst_img)
                    stats['optimiert_bytes'] += dst_img.stat().st_size
                    print(f'    Optimierung fehlgeschlagen ({name}): {e}')

                stats['kopiert'] += 1

                # Qualitätsanalyse (nur wenn noch kein Score vorhanden)
                if name not in saved_scores:
                    q = analyze_quality(dst_img)
                    if img.name in dupes:
                        q['flagged'] = True
                        q['reason'] = f'Duplikat von {dupes[img.name]}'
                else:
                    q = saved_scores[name]
                quality_scores[name] = q
                if q.get('flagged'):
                    stats['flagged'] += 1

            # Scores speichern
            score_path.write_text(
                json.dumps(quality_scores, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            # excluded.txt wiederherstellen
            if saved_excl is not None:
                excl_path.write_text(saved_excl, encoding='utf-8')

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
    cmd = [
        'rclone', 'copy', str(MEDIA_DIR),
        f'{RCLONE_REMOTE}:{RCLONE_PATH}',
        '--include', 'excluded.txt', '--progress',
    ]
    print('Übertrage excluded.txt-Dateien nach OneDrive …')
    subprocess.run(cmd)

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

    if not args.no_sync:
        TMP_DIR.mkdir(exist_ok=True)
        run_rclone_sync(dry_run=args.dry_run)

    if args.dry_run:
        print(f'\nVorschau: {TMP_DIR} → {MEDIA_DIR}')
        process(TMP_DIR, MEDIA_DIR, dry_run=True)
        return

    print(f'\nVerarbeite {TMP_DIR} → {MEDIA_DIR} …')
    if MEDIA_DIR.exists():
        for item in MEDIA_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
    MEDIA_DIR.mkdir(exist_ok=True)
    stats = process(TMP_DIR, MEDIA_DIR)

    saved_mb    = (stats['original_bytes'] - stats['optimiert_bytes']) / 1_000_000
    original_mb = stats['original_bytes'] / 1_000_000
    optimiert_mb = stats['optimiert_bytes'] / 1_000_000

    print(f'\nErgebnis:')
    print(f'  {stats["saisons"]} Spielzeiten, {stats["produktionen"]} Produktionen')
    if stats['uebersprungen']:
        print(f'  {stats["uebersprungen"]} Produktionen übersprungen')
    print(f'  {stats["kopiert"]} Bilder kopiert und optimiert')
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
