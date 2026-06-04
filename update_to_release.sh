#!/bin/bash
# =============================================================================
# update_to_release.sh – Aktualisiert das Repo beim Start auf das neueste Release
#
# Eine "Release" ist ein Git-Tag der Form vX.Y.Z (z.B. v1.2.0).
# Das Skript wird beim Gerätestart ausgeführt (nicht zwischendurch) und stellt
# das Arbeitsverzeichnis auf das höchste verfügbare Release-Tag um.
#
# Robust: bricht den Start NIE ab (immer exit 0). Bei fehlendem Netz, fehlenden
# Tags oder lokalen Konflikten bleibt die aktuell installierte Version aktiv.
#
# Lokale, per .gitignore ausgeschlossene Dateien (excluded_folders.txt,
# genres.txt, rclone-Config) bleiben unberührt.
# =============================================================================

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR" || exit 0

log() { echo "[TaF Update] $*"; }

# timeout-Wrapper: nutzt timeout (Linux), fällt sonst auf direkten Aufruf zurück
run_to() {
    local t=$1; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$t" "$@"
    else
        "$@"
    fi
}

# Kein Git-Repo? → nichts tun
if [ ! -d .git ]; then
    log "Kein Git-Repo in $REPO_DIR – überspringe Update."
    exit 0
fi

# Remote erreichbar? (kurzer Timeout, damit der Start nicht hängt)
if ! run_to 15 git ls-remote --exit-code origin >/dev/null 2>&1; then
    log "Remote nicht erreichbar – behalte aktuelle Version."
    exit 0
fi

# Tags holen
run_to 30 git fetch --tags --quiet origin 2>/dev/null || {
    log "git fetch fehlgeschlagen – behalte aktuelle Version."
    exit 0
}

# Höchstes Release-Tag (vX.Y.Z) nach Versionsnummer
LATEST=$(git tag -l 'v*' | sort -V | tail -1)
if [ -z "$LATEST" ]; then
    log "Keine Release-Tags (v*) vorhanden – behalte aktuelle Version."
    exit 0
fi

# Aktuell ausgechecktes Tag (falls vorhanden)
CURRENT=$(git describe --tags --exact-match 2>/dev/null || echo "")

if [ "$CURRENT" = "$LATEST" ]; then
    log "Bereits auf neuestem Release: $LATEST"
    exit 0
fi

log "Neue Release verfügbar: $LATEST (aktuell: ${CURRENT:-unbekannt})"

# Auf das Release-Tag umstellen. Schlägt es fehl (z.B. lokale Änderungen an
# getrackten Dateien), bleibt die aktuelle Version aktiv – Start läuft weiter.
if git checkout --quiet "$LATEST" 2>/dev/null; then
    log "Erfolgreich auf $LATEST aktualisiert."
else
    log "Wechsel auf $LATEST fehlgeschlagen (lokale Änderungen?) – behalte aktuelle Version."
fi

exit 0
