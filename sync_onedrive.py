#!/usr/bin/env python3
# Synchronisiert Bilder von OneDrive nach ~/media/.
#
# Was dieses Skript tut:
#   1. rclone sync: Rohdaten von OneDrive in ein temporäres Verzeichnis holen
#   2. Hochformat-Bilder (Höhe > Breite) herausfiltern
#   3. Verzeichnisstruktur auf genau zwei Ebenen (Saison/Produktion) reduzieren –
#      tiefer verschachtelte Bilder landen direkt im Produktionsordner
#   4. Ergebnis nach ~/media/ schreiben
#
# excluded.txt-Dateien in Produktionsordnern werden bei der Sync beibehalten.
# Neue excluded.txt-Dateien, die lokal durch Kuration entstehen, werden beim
# nächsten 'rclone sync --two-way' (oder manuell) zurück nach OneDrive übertragen.
#
# Aufruf:
#   python3 sync_onedrive.py                  # Sync + Verarbeitung
#   python3 sync_onedrive.py --dry-run        # Zeigt nur, was passieren würde
#   python3 sync_onedrive.py --no-sync        # Nur Verarbeitung (kein rclone)

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# Konfiguration – hier anpassen
# ---------------------------------------------------------------------------

RCLONE_REMOTE = 'onedrive'          # Name der rclone-Konfiguration (rclone config)
RCLONE_PATH   = 'Theater/Fotos'     # Pfad in OneDrive
MEDIA_DIR     = Path.home() / 'media'
TMP_DIR       = Path.home() / '.media_sync_tmp'

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}

# ---------------------------------------------------------------------------
# Hochformat-Prüfung
# ---------------------------------------------------------------------------

def is_portrait(path: Path) -> bool:
    try:
        with Image.open(path) as img:
            w, h = img.size
            return h > w
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Verzeichnis-Flatten: Saison/Produktion/[beliebige Tiefe] → Saison/Produktion/
# ---------------------------------------------------------------------------

def collect_images(folder: Path) -> list[Path]:
    """Alle Bilddateien in folder, beliebig tief, ohne Hochformat."""
    result = []
    portrait_count = 0
    for f in sorted(folder.rglob('*')):
        if f.suffix.lower() not in IMAGE_EXTS:
            continue
        if is_portrait(f):
            portrait_count += 1
            continue
        result.append(f)
    return result, portrait_count


def process(src: Path, dst: Path, dry_run: bool = False) -> dict:
    """
    Liest Saison/Produktion-Struktur aus src, schreibt nach dst.
    Gibt Statistiken zurück.
    """
    stats = {'saisons': 0, 'produktionen': 0, 'kopiert': 0, 'hochformat': 0, 'konflikte': 0}

    for saison_dir in sorted(src.iterdir()):
        if not saison_dir.is_dir():
            continue
        stats['saisons'] += 1

        for prod_dir in sorted(saison_dir.iterdir()):
            if not prod_dir.is_dir():
                continue
            stats['produktionen'] += 1

            target_prod = dst / saison_dir.name / prod_dir.name
            images, portrait_count = collect_images(prod_dir)
            stats['hochformat'] += portrait_count

            if dry_run:
                print(f"  {saison_dir.name}/{prod_dir.name}: "
                      f"{len(images)} Bilder, {portrait_count} Hochformat ignoriert")
                continue

            target_prod.mkdir(parents=True, exist_ok=True)

            # excluded.txt aus Zielordner bewahren
            existing_excluded = None
            existing_excl_path = target_prod / 'excluded.txt'
            if existing_excl_path.exists():
                existing_excluded = existing_excl_path.read_text(encoding='utf-8')

            # Bilder kopieren – bei Namenskonflikt Ordnerpfad als Präfix
            seen_names: dict[str, Path] = {}
            for img in images:
                name = img.name
                if name in seen_names:
                    # Konflikt: gleichnamige Datei aus anderem Unterordner
                    rel = img.relative_to(prod_dir)
                    name = '__'.join(rel.parts)  # z.B. "2024__show1__bild.jpg"
                    stats['konflikte'] += 1
                seen_names[name] = img
                shutil.copy2(img, target_prod / name)
                stats['kopiert'] += 1

            # excluded.txt wiederherstellen
            if existing_excluded is not None:
                existing_excl_path.write_text(existing_excluded, encoding='utf-8')

    return stats

# ---------------------------------------------------------------------------
# rclone-Sync
# ---------------------------------------------------------------------------

def run_rclone_sync(dry_run: bool = False):
    cmd = [
        'rclone', 'sync',
        f'{RCLONE_REMOTE}:{RCLONE_PATH}',
        str(TMP_DIR),
        '--progress',
        '--filter', '+ *.jpg',
        '--filter', '+ *.jpeg',
        '--filter', '+ *.png',
        '--filter', '+ *.bmp',
        '--filter', '+ *.gif',
        '--filter', '+ excluded.txt',   # Kurationsdaten mitsynchen
        '--filter', '- *',
    ]
    if dry_run:
        cmd.append('--dry-run')

    print(f"rclone sync {RCLONE_REMOTE}:{RCLONE_PATH} → {TMP_DIR}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("Fehler beim rclone-Sync.", file=sys.stderr)
        sys.exit(1)


def push_excluded_back():
    """Überträgt lokale excluded.txt-Dateien zurück nach OneDrive."""
    cmd = [
        'rclone', 'copy',
        str(MEDIA_DIR),
        f'{RCLONE_REMOTE}:{RCLONE_PATH}',
        '--include', 'excluded.txt',
        '--progress',
    ]
    print("Übertrage excluded.txt-Dateien nach OneDrive …")
    subprocess.run(cmd)

# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='OneDrive → ~/media Sync')
    parser.add_argument('--dry-run',  action='store_true', help='Nur anzeigen, nicht kopieren')
    parser.add_argument('--no-sync',  action='store_true', help='rclone überspringen, nur verarbeiten')
    parser.add_argument('--push-excluded', action='store_true',
                        help='Lokale excluded.txt-Dateien zurück nach OneDrive übertragen')
    args = parser.parse_args()

    if args.push_excluded:
        push_excluded_back()
        return

    if not args.no_sync:
        TMP_DIR.mkdir(exist_ok=True)
        run_rclone_sync(dry_run=args.dry_run)

    if args.dry_run:
        print(f"\nVorschau: Verarbeitung {TMP_DIR} → {MEDIA_DIR}")
        stats = process(TMP_DIR, MEDIA_DIR, dry_run=True)
    else:
        print(f"\nVerarbeite {TMP_DIR} → {MEDIA_DIR} …")
        # Zielverzeichnis leeren (excluded.txt bleibt dank process() erhalten)
        if MEDIA_DIR.exists():
            for item in MEDIA_DIR.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
        MEDIA_DIR.mkdir(exist_ok=True)
        stats = process(TMP_DIR, MEDIA_DIR, dry_run=False)

    print(f"\nErgebnis:")
    print(f"  {stats['saisons']} Spielzeiten, {stats['produktionen']} Produktionen")
    print(f"  {stats['kopiert']} Bilder kopiert")
    print(f"  {stats['hochformat']} Hochformat-Bilder ignoriert")
    if stats['konflikte']:
        print(f"  {stats['konflikte']} Namenskonflikte umbenannt (Ordnerpfad als Präfix)")


if __name__ == '__main__':
    main()
