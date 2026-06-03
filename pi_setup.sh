#!/bin/bash
# =============================================================================
# pi_setup.sh – Raspberry Pi als Touch-Kiosk einrichten
#
# Was dieses Script tut:
#   1. Pakete installieren (syncthing, wakeonlan, kivy, pillow)
#   2. Syncthing einrichten (empfängt Medien vom Server)
#   3. WoL-Boot-Service einrichten (weckt Server beim Pi-Start)
#   4. mediaplayer-Service einrichten (startet App nach Syncthing-Sync)
#
# Nach dem Script noch manuell nötig:
#   a) SERVER_MAC und SERVER_IP in /etc/taf/pi.conf eintragen
#   b) Syncthing-Geräte-IDs mit Server tauschen
#      (Web-UI: http://<Pi-IP>:8384 oder http://localhost:8384)
# =============================================================================

set -e

TAF_USER="${SUDO_USER:-taf}"
SCRIPT_DIR="/home/$TAF_USER"

if [ "$EUID" -ne 0 ]; then
    echo "Bitte als root ausführen: sudo bash pi_setup.sh"
    exit 1
fi

echo "=== TaF Pi Setup ==="
echo "Benutzer: $TAF_USER"
echo ""

# ---------------------------------------------------------------------------
# 1. Pakete installieren
# ---------------------------------------------------------------------------

echo "[1/5] Pakete installieren..."
apt-get update -qq
apt-get install -y \
    syncthing \
    wakeonlan \
    python3-pip \
    libsdl2-dev libsdl2-image-dev libsdl2-ttf-dev libsdl2-mixer-dev \
    libmtdev-dev libgl1-mesa-dev \
    feh lshw

pip3 install --break-system-packages kivy[base] pillow 2>/dev/null || \
pip3 install kivy[base] pillow

echo "      ✓ Pakete installiert"

# ---------------------------------------------------------------------------
# 2. Pi-Konfiguration anlegen
# ---------------------------------------------------------------------------

echo "[2/5] Konfiguration anlegen..."
mkdir -p /etc/taf

cat > /etc/taf/pi.conf <<'CONF'
# TaF Pi-Konfiguration
# MAC-Adresse des Medienservers (Dell Optiplex) – für Wake-on-LAN
SERVER_MAC="AA:BB:CC:DD:EE:FF"

# IP-Adresse des Medienservers
SERVER_IP="192.168.1.100"

# Timeout in Sekunden: wie lange auf Server warten (0 = kein Warten)
SERVER_WAIT_TIMEOUT=60
CONF

echo "      ✓ /etc/taf/pi.conf angelegt"
echo "      → SERVER_MAC und SERVER_IP eintragen!"

# ---------------------------------------------------------------------------
# 3. WoL + Warte-Service einrichten
# ---------------------------------------------------------------------------

echo "[3/5] WoL-Service einrichten..."

cat > /usr/local/bin/taf_wol_wait.sh <<'SCRIPT'
#!/bin/bash
# Sendet Wake-on-LAN an den Medienserver und wartet bis er erreichbar ist.

CONFIG_FILE="/etc/taf/pi.conf"
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

SERVER_MAC="${SERVER_MAC:-}"
SERVER_IP="${SERVER_IP:-}"
SERVER_WAIT_TIMEOUT="${SERVER_WAIT_TIMEOUT:-60}"

if [ -z "$SERVER_MAC" ] || [ "$SERVER_MAC" = "AA:BB:CC:DD:EE:FF" ]; then
    echo "SERVER_MAC nicht konfiguriert – kein WoL"
    exit 0
fi

echo "Sende Wake-on-LAN an $SERVER_MAC ..."
# Mehrfach senden (WLAN-Broadcast kann verloren gehen)
wakeonlan "$SERVER_MAC" 2>/dev/null || true
sleep 2
wakeonlan "$SERVER_MAC" 2>/dev/null || true
sleep 2
wakeonlan "$SERVER_MAC" 2>/dev/null || true

