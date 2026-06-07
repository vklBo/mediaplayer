#!/bin/bash
# =============================================================================
# taf-pull.sh – Manuelles Update des mediaplayer-Repos auf den neuesten Stand
#
# Wechselt auf den main-Branch (aus dem detached HEAD den update_to_release.sh
# hinterlässt) und zieht die neuesten Änderungen von GitHub.
#
# Aufruf: ./taf-pull.sh
# =============================================================================

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR" || exit 1

echo "[TaF Pull] Wechsle auf Branch main …"
git checkout main || { echo "[TaF Pull] Fehler beim Branch-Wechsel."; exit 1; }

echo "[TaF Pull] Hole neuesten Stand von origin/main …"
git pull origin main || { echo "[TaF Pull] Fehler beim Pull."; exit 1; }

echo "[TaF Pull] Fertig. Aktueller Stand:"
git log --oneline -3
