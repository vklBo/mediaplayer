#!/usr/bin/env python3
# Synchronisiert Bilder von OneDrive/SharePoint nach ~/media/.
#
# Typischer erster Ablauf:
#   1. rclone config                              # einmalig: Browser-Login mit M365-Konto
#   2. python3 sync_onedrive.py --list-folders    # alle Ordner auflisten → excluded_folders.txt
#   3. excluded_folders.txt bearbeiten:           # Zeilen der gewünschten Ordner löschen
#   4. python3 sync_onedrive.py                   # erster Sync (nur nicht ausgeschlossene Ordner)
#
# Weiterer Betrieb:
#   python3 sync_onedrive.py                      # Sync + Verarbeitung
#   python3 sync_onedrive.py --dry-run            # Zeigt nur, was passieren würde
#   python3 sync_onedrive.py --push-excluded      # lokale excluded.txt zurück nach OneDrive

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
RCLONE_PATH   = 'Theater/Fotos'     # Pfad in OneDrive/SharePoint
MEDIA_DIR     = Path.home() / 'media'
TMP_DIR       = Path.home() / '.media_sync_tmp'

# Datei mit Ordnern, die beim Sync ÜBERSPRUNGEN werden.
# Format: eine Zeile pro Ordner als "Saison/Produktion".
# Leere Zeilen und Zeilen mit # werden ignoriert.
EXCLUDED_FOLDERS_FILE = Path(__file__).parent / 'excluded_folders.txt'

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}

# ---------------------------------------------------------------------------
# excluded_folders.txt lesen
# ---------------------------------------------------------------------------

def load_excluded_folders() -> set:
    """Liest excluded_folders.txt und gibt eine Menge von 'Saison/Produktion'-Pfaden zurück."""
    if not EXCLUDED_FOLDERS_FILE.exists():
        return set()
    lines = EXCLUDED_FOLDERS_FILE.read_text(encoding='utf-8').splitlines()
    return {
        line.strip().strip('/')
        for line in lines
        if line.strip() and not line.strip().startswith('#')
    }


def is_excluded_folder(saison: str, produktion: str, excluded: set) -> bool:
    return f'{saison}/{produktion}' in excluded or saison in excluded

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

def collect_images(folder: Path):
    """Alle Querformat-Bilddateien in folder, beliebig tief."""
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
    """Liest Saison/Produktion-Struktur aus src, schreibt nach dst."""
    excluded = load_excluded_folders()
    stats = {'saisons': 0, 'produktionen': 0, 'kopiert': 0,
             'hochformat': 0, 'konflikte': 0, 'uebersprungen': 0}

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
                    print(f"  SKIP  {saison_dir.name}/{prod_dir.name}")
                continue

            stats['produktionen'] += 1
            images, portrait_count = collect_images(prod_dir)
            stats['hochformat'] += portrait_count

            if dry_run:
                print(f"  SYNC  {saison_dir.name}/{prod_dir.name}: "
                      f"{len(images)} Bilder, {portrait_count} Hochformat ignoriert")
                continue

            target_prod = dst / saison_dir.name / prod_dir.name
            target_prod.mkdir(parents=True, exist_ok=True)

            # excluded.txt aus Zielordner bewahren
            excl_path = target_prod / 'excluded.txt'
            existing_excluded = excl_path.read_text(encoding='utf-8') if excl_path.exists() else None

            # Bilder kopieren – bei Namenskonflikt Ordnerpfad als Präfix
            seen: dict[str, Path] = {}
            for img in images:
                name = img.name
                if name in seen:
                    rel = img.relative_to(prod_dir)
                    name = '__'.join(rel.parts)
                    stats['konflikte'] += 1
                seen[name] = img
                shutil.copy2(img, target_prod / name)
                stats['kopiert'] += 1

            if existing_excluded is not None:
                excl_path.write_text(existing_excluded, encoding='utf-8')

    return stats

# ---------------------------------------------------------------------------
# rclone-Ordner auflisten → excluded_folders.txt erstellen
# ---------------------------------------------------------------------------