if [ -z "$SERVER_IP" ] || [ "$SERVER_WAIT_TIMEOUT" -eq 0 ]; then
    exit 0
fi

echo "Warte auf Server $SERVER_IP (max. ${SERVER_WAIT_TIMEOUT}s) ..."
ELAPSED=0
while [ $ELAPSED -lt $SERVER_WAIT_TIMEOUT ]; do
    if ping -c 1 -W 2 "$SERVER_IP" > /dev/null 2>&1; then
        echo "Server erreichbar nach ${ELAPSED}s"
        exit 0
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

echo "Server nicht erreichbar nach ${SERVER_WAIT_TIMEOUT}s – starte trotzdem"
exit 0
SCRIPT

chmod +x /usr/local/bin/taf_wol_wait.sh

cat > /etc/systemd/system/taf-wol.service <<SERVICE
[Unit]
Description=TaF Wake-on-LAN – Medienserver aufwecken
After=network-online.target
Wants=network-online.target
Before=syncthing@${TAF_USER}.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/taf_wol_wait.sh
RemainAfterExit=yes
User=root

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable taf-wol.service
echo "      ✓ WoL-Service aktiviert"

# ---------------------------------------------------------------------------
# 4. Syncthing einrichten
# ---------------------------------------------------------------------------

echo "[4/5] Syncthing einrichten..."

# Verzeichnisse anlegen
mkdir -p "/home/$TAF_USER/media"
mkdir -p "/home/$TAF_USER/basismedien"
chown "$TAF_USER:$TAF_USER" "/home/$TAF_USER/media" "/home/$TAF_USER/basismedien"

systemctl enable "syncthing@$TAF_USER"
systemctl start  "syncthing@$TAF_USER"
sleep 3

PI_IP=$(hostname -I | awk '{print $1}')
echo "      ✓ Syncthing läuft"
echo "      → Web-UI: http://$PI_IP:8384"
echo "      → Geräte-ID notieren, auf Server eintragen"
echo "      → Ordner 1: ~/media        ← vom Server (Nur empfangen)"
echo "      → Ordner 2: ~/basismedien  ↔ bidirektional mit Server (Senden & Empfangen)"
echo "         (USB-Stick mit basismedien/ → wird automatisch an alle Pis verteilt)"

# ---------------------------------------------------------------------------
# 5. mediaplayer-Service einrichten
# ---------------------------------------------------------------------------

echo "[5/5] mediaplayer-Service einrichten..."

cat > /etc/systemd/system/taf_service.service <<SERVICE
[Unit]
Description=TaF Interaktiver Mediaplayer
After=taf-wol.service syncthing@${TAF_USER}.service network-online.target
Wants=syncthing@${TAF_USER}.service

[Service]
Type=simple
User=$TAF_USER
# KMS/DRM: kein X-Server nötig
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=KIVY_WINDOW=sdl2
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStart=python3 /home/$TAF_USER/mediaplayer_app.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable taf_service.service
echo "      ✓ mediaplayer-Service aktiviert"

# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------

echo ""
echo "========================================="
echo "  Setup abgeschlossen!"
echo "========================================="
echo ""
echo "Noch manuell nötig:"
echo ""
echo "  1. Server-MAC und IP eintragen:"
echo "     nano /etc/taf/pi.conf"
echo ""
echo "  2. Syncthing mit Server koppeln:"
echo "     http://$PI_IP:8384"
echo "     → Gerät hinzufügen → Server-Geräte-ID eingeben"
echo "     → Geteilten Ordner (~/media) akzeptieren"
echo ""
echo "  3. Neustart:"
echo "     sudo reboot"
echo ""
echo "  Testen:"
echo "     sudo systemctl start taf_service.service"
echo "     journalctl -u taf_service.service -f"
echo ""
