#!/usr/bin/env python3
# =============================================================================
# qlab_media_collector.py – QLab-Medienbibliothek aufbauen
#
# Scannt das QLab-Backup-Verzeichnis auf dem Server, sammelt alle Mediendateien,
# extrahiert technische Metadaten per ffprobe, dedupliziert und kategorisiert.
# Ergebnis: /srv/qlab_media/ (sortierte Kopien) + katalog.json (durchsuchbarer Index)
#
# Aufruf:
#   python3 qlab_media_collector.py             # Scan + Katalog aktualisieren
#   python3 qlab_media_collector.py --dry-run   # Nur anzeigen, nichts kopieren
#   python3 qlab_media_collector.py --clean     # qlab_media/ leeren und neu aufbauen
# =============================================================================

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Konfiguration – hier anpassen
# ---------------------------------------------------------------------------

# Verzeichnis mit den QLab-Backups vom Mac (per Syncthing empfangen)
QLAB_BACKUP_DIR = Path('/srv/qlab_backup')

# Zielverzeichnis für die aufgeräumte Medienbibliothek
QLAB_MEDIA_DIR  = Path('/srv/qlab_media')

# Pfad zum Katalog
KATALOG_PATH    = QLAB_MEDIA_DIR / 'katalog.json'

# QLab-Workspace-Endungen (QLab 4 + 5)
QLAB_EXTENSIONS = {'.qlab4', '.qlab5'}

# Unterstützte Mediendateitypen
AUDIO_EXTS = {'.wav', '.aiff', '.aif', '.mp3', '.aac', '.m4a', '.flac', '.ogg', '.caf'}
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.mxf'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.gif'}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS

# Audio-Kategorisierung nach Länge (Sekunden)
SFX_MAX     =  5.0   # bis 5s   → sfx
STING_MAX   = 60.0   # bis 60s  → stings
# > 60s                          → musik_ambience

# ---------------------------------------------------------------------------
# ffprobe-Analyse
# ---------------------------------------------------------------------------

