#!/bin/bash
# =============================================================================
# server_watchdog.sh – Server fährt herunter wenn keine Pis mehr aktiv sind
#
# Bedingungen für Shutdown (ALLE müssen erfüllt sein):
#   1. Kein Pi seit TIMEOUT_MINUTES erreichbar
#   2. sync_onedrive.py läuft nicht mehr (Lock-Datei /tmp/taf_sync_running)
#   3. Syncthing hat keine ausstehenden Übertragungen mehr
#
# Wird als systemd-Timer alle 5 Minuten ausgeführt.
# =============================================================================

CONFIG_FILE="/etc/taf/watchdog.conf"
LAST_SEEN_FILE="/tmp/taf_last_pi_seen"
LOCK_FILE="/tmp/taf_sync_running"
LOG_PREFIX="[TaF Watchdog]"

# ---------------------------------------------------------------------------
# Konfiguration laden
# ---------------------------------------------------------------------------

if [ ! -f "$CONFIG_FILE" ]; then
    echo "$LOG_PREFIX Keine Konfiguration unter $CONFIG_FILE"
    exit 0
fi

source "$CONFIG_FILE"
TIMEOUT_MINUTES=${TIMEOUT_MINUTES:-15}

PI_IPS=$(grep -v "^#" "$CONFIG_FILE" | grep -v "^$" | grep -v "^TIMEOUT" | grep -E "^[0-9]")

if [ -z "$PI_IPS" ]; then
    echo "$LOG_PREFIX Keine Pi-IPs konfiguriert – Watchdog inaktiv"
    exit 0
fi

# ---------------------------------------------------------------------------
# Hilfsfunktion: Syncthing auf ausstehende Übertragungen prüfen
# ---------------------------------------------------------------------------

syncthing_is_busy() {
    local SYNCTHING_CONFIG
    SYNCTHING_CONFIG=$(find /home -name "config.xml" -path "*/syncthing/*" 2>/dev/null | head -1)

    if [ -z "$SYNCTHING_CONFIG" ]; then
        return 1  # Syncthing-Config nicht gefunden → annehmen: fertig
    fi

    local API_KEY
    API_KEY=$(grep -m1 "apikey" "$SYNCTHING_CONFIG" 2>/dev/null \
              | sed 's/.*<apikey>\(.*\)<\/apikey>.*/\1/')

    if [ -z "$API_KEY" ]; then
        return 1
    fi

    # Syncthing REST-API: Systemstatus abrufen
    local RESPONSE
    RESPONSE=$(curl -s --max-time 3 \
        -H "X-API-Key: $API_KEY" \
        "http://localhost:8384/rest/db/completion" 2>/dev/null)

    if [ -z "$RESPONSE" ]; then
        return 1  # API nicht erreichbar → annehmen: fertig
    fi

    # needBytes > 0 bedeutet: noch Daten ausstehend
    local NEED_BYTES
    NEED_BYTES=$(echo "$RESPONSE" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('needBytes',0))" 2>/dev/null || echo "0")

    [ "$NEED_BYTES" -gt 0 ]
}

# ---------------------------------------------------------------------------
# Pis anpingen
# ---------------------------------------------------------------------------

PI_FOUND=0
NOW=$(date +%s)

for IP in $PI_IPS; do
    if ping -c 1 -W 2 "$IP" > /dev/null 2>&1; then
        echo "$LOG_PREFIX Pi erreichbar: $IP"
        PI_FOUND=1
        break
    fi
done

if [ "$PI_FOUND" -eq 1 ]; then
    echo "$NOW" > "$LAST_SEEN_FILE"
    exit 0
fi

# ---------------------------------------------------------------------------
# Kein Pi erreichbar – Zeitstempel prüfen
# ---------------------------------------------------------------------------

if [ ! -f "$LAST_SEEN_FILE" ]; then
    echo "$NOW" > "$LAST_SEEN_FILE"
    echo "$LOG_PREFIX Kein Pi erreichbar – starte Countdown ($TIMEOUT_MINUTES Min)"
    exit 0
fi

LAST_SEEN=$(cat "$LAST_SEEN_FILE")
ELAPSED_MINUTES=$(( (NOW - LAST_SEEN) / 60 ))
echo "$LOG_PREFIX Kein Pi seit ${ELAPSED_MINUTES} Min (Limit: ${TIMEOUT_MINUTES} Min)"

if [ "$ELAPSED_MINUTES" -lt "$TIMEOUT_MINUTES" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Timeout erreicht – Shutdown-Bedingungen prüfen
# ---------------------------------------------------------------------------

# Bedingung 1: Läuft noch ein Sync?
if [ -f "$LOCK_FILE" ]; then
    echo "$LOG_PREFIX OneDrive-Sync läuft noch – Shutdown verschoben"
    # Timer nicht zurücksetzen: Pis sind weg, aber wir warten auf Sync
    exit 0
fi

# Bedingung 2: Überträgt Syncthing noch Daten an Pis?
if syncthing_is_busy; then
    echo "$LOG_PREFIX Syncthing überträgt noch – Shutdown verschoben"
    exit 0
fi

# ---------------------------------------------------------------------------
# Alle Bedingungen erfüllt → herunterfahren
# ---------------------------------------------------------------------------

echo "$LOG_PREFIX Alle Bedingungen erfüllt – Server fährt herunter"
systemctl stop "syncthing@$(logname 2>/dev/null || echo taf)" 2>/dev/null || true
sleep 5
/sbin/shutdown -h now "TaF Watchdog: Keine Pis aktiv, Sync abgeschlossen"
