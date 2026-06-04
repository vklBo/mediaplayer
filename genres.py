#!/usr/bin/env python3
# =============================================================================
# genres.py – Genres für Produktionen verwalten (läuft auf dem Server)
#
# Genres sind hierarchisch mit "/" aufgebaut, z.B.:
#   JungesEnsemble/Kinder
#   JungesEnsemble/Jugendliche
#   Drama/Klassiker
#
# Eine Produktion kann mehrere Genres haben (Komma-getrennt).
#
# Quelle der Wahrheit: genre.txt in jedem Produktionsordner (ein Genre pro Zeile).
# Diese werden per Syncthing an die Pis verteilt und vom Sync erhalten.
# Zur bequemen Bearbeitung dient die zentrale Datei genres.txt (neben diesem Skript).
#
# Ablauf:
#   python3 genres.py scan     # genres.txt aus vorhandenen Produktionen erzeugen
#   nano genres.txt            # Genres eintragen
#   python3 genres.py apply    # genre.txt in jeden Produktionsordner schreiben
#   python3 genres.py list     # aktuelle Zuordnung + Genre-Baum anzeigen
# =============================================================================

import sys
from pathlib import Path

MEDIA_DIR   = Path('/srv/media')
# Im Projektverzeichnis (schreibbar für taf), nicht unter /srv (nur root)
GENRES_FILE = Path(__file__).parent / 'genres.txt'
GENRE_TXT   = 'genre.txt'


# ---------------------------------------------------------------------------
# Produktionen finden (Saison/Produktion = zwei Ebenen tief)
# ---------------------------------------------------------------------------

def finde_produktionen() -> list:
    """Gibt sortierte Liste von (saison, produktion, pfad) zurück."""
    result = []
    if not MEDIA_DIR.exists():
        print(f'Medienverzeichnis nicht gefunden: {MEDIA_DIR}', file=sys.stderr)
        sys.exit(1)
    for saison in sorted(d for d in MEDIA_DIR.iterdir() if d.is_dir()):
        if saison.name in ('basismedien',):
            continue
        for prod in sorted(d for d in saison.iterdir() if d.is_dir()):
            result.append((saison.name, prod.name, prod))
    return result


def lese_genre_txt(prod_pfad: Path) -> list:
    """Liest genre.txt eines Produktionsordners → Liste von Genres."""
    f = prod_pfad / GENRE_TXT
    if not f.exists():
        return []
    return [z.strip() for z in f.read_text(encoding='utf-8').splitlines() if z.strip()]


def schreibe_genre_txt(prod_pfad: Path, genres: list):
    """Schreibt genre.txt (oder löscht sie wenn keine Genres)."""
    f = prod_pfad / GENRE_TXT
    if genres:
        f.write_text('\n'.join(genres) + '\n', encoding='utf-8')
    elif f.exists():
        f.unlink()


# ---------------------------------------------------------------------------
# scan: zentrale Datei erzeugen/aktualisieren
# ---------------------------------------------------------------------------

def cmd_scan():
    produktionen = finde_produktionen()
    if not produktionen:
        print('Keine Produktionen gefunden.')
        return

    lines = [
        '# Genre-Zuordnung (hierarchisch mit /)',
        '# Format:  Spielzeit/Produktion = Genre1, Oberkategorie/Unterkategorie',
        '#',
        '# Beispiele:',
        '#   2024-25/Faust         = Drama/Klassiker, Abendprogramm',
        '#   2024-25/Raeuberkinder = JungesEnsemble/Kinder',
        '#   2024-25/Romeo         = JungesEnsemble/Jugendliche, Drama',
        '#',
        '# Mehrere Genres mit Komma trennen. Leer lassen = kein Genre.',
        '',
    ]
    current_saison = None
    for saison, prod, pfad in produktionen:
        if saison != current_saison:
            lines.append(f'\n# --- {saison} ---')
            current_saison = saison
        vorhandene = ', '.join(lese_genre_txt(pfad))
        lines.append(f'{saison}/{prod} = {vorhandene}')

    GENRES_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'✓ {len(produktionen)} Produktionen in {GENRES_FILE} geschrieben.')
    print(f'→ Genres eintragen: nano {GENRES_FILE}')
    print(f'→ Danach übernehmen: python3 genres.py apply')


# ---------------------------------------------------------------------------
# apply: zentrale Datei → genre.txt in die Ordner
# ---------------------------------------------------------------------------

def parse_genres_datei() -> dict:
    """Liest /srv/genres.txt → dict 'saison/produktion' → [genres]."""
    if not GENRES_FILE.exists():
        print(f'{GENRES_FILE} nicht gefunden. Erst: python3 genres.py scan', file=sys.stderr)
        sys.exit(1)
    zuordnung = {}
    for zeile in GENRES_FILE.read_text(encoding='utf-8').splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith('#') or '=' not in zeile:
            continue
        pfad_teil, genre_teil = zeile.split('=', 1)
        pfad = pfad_teil.strip().strip('/')
        genres = [g.strip().strip('/') for g in genre_teil.split(',') if g.strip()]
        zuordnung[pfad] = genres
    return zuordnung


def cmd_apply():
    zuordnung = parse_genres_datei()
    geschrieben, geleert = 0, 0
    for saison, prod, pfad in finde_produktionen():
        key = f'{saison}/{prod}'
        genres = zuordnung.get(key, [])
        vorher = lese_genre_txt(pfad)
        if genres != vorher:
            schreibe_genre_txt(pfad, genres)
            if genres:
                geschrieben += 1
            else:
                geleert += 1
    print(f'✓ {geschrieben} Produktionen mit Genres versehen, {geleert} geleert.')
    print('→ Syncthing verteilt die genre.txt-Dateien automatisch an die Pis.')


# ---------------------------------------------------------------------------
# list: Zuordnung + Genre-Baum anzeigen
# ---------------------------------------------------------------------------

def cmd_list():
    produktionen = finde_produktionen()
    genre_zu_prod = {}
    for saison, prod, pfad in produktionen:
        for g in lese_genre_txt(pfad):
            genre_zu_prod.setdefault(g, []).append(f'{saison}/{prod}')

    if not genre_zu_prod:
        print('Noch keine Genres zugewiesen.')
        print('→ python3 genres.py scan, dann /srv/genres.txt bearbeiten, dann apply')
        return

    # Genre-Baum aufbauen
    print('\nGenre-Baum:')
    baum = {}
    for genre in genre_zu_prod:
        knoten = baum
        for teil in genre.split('/'):
            knoten = knoten.setdefault(teil, {})

    def drucke_baum(knoten, prefix=''):
        for name in sorted(knoten):
            # Vollpfad rekonstruieren für die Zählung
            print(f'{prefix}{name}')
            drucke_baum(knoten[name], prefix + '  ')

    drucke_baum(baum)

    print('\nZuordnung:')
    for genre in sorted(genre_zu_prod):
        prods = genre_zu_prod[genre]
        print(f'  {genre}  ({len(prods)} Produktion{"en" if len(prods)!=1 else ""})')
        for p in prods:
            print(f'      {p}')


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('scan', 'apply', 'list'):
        print('Verwendung:')
        print('  python3 genres.py scan    # /srv/genres.txt erzeugen')
        print('  python3 genres.py apply   # genre.txt in die Ordner schreiben')
        print('  python3 genres.py list    # Zuordnung + Genre-Baum anzeigen')
        sys.exit(1)

    {'scan': cmd_scan, 'apply': cmd_apply, 'list': cmd_list}[sys.argv[1]]()


if __name__ == '__main__':
    main()
