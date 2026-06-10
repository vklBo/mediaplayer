#!/usr/bin/env python3
# =============================================================================
# face_exclude.py – Bilder mit bestimmten Personen finden und ausblenden
#
# Läuft auf dem Medienserver. Liest Referenzfotos aus FACES_DIR:
#   ~/mediaplayer/faces/
#       alex/          ← ein Unterordner pro Person
#           foto1.jpg
#           foto2.jpg
#       person2/
#           foto1.jpg
#
# Vergleicht alle Bilder in MEDIA_DIR gegen die Referenzfotos und trägt
# Treffer in die jeweilige excluded.txt ein.
# Syncthing verteilt die excluded.txt automatisch an die Pis.
#
# Aufruf:
#   python3 face_exclude.py              # alle Personen aus faces/, nur Vorschau
#   python3 face_exclude.py --apply      # Treffer in excluded.txt eintragen
#   python3 face_exclude.py --ref alex   # nur eine bestimmte Person prüfen
#   python3 face_exclude.py --ref /pfad/zum/foto.jpg  # einzelnes Referenzfoto
# =============================================================================

import argparse
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

DEFAULT_MEDIA_DIR = Path('/srv/media')
DEFAULT_FACES_DIR = Path(__file__).parent / 'faces'
IMAGE_EXTS        = {'.jpg', '.jpeg', '.png', '.bmp'}
EXCLUDED_FILE     = 'excluded.txt'

# Schwellwert für Gesichtserkennung (niedriger = strenger, höher = großzügiger)
# DeepFace-Standard ist 0.4 für Facenet512; wir nehmen 0.45 für mehr Treffer
# (lieber zu viele ausblenden als zu wenige)
DISTANCE_THRESHOLD = 0.45


# ---------------------------------------------------------------------------
# excluded.txt Helfer
# ---------------------------------------------------------------------------

def load_excluded(folder: Path) -> set:
    f = folder / EXCLUDED_FILE
    if not f.exists():
        return set()
    return {l.strip() for l in f.read_text(encoding='utf-8').splitlines() if l.strip()}


def save_excluded(folder: Path, excluded: set):
    path = folder / EXCLUDED_FILE
    if excluded:
        path.write_text('\n'.join(sorted(excluded)) + '\n', encoding='utf-8')
    elif path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Gesichtserkennung
# ---------------------------------------------------------------------------

def load_ref_images(ref_path: Path) -> list:
    """Gibt Liste aller Referenzbilder zurück – einzelne Datei oder ganzes Verzeichnis."""
    if ref_path.is_dir():
        refs = sorted(f for f in ref_path.iterdir() if f.suffix.lower() in IMAGE_EXTS)
        if not refs:
            print(f'Fehler: Keine Bilder in {ref_path}', file=sys.stderr)
            sys.exit(1)
        print(f'{len(refs)} Referenzfotos geladen aus {ref_path}')
        return refs
    elif ref_path.is_file():
        return [ref_path]
    else:
        print(f'Fehler: Referenzfoto/-ordner nicht gefunden: {ref_path}', file=sys.stderr)
        sys.exit(1)


def find_matches(ref_path: Path, media_dir: Path, threshold: float = DISTANCE_THRESHOLD) -> dict:
    """
    Durchsucht alle Produktionsordner nach Bildern die die Person aus ref_path zeigen.
    ref_path kann eine einzelne Datei oder ein Verzeichnis mit mehreren Referenzfotos sein.
    Gibt dict zurück: 'saison/produktion' → [liste von Path-Objekten]
    """
    try:
        from deepface import DeepFace
    except ImportError:
        print('Fehler: deepface nicht installiert. pip3 install deepface', file=sys.stderr)
        sys.exit(1)

    ref_images = load_ref_images(ref_path)

    # Alle Produktionsordner (zwei Ebenen tief: saison/produktion)
    prod_dirs = []
    for saison in sorted(media_dir.iterdir()):
        if not saison.is_dir() or saison.name == 'basismedien':
            continue
        for prod in sorted(saison.iterdir()):
            if prod.is_dir():
                prod_dirs.append((saison.name, prod.name, prod))

    if not prod_dirs:
        print(f'Keine Produktionen gefunden in {media_dir}')
        sys.exit(0)

    treffer = defaultdict(list)
    gesamt  = 0
    geprueft = 0

    print(f'Referenzfotos: {", ".join(r.name for r in ref_images)}')
    print(f'Medienordner:  {media_dir}')
    print(f'Threshold:     {threshold}')
    print(f'Modell:        Facenet512\n')

    for saison_name, prod_name, prod_path in prod_dirs:
        bilder = sorted(f for f in prod_path.rglob('*') if f.suffix.lower() in IMAGE_EXTS)
        gesamt += len(bilder)
        key = f'{saison_name}/{prod_name}'
        print(f'  {key} ({len(bilder)} Bilder) …', end='', flush=True)
        prod_treffer = 0

        for bild in bilder:
            geprueft += 1
            try:
                # Treffer wenn mindestens ein Referenzfoto übereinstimmt
                gefunden = False
                for ref in ref_images:
                    result = DeepFace.verify(
                        img1_path=str(ref),
                        img2_path=str(bild),
                        model_name='Facenet512',
                        distance_metric='cosine',
                        enforce_detection=False,
                        threshold=threshold,
                    )
                    if result.get('verified'):
                        gefunden = True
                        break
                if gefunden:
                    treffer[key].append(bild)
                    prod_treffer += 1
            except Exception:
                pass  # kein Gesicht gefunden oder Fehler → überspringen

        if prod_treffer:
            print(f' {prod_treffer} Treffer')
        else:
            print(' –')

    print(f'\n{geprueft} Bilder geprüft, {sum(len(v) for v in treffer.values())} Treffer gefunden.')
    return dict(treffer)


