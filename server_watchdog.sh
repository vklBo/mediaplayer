#!/bin/bash
# =============================================================================
# server_watchdog.sh – Server fährt herunter wenn keine Pis erreichbar sind
#
# Wird als systemd-Timer alle 5 Minuten ausgeführt.
# Merkt sich in /tmp/taf_last_pi_seen wann zuletzt ein Pi geantwortet hat.
# Wenn kein Pi seit TIMEOUT_MINUTES erreichbar → shutdown.
# =============================================================================

CONFIG_FILE="/etc/taf/watchdog.conf"
LAST_SEEN_FILE="/tmp/taf_last_pi_seen"
LOG_PREFIX="[TaF Watchdog]"

# ---------------------------------------------------------------------------
# Konfiguration laden
# ---------------------------------------------------------------------------

if [ ! -f "$CONFIG_FILE" ]; then
    echo "$LOG_PREFIX Keine Konfiguration unter $CONFIG_FILE"
    exit 0
fi

# Timeout aus Config lesen (Standard: 15 Minuten)
TIMEOUT_MINUTES=$(grep "^TIMEOUT_MINUTES=" "$CONFIG_FILE" | cut -d= -f2 | tr -d ' ')
TIMEOUT_MINUTES=${TIMEOUT_MINUTES:-15}

# Pi-IPs aus Config lesen (Zeilen ohne # und ohne TIMEOUT)
PI_IPS=$(grep -v "^#" "$CONFIG_FILE" | grep -v "^$" | grep -v "^TIMEOUT" | grep -v "^$")

if [ -z "$PI_IPS" ]; then
    echo "$LOG_PREFIX Keine Pi-IPs konfiguriert – Watchdog inaktiv"
    exit 0
fi

# ---------------------------------------------------------------------------
# Pis anpingen
# ---------------------------------------------------------------------------

PI_FOUND=0

for IP in $PI_IPS; do
    if ping -c 1 -W 2 "$IP" > /dev/null 2>&1; then
        echo "$LOG_PREFIX Pi erreichbar: $IP"
        PI_FOUND=1
        break
    fi
done

# ---------------------------------------------------------------------------
# Zeitstempel aktualisieren oder Timeout prüfen
# ---------------------------------------------------------------------------

NOW=$(date +%s)

if [ "$PI_FOUND" -eq 1 ]; then
    echo "$NOW" > "$LAST_SEEN_FILE"
    exit 0
fi

# Kein Pi erreichbar – wann war der letzte?
if [ ! -f "$LAST_SEEN_FILE" ]; then
    # Erste Prüfung ohne Pi → Zeitstempel setzen, noch nicht herunterfahren
    echo "$NOW" > "$LAST_SEEN_FILE"
    echo "$LOG_PREFIX Kein Pi erreichbar – starte Countdown ($TIMEOUT_MINUTES Min)"
    exit 0
fi

LAST_SEEN=$(cat "$LAST_SEEN_FILE")
ELAPSED_MINUTES=$(( (NOW - LAST_SEEN) / 60 ))

echo "$LOG_PREFIX Kein Pi seit $ELAPSED_MINUTES Min erreichbar (Limit: $TIMEOUT_MINUTES Min)"

if [ "$ELAPSED_MINUTES" -ge "$TIMEOUT_MINUTES" ]; then
    echo "$LOG_PREFIX Timeout erreicht – Server fährt herunter"
    # Syncthing sauber beenden
    systemctl stop "syncthing@$(logname 2>/dev/null || echo taf)" 2>/dev/null || true
    # Kurz warten
    sleep 5
    /sbin/shutdown -h now "TaF Watchdog: Keine Pis aktiv"
fi
