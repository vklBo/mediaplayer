#!/bin/bash
# =============================================================================
# mac_wol_setup.sh – launchd-Agent einrichten (läuft auf dem MacBook)
#
# Richtet einen launchd-Agent ein, der mac_wol.sh automatisch ausführt:
#   - beim Login
#   - beim Aufwachen aus dem Ruhezustand (via sleepwatcher-Alternative)
#   - alle 5 Minuten als Fallback
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WOL_SCRIPT="$SCRIPT_DIR/mac_wol.sh"
PLIST="$HOME/Library/LaunchAgents/de.theateramfluss.server-wol.plist"
LOG="$HOME/Library/Logs/taf_server_wol.log"

if [ ! -f "$WOL_SCRIPT" ]; then
    echo "mac_wol.sh nicht gefunden unter $WOL_SCRIPT"
    exit 1
fi

chmod +x "$WOL_SCRIPT"

# ---------------------------------------------------------------------------
# Schritt 1: SERVER_MAC und SERVER_IP konfiguriert?
# ---------------------------------------------------------------------------

if grep -q 'AA:BB:CC:DD:EE:FF' "$WOL_SCRIPT"; then
    echo "⚠  SERVER_MAC ist noch nicht konfiguriert!"
    echo "   Bitte zuerst in mac_wol.sh eintragen:"
    echo "   SERVER_MAC='XX:XX:XX:XX:XX:XX'"
    echo "   SERVER_IP='192.168.1.XXX'"
    echo ""
    echo "   Tipp: MAC-Adresse auf dem Server herausfinden mit:"
    echo "   ssh taf@<SERVER-IP> ip link show"
    echo ""
    read -p "Trotzdem fortfahren? (j/N) " antwort
    [[ "$antwort" =~ ^[jJ]$ ]] || exit 0
fi

# ---------------------------------------------------------------------------
# Schritt 2: launchd-Plist erstellen
# ---------------------------------------------------------------------------

mkdir -p "$(dirname "$PLIST")"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>de.theateramfluss.server-wol</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${WOL_SCRIPT}</string>
    </array>

    <!-- Beim Login sofort ausführen -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Alle 5 Minuten wiederholen (hält Server wach + weckt nach Schlaf) -->
    <key>StartInterval</key>
    <integer>300</integer>

    <key>StandardOutPath</key>
    <string>${LOG}</string>
    <key>StandardErrorPath</key>
    <string>${LOG}</string>
</dict>
</plist>
PLIST

# ---------------------------------------------------------------------------
# Schritt 3: Agent laden
# ---------------------------------------------------------------------------

# Alten Agent ggf. entladen
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo ""
echo "✓ launchd-Agent eingerichtet."
echo "  → Startet beim Login und alle 5 Minuten"
echo "  → Log: $LOG"
echo ""
echo "Testen:"
echo "  bash $WOL_SCRIPT"
echo ""
echo "Deinstallieren:"
echo "  launchctl unload $PLIST && rm $PLIST"