# ---------------------------------------------------------------------------
# Ausgabe und Anwenden
# ---------------------------------------------------------------------------

def show_results(treffer: dict):
    if not treffer:
        print('\nKeine Treffer – Person nicht gefunden.')
        return

    print('\n' + '=' * 60)
    print('TREFFER nach Produktion:')
    print('=' * 60)
    for key in sorted(treffer):
        bilder = treffer[key]
        print(f'\n  {key} ({len(bilder)} Bilder):')
        for b in bilder:
            print(f'    - {b.name}')


def apply_excluded(treffer: dict, media_dir: Path):
    """Trägt alle Treffer in die jeweiligen excluded.txt ein."""
    gesamt = 0
    for key, bilder in treffer.items():
        parts = key.split('/', 1)
        if len(parts) != 2:
            continue
        prod_path = media_dir / parts[0] / parts[1]
        excluded  = load_excluded(prod_path)
        neu       = 0
        for bild in bilder:
            if bild.name not in excluded:
                excluded.add(bild.name)
                neu += 1
        save_excluded(prod_path, excluded)
        print(f'  {key}: {neu} neu eingetragen ({len(bilder)} gesamt)')
        gesamt += neu
    print(f'\n✓ {gesamt} Einträge in excluded.txt geschrieben.')
    print('Syncthing verteilt die Änderungen automatisch an die Pis.')


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def load_persons(ref_arg: str | None, faces_dir: Path) -> list:
    """
    Gibt Liste von (name, ref_path) zurück.
    - Kein --ref: alle Unterordner in faces_dir
    - --ref name: nur der Unterordner faces_dir/name
    - --ref /pfad: direkt als Pfad (Datei oder Ordner)
    """
    if ref_arg is None:
        # Alle Personen aus faces/
        if not faces_dir.exists():
            print(f'Fehler: Faces-Verzeichnis nicht gefunden: {faces_dir}', file=sys.stderr)
            print(f'Bitte anlegen: mkdir -p {faces_dir}/<name> und Fotos hineinkopieren.')
            sys.exit(1)
        persons = [(d.name, d) for d in sorted(faces_dir.iterdir()) if d.is_dir()]
        if not persons:
            print(f'Keine Personen-Unterordner in {faces_dir} gefunden.')
            sys.exit(0)
        return persons

    ref_path = Path(ref_arg)
    if not ref_path.is_absolute():
        # Relativer Name → in faces_dir suchen
        ref_path = faces_dir / ref_arg
    if not ref_path.exists():
        print(f'Fehler: {ref_path} nicht gefunden.', file=sys.stderr)
        sys.exit(1)
    name = ref_path.stem if ref_path.is_file() else ref_path.name
    return [(name, ref_path)]


def main():
    parser = argparse.ArgumentParser(
        description='Bilder mit bestimmten Personen finden und in excluded.txt eintragen.'
    )
    parser.add_argument('--ref',   default=None,
                        help='Name des Personen-Unterordners in faces/ oder direkter Pfad. '
                             'Ohne --ref: alle Personen in faces/ werden geprüft.')
    parser.add_argument('--media', default=str(DEFAULT_MEDIA_DIR),
                        help=f'Medienordner (Standard: {DEFAULT_MEDIA_DIR})')
    parser.add_argument('--faces', default=str(DEFAULT_FACES_DIR),
                        help=f'Faces-Verzeichnis (Standard: {DEFAULT_FACES_DIR})')
    parser.add_argument('--apply', action='store_true',
                        help='Treffer in excluded.txt eintragen (ohne: nur Vorschau)')
    parser.add_argument('--threshold', type=float, default=DISTANCE_THRESHOLD,
                        help=f'Erkennungsschwelle (Standard: {DISTANCE_THRESHOLD}, '
                             'niedriger = strenger)')
    args = parser.parse_args()

    threshold  = args.threshold
    media_dir  = Path(args.media)
    faces_dir  = Path(args.faces)
    persons    = load_persons(args.ref, faces_dir)

    alle_treffer = {}
    for name, ref_path in persons:
        print(f'\n{"=" * 60}')
        print(f'Person: {name}')
        print('=' * 60)
        treffer = find_matches(ref_path, media_dir, threshold)
        show_results(treffer)
        alle_treffer[name] = treffer

    gesamt = sum(
        len(bilder)
        for treffer in alle_treffer.values()
        for bilder in treffer.values()
    )

    if gesamt == 0:
        print('\nKeine Treffer gefunden.')
        return

    if args.apply:
        print(f'\n{"=" * 60}')
        print('Trage alle Treffer in excluded.txt ein …')
        for name, treffer in alle_treffer.items():
            if treffer:
                print(f'\n  Person: {name}')
                apply_excluded(treffer, media_dir)
        print('\n✓ Fertig. Syncthing verteilt die Änderungen automatisch an die Pis.')
    else:
        print(f'\nHinweis: Ohne --apply werden keine excluded.txt-Dateien geschrieben.')
        print(f'Aufruf mit --apply um {gesamt} Bilder auszublenden.')


if __name__ == '__main__':
    main()