def list_folders_and_write_exclude():
    """
    Listet alle Saison/Produktions-Ordner in OneDrive auf und schreibt sie
    alle in excluded_folders.txt. Der Benutzer löscht dann die Zeilen der
    Ordner, die er synchronisieren möchte.
    """
    print(f"Lese Ordnerstruktur von {RCLONE_REMOTE}:{RCLONE_PATH} …")
    print("(Nur Verzeichnisse, keine Dateien – geht schnell)\n")

    result = subprocess.run(
        ['rclone', 'lsd', '--recursive', f'{RCLONE_REMOTE}:{RCLONE_PATH}'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("Fehler beim Auflisten der Ordner:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        print("\nIst rclone konfiguriert? → rclone config", file=sys.stderr)
        sys.exit(1)

    # rclone lsd gibt aus: "          -1 2024-01-01 00:00:00        -1 Saison/Produktion"
    folders = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            folders.append(parts[4].strip())

    # Nur zwei Ebenen tief (Saison/Produktion), keine tieferen Unterordner
    two_level = sorted({
        '/'.join(f.split('/')[:2])
        for f in folders
        if '/' in f
    })

    if not two_level:
        print("Keine Unterordner gefunden. Stimmt der RCLONE_PATH?")
        print(f"  Aktuell: {RCLONE_REMOTE}:{RCLONE_PATH}")
        sys.exit(1)

    # Datei schreiben – alle Ordner sind standardmäßig ausgeschlossen
    lines = [
        '# excluded_folders.txt – Ordner die NICHT synchronisiert werden',
        '# Zeile löschen oder mit # auskommentieren = Ordner WIRD synchronisiert',
        '# Saison alleine (ohne /Produktion) schließt die ganze Spielzeit aus.',
        '#',
        f'# Gefunden: {len(two_level)} Produktionen',
        '#',
        '',
    ]
    # Leerzeile zwischen Spielzeiten
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

    print(f"✓ {len(two_level)} Produktionen gefunden.")
    print(f"✓ Alle in '{EXCLUDED_FOLDERS_FILE.name}' eingetragen (alle ausgeschlossen).")
    print()
    print("Nächste Schritte:")
    print(f"  1. '{EXCLUDED_FOLDERS_FILE.name}' öffnen")
    print("  2. Zeilen der Ordner LÖSCHEN, die synchronisiert werden sollen")
    print("  3. python3 sync_onedrive.py --dry-run   # Vorschau")
    print("  4. python3 sync_onedrive.py             # Sync starten")

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
        '--filter', '+ excluded.txt',
        '--filter', '- *',
    ]

    # Ausgeschlossene Ordner als rclone-Filter ergänzen
    excluded = load_excluded_folders()
    for folder in excluded:
        cmd += ['--exclude', f'{folder}/**']

    if dry_run:
        cmd.append('--dry-run')

    print(f"rclone sync {RCLONE_REMOTE}:{RCLONE_PATH} → {TMP_DIR}")
    if excluded:
        print(f"  ({len(excluded)} Ordner werden übersprungen)")
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
    parser = argparse.ArgumentParser(description='OneDrive/SharePoint → ~/media Sync')
    parser.add_argument('--list-folders', action='store_true',
                        help='Ordnerstruktur auflisten und excluded_folders.txt erstellen')
    parser.add_argument('--dry-run',  action='store_true',
                        help='Nur anzeigen, nicht kopieren')
    parser.add_argument('--no-sync',  action='store_true',
                        help='rclone überspringen, nur lokale Verarbeitung')
    parser.add_argument('--push-excluded', action='store_true',
                        help='Lokale excluded.txt-Dateien zurück nach OneDrive übertragen')
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
        print(f"\nVorschau: Verarbeitung {TMP_DIR} → {MEDIA_DIR}")
        stats = process(TMP_DIR, MEDIA_DIR, dry_run=True)
    else:
        print(f"\nVerarbeite {TMP_DIR} → {MEDIA_DIR} …")
        if MEDIA_DIR.exists():
            for item in MEDIA_DIR.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
        MEDIA_DIR.mkdir(exist_ok=True)
        stats = process(TMP_DIR, MEDIA_DIR, dry_run=False)

    print(f"\nErgebnis:")
    print(f"  {stats['saisons']} Spielzeiten, {stats['produktionen']} Produktionen synchronisiert")
    if stats['uebersprungen']:
        print(f"  {stats['uebersprungen']} Produktionen übersprungen (excluded_folders.txt)")
    print(f"  {stats['kopiert']} Bilder kopiert")
    print(f"  {stats['hochformat']} Hochformat-Bilder ignoriert")
    if stats['konflikte']:
        print(f"  {stats['konflikte']} Namenskonflikte umbenannt")


if __name__ == '__main__':
    main()