def ffprobe(path: Path) -> dict:
    """Extrahiert Metadaten per ffprobe. Gibt leeres dict zurück bei Fehler."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            str(path),
        ], capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return {}

        data     = json.loads(result.stdout)
        fmt      = data.get('format', {})
        streams  = data.get('streams', [])

        # Ersten Audio- bzw. Video-Stream suchen
        audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)
        video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)

        tags = {k.lower(): v for k, v in fmt.get('tags', {}).items()}
        # Auch Stream-Tags einbeziehen
        for s in streams:
            for k, v in s.get('tags', {}).items():
                tags.setdefault(k.lower(), v)

        info = {
            'dauer_sek':    round(float(fmt.get('duration', 0) or 0), 2),
            'groesse_bytes': int(fmt.get('size', 0) or 0),
            'bitrate_kbps': round(int(fmt.get('bit_rate', 0) or 0) / 1000),
            'format_name':  fmt.get('format_name', '').split(',')[0],
            'tags':         tags,
        }

        if audio_stream:
            info['kanaele']     = audio_stream.get('channels', 0)
            info['samplerate']  = int(audio_stream.get('sample_rate', 0) or 0)
            info['audio_codec'] = audio_stream.get('codec_name', '')

        if video_stream:
            info['breite']      = video_stream.get('width', 0)
            info['hoehe']       = video_stream.get('height', 0)
            info['video_codec'] = video_stream.get('codec_name', '')
            info['framerate']   = video_stream.get('r_frame_rate', '')

        return info

    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return {}


def format_dauer(sek: float) -> str:
    """Formatiert Sekunden als m:ss oder h:mm:ss."""
    sek = int(sek)
    h, rest = divmod(sek, 3600)
    m, s    = divmod(rest, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def sha256(path: Path, chunk=65536) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()[:16]   # Kurz-Hash reicht für Deduplizierung


def medientyp(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in AUDIO_EXTS: return 'audio'
    if ext in VIDEO_EXTS: return 'video'
    if ext in IMAGE_EXTS: return 'bild'
    return 'unbekannt'


def audio_kategorie(dauer_sek: float) -> str:
    if dauer_sek <= SFX_MAX:   return 'sfx'
    if dauer_sek <= STING_MAX: return 'stings'
    return 'musik_ambience'


def kategorie(path: Path, dauer_sek: float) -> str:
    typ = medientyp(path)
    if typ == 'audio': return audio_kategorie(dauer_sek)
    if typ == 'video': return 'video'
    if typ == 'bild':  return 'bilder'
    return 'sonstige'


def ziel_pfad(base: Path, path: Path, dauer_sek: float) -> Path:
    """Berechnet den Zielpfad in der sortierten Medienbibliothek."""
    typ = medientyp(path)
    kat = kategorie(path, dauer_sek)
    return base / kat / path.name


# ---------------------------------------------------------------------------
# QLab-Workspace-Erkennung
# ---------------------------------------------------------------------------

def find_workspaces(base: Path) -> dict:
    """
    Gibt dict zurück: Verzeichnis → Liste von Workspace-Namen in diesem Verzeichnis.
    Berücksichtigt, dass QLab-Bundles selbst Verzeichnisse sind.
    """
    workspaces: dict[Path, list] = {}
    for ws in base.rglob('*'):
        if ws.suffix.lower() in QLAB_EXTENSIONS:
            # Das übergeordnete Verzeichnis
            parent = ws.parent
            name   = ws.stem   # Projektname ohne Endung
            workspaces.setdefault(parent, []).append(name)
            # Auch das Workspace-Bundle selbst (falls Medien drin sind)
            workspaces.setdefault(ws, []).append(name)
    return workspaces


def projekte_fuer_datei(path: Path, workspaces: dict) -> list:
    """
    Ermittelt die QLab-Projekte, denen eine Mediendatei zugeordnet ist.
    Prüft alle Elternverzeichnisse der Datei.
    """
    projekte = set()
    for parent in [path.parent] + list(path.parents):
        if parent in workspaces:
            projekte.update(workspaces[parent])
        if parent == QLAB_BACKUP_DIR:
            break
    return sorted(projekte) if projekte else ['(unbekannt)']


# ---------------------------------------------------------------------------
# Hauptlogik: Scannen und Katalog erstellen
# ---------------------------------------------------------------------------

def scan(dry_run: bool = False) -> list:
    """
    Scannt QLAB_BACKUP_DIR, sammelt alle Mediendateien,
    dedupliziert und erstellt Katalog-Einträge.
    """
    if not QLAB_BACKUP_DIR.exists():
        print(f'Backup-Verzeichnis nicht gefunden: {QLAB_BACKUP_DIR}', file=sys.stderr)
        print('→ Syncthing einrichten und QLab-Projekte vom Mac synchronisieren.')
        sys.exit(1)

    print(f'Scanne {QLAB_BACKUP_DIR} …')
    workspaces = find_workspaces(QLAB_BACKUP_DIR)
    print(f'  {sum(len(v) for v in workspaces.values())} QLab-Workspace(s) gefunden')

    # Existierenden Katalog laden (für inkrementelle Updates)
    bestehend: dict = {}
    if KATALOG_PATH.exists():
        try:
            for eintrag in json.loads(KATALOG_PATH.read_text('utf-8')):
                bestehend[eintrag['hash']] = eintrag
        except Exception:
            pass

    alle_hashes:    set  = set()
    katalog:        list = []
    stats = {'neu': 0, 'unveraendert': 0, 'portrait': 0, 'fehler': 0}

    for media_path in sorted(QLAB_BACKUP_DIR.rglob('*')):
        if media_path.suffix.lower() not in MEDIA_EXTS:
            continue
        if not media_path.is_file():
            continue
        # Dateien innerhalb von Syncthing-Metadaten überspringen
        if '.stfolder' in media_path.parts or '.stversions' in media_path.parts:
            continue

        file_hash = sha256(media_path)

        # Duplikat: schon in diesem Scan gesehen?
        if file_hash in alle_hashes:
            continue
        alle_hashes.add(file_hash)

        projekte = projekte_fuer_datei(media_path, workspaces)

        # Unveränderter Eintrag aus bestehendem Katalog?
        if file_hash in bestehend:
            eintrag = bestehend[file_hash]
            # Projekte aktualisieren (könnte in neuem Projekt aufgetaucht sein)
            eintrag['projekte'] = sorted(set(eintrag.get('projekte', [])) | set(projekte))
            katalog.append(eintrag)
            stats['unveraendert'] += 1
            continue

        # Neu: Metadaten per ffprobe extrahieren
        meta = ffprobe(media_path)
        if not meta:
            stats['fehler'] += 1
            continue

        dauer  = meta.get('dauer_sek', 0)
        kat    = kategorie(media_path, dauer)
        typ    = medientyp(media_path)
        ziel   = ziel_pfad(QLAB_MEDIA_DIR, media_path, dauer)

        eintrag = {
            'id':               file_hash,
            'hash':             file_hash,
            'dateiname':        media_path.name,
            'pfad_relativ':     str(ziel.relative_to(QLAB_MEDIA_DIR)),
            'typ':              typ,
            'kategorie':        kat,
            'dauer_sek':        dauer,
            'dauer_formatiert': format_dauer(dauer),
            'groesse_bytes':    meta.get('groesse_bytes', 0),
            'bitrate_kbps':     meta.get('bitrate_kbps', 0),
            'format':           meta.get('format_name', ''),
            'audio_codec':      meta.get('audio_codec', ''),
            'video_codec':      meta.get('video_codec', ''),
            'kanaele':          meta.get('kanaele', 0),
            'samplerate':       meta.get('samplerate', 0),
            'breite':           meta.get('breite', 0),
            'hoehe':            meta.get('hoehe', 0),
            'framerate':        meta.get('framerate', ''),
            'tags': {
                k: v for k, v in meta.get('tags', {}).items()
                if k in ('title', 'artist', 'album', 'genre', 'comment',
                         'description', 'date', 'track')
            },
            'projekte':         projekte,
            'hinzugefuegt':     datetime.now().strftime('%Y-%m-%d'),
        }

        if dry_run:
            print(f'  NEU  {kat}/{media_path.name}'
                  f' ({format_dauer(dauer)}, {", ".join(projekte)})')
        else:
            # Datei in Bibliothek kopieren
            ziel.parent.mkdir(parents=True, exist_ok=True)
            if not ziel.exists():
                shutil.copy2(media_path, ziel)
                # Bei Namenskonflikt (andere Datei, gleicher Name): Hash-Suffix
            elif ziel.stat().st_size != media_path.stat().st_size:
                ziel = ziel.with_stem(f'{ziel.stem}_{file_hash}')
                eintrag['dateiname']   = ziel.name
                eintrag['pfad_relativ'] = str(ziel.relative_to(QLAB_MEDIA_DIR))
                shutil.copy2(media_path, ziel)

        katalog.append(eintrag)
        stats['neu'] += 1

    return katalog, stats


def main():
    parser = argparse.ArgumentParser(description='QLab-Medienbibliothek aufbauen')
    parser.add_argument('--dry-run', action='store_true', help='Nur anzeigen, nichts kopieren')
    parser.add_argument('--clean',   action='store_true', help='Bibliothek leeren und neu aufbauen')
    args = parser.parse_args()

    if args.clean and not args.dry_run:
        if QLAB_MEDIA_DIR.exists():
            for item in QLAB_MEDIA_DIR.iterdir():
                if item.name != 'katalog.json':
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
        print('Bibliothek geleert.')

    QLAB_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    katalog, stats = scan(dry_run=args.dry_run)

    if not args.dry_run:
        KATALOG_PATH.write_text(
            json.dumps(katalog, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        print(f'\nKatalog gespeichert: {KATALOG_PATH}')

    total = stats['neu'] + stats['unveraendert']
    print(f'\nErgebnis:')
    print(f'  {total} Dateien in der Bibliothek')
    print(f'  {stats["neu"]} neu hinzugefügt')
    print(f'  {stats["unveraendert"]} unverändert')
    if stats['fehler']:
        print(f'  {stats["fehler"]} Fehler (ffprobe)')

    # Kategorien-Übersicht
    from collections import Counter
    cats = Counter(e['kategorie'] for e in katalog)
    print(f'\nNach Kategorie:')
    for kat, n in sorted(cats.items()):
        print(f'  {kat:20s} {n:4d}')


if __name__ == '__main__':
    main()
