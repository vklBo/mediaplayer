#!/bin/bash
# =============================================================================
# server_setup.sh – Dell Optiplex (Ubuntu/Debian) als Medienserver einrichten
#
# Was dieses Script tut:
#   1. Pakete installieren (rclone, syncthing, python3-deps, nfs)
#   2. /srv/media anlegen und per NFS exportieren (optional, für LAN-Geräte)
#   3. Syncthing als systemd-Service einrichten (Verteilung an Pis)
#   4. OneDrive-Sync als Boot-Service einrichten (niedriger Priorität)
#   5. Watchdog-Service einrichten (Server fährt runter wenn keine Pis aktiv
#      UND Sync + Syncthing-Übertragung abgeschlossen sind)
#
# Nach dem Script noch manuell nötig:
#   a) rclone config   → OneDrive/SharePoint-Konto verbinden
#   b) sync_onedrive.py RCLONE_PATH anpassen
#   c) Syncthing-Geräte-IDs mit den Pis tauschen (Web-UI: http://localhost:8384)
#   d) PI_IPS in /etc/taf/watchdog.conf eintragen
# =============================================================================

set -e

# ---------------------------------------------------------------------------
# Konfiguration – hier anpassen
# ---------------------------------------------------------------------------

MEDIA_DIR="/srv/media"
TAF_USER="${SUDO_USER:-taf}"          # Benutzer unter dem der Service läuft
SYNC_SCRIPT_DIR="/home/$TAF_USER/mediaplayer"

# ---------------------------------------------------------------------------
# Voraussetzungen prüfen
# ---------------------------------------------------------------------------

if [ "$EUID" -ne 0 ]; then
    echo "Bitte als root ausführen: sudo bash server_setup.sh"
    exit 1
fi

echo "=== TaF Medienserver Setup ==="
echo "Benutzer: $TAF_USER"
echo "Medienordner: $MEDIA_DIR"
echo ""

# ---------------------------------------------------------------------------
# 1. Pakete installieren
# ---------------------------------------------------------------------------

echo "[1/6] Pakete installieren..."
apt-get update -qq
apt-get install -y \
    openssh-server \
    rclone \
    syncthing \
    nfs-kernel-server \
    python3-pip \
    python3-opencv \
    ffmpeg \
    wakeonlan \
    curl

# SSH aktivieren und starten
systemctl enable ssh
systemctl start  ssh
echo "      ✓ SSH aktiviert (Port 22)"

# Python-Bibliotheken (Mediaplayer + QLab-Kollektor + Web-UI)
pip3 install --break-system-packages pillow imagehash flask 2>/dev/null || \
pip3 install pillow imagehash flask

echo "      ✓ Pakete installiert"

# ---------------------------------------------------------------------------
# 2. Medienordner anlegen
# ---------------------------------------------------------------------------

echo "[2/6] Medienordner anlegen..."
mkdir -p "$MEDIA_DIR"
chown "$TAF_USER:$TAF_USER" "$MEDIA_DIR"
chmod 755 "$MEDIA_DIR"

# Grundstock-Ordner (Sponsor-/Dauerbilder) – wird per Syncthing bidirektional
# mit allen Pis synchronisiert (Send & Receive, nicht nur Send wie bei media)
mkdir -p /srv/basismedien
chown "$TAF_USER:$TAF_USER" /srv/basismedien
chmod 755 /srv/basismedien

echo "      ✓ $MEDIA_DIR angelegt"
echo "      ✓ /srv/basismedien angelegt"

# Staging-Ordner für absturzsicheren Sync (gleiches Dateisystem wie /srv/media!)
mkdir -p /srv/media_staging
chown "$TAF_USER:$TAF_USER" /srv/media_staging
chmod 755 /srv/media_staging

# QLab-Backup und Medienbibliothek
mkdir -p /srv/qlab_backup
mkdir -p /srv/qlab_media
chown "$TAF_USER:$TAF_USER" /srv/qlab_backup /srv/qlab_media
chmod 755 /srv/qlab_backup /srv/qlab_media
echo "      ✓ /srv/qlab_backup angelegt  (Syncthing: vom Mac empfangen)"
echo "      ✓ /srv/qlab_media angelegt   (aufgeräumte Bibliothek)"

# ---------------------------------------------------------------------------
# 3. NFS exportieren (optional – für kabelgebundene Geräte im Netz)
# ---------------------------------------------------------------------------

echo "[3/6] NFS konfigurieren..."
NETWORK=$(ip route | grep -m1 "src" | awk '{print $1}' || echo "192.168.1.0/24")

if ! grep -q "$MEDIA_DIR" /etc/exports 2>/dev/null; then
    echo "$MEDIA_DIR  $NETWORK(ro,sync,no_subtree_check,no_root_squash)" >> /etc/exports
    exportfs -ra
    systemctl enable nfs-kernel-server
    systemctl start nfs-kernel-server
    echo "      ✓ NFS Export: $MEDIA_DIR → $NETWORK"
else
    echo "      ✓ NFS bereits konfiguriert"
fi

# ---------------------------------------------------------------------------
# 4. Syncthing einrichten
# ---------------------------------------------------------------------------

echo "[4/6] Syncthing einrichten..."
# Als Benutzer-Service laufen lassen
systemctl enable "syncthing@$TAF_USER"
systemctl start  "syncthing@$TAF_USER"

# Kurz warten damit Syncthing seine Config generiert
sleep 3

echo "      ✓ Syncthing läuft (Web-UI: http://$(hostname -I | awk '{print $1}'):8384)"
echo "      → Ordner 1: $MEDIA_DIR         → Typ: 'Nur senden' (Server → Pis)"
echo "      → Ordner 2: /srv/basismedien   → Typ: 'Senden & Empfangen' (bidirektional)"
echo "      → Geräte-ID notieren und mit Pi-IDs tauschen"

# ---------------------------------------------------------------------------
# 5. Watchdog-Konfiguration anlegen
# ---------------------------------------------------------------------------

echo "[5/6] Watchdog einrichten..."
mkdir -p /etc/taf

cat > /etc/taf/watchdog.conf <<'CONF'
# Watchdog-Konfiguration
# IP-Adressen aller Geräte, die den Server wach halten (Pis + MacBook)
# Server fährt herunter wenn KEINES dieser Geräte mehr erreichbar ist.
# Hostnamen funktionieren auch (z.B. macbook.local)
# Der Server fährt herunter wenn keiner dieser Hosts seit TIMEOUT Minuten erreichbar war.

#192.168.1.102    # Pi 1
#192.168.1.105    # Pi 2
#192.168.1.110    # MacBook (feste IP oder DHCP-Reservierung empfohlen)
#macbook.local    # Alternative: mDNS-Hostname

TIMEOUT_MINUTES=15
CONF

# Watchdog-Script installieren
cp "$(dirname "$0")/server_watchdog.sh" /usr/local/bin/taf_watchdog.sh
chmod +x /usr/local/bin/taf_watchdog.sh

# systemd-Timer
cat > /etc/systemd/system/taf-watchdog.service <<SERVICE
[Unit]
Description=TaF Medienserver Watchdog – Shutdown wenn keine Pis aktiv

[Service]
Type=oneshot
ExecStart=/usr/local/bin/taf_watchdog.sh
SERVICE

cat > /etc/systemd/system/taf-watchdog.timer <<TIMER
[Unit]
Description=TaF Watchdog alle 5 Minuten

[Timer]
OnBootSec=10min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
systemctl enable taf-watchdog.timer
systemctl start  taf-watchdog.timer
echo "      ✓ Watchdog-Timer aktiv (alle 5 Min)"
echo "      → Pi-IPs in /etc/taf/watchdog.conf eintragen!"

# ---------------------------------------------------------------------------
# 6. OneDrive-Sync als Boot-Service einrichten (niedrige Priorität)
# ---------------------------------------------------------------------------

echo "[6/6] Sync-Boot-Service einrichten..."

touch /var/log/taf_sync.log
chown "$TAF_USER:$TAF_USER" /var/log/taf_sync.log

# Update-Service: stellt das Repo beim Boot auf die neueste Release um.
# Läuft VOR Sync und Web-UI, damit alle Dienste den neuen Code verwenden.
cat > /etc/systemd/system/taf-update.service <<SERVICE
[Unit]
Description=TaF Update auf neueste Release (beim Boot)
After=network-online.target
Wants=network-online.target
Before=taf-sync.service taf-qlab-web.service

[Service]
Type=oneshot
User=$TAF_USER
ExecStart=$SYNC_SCRIPT_DIR/update_to_release.sh
RemainAfterExit=yes
StandardOutput=append:/var/log/taf_sync.log
StandardError=append:/var/log/taf_sync.log

[Install]
WantedBy=multi-user.target
SERVICE

systemctl enable taf-update.service
echo "      ✓ Update-Service aktiviert (zieht neueste Release beim Boot)"

cat > /etc/systemd/system/taf-sync.service <<SERVICE
[Unit]
Description=TaF OneDrive-Sync (startet nach dem Boot)
After=network-online.target syncthing@${TAF_USER}.service taf-update.service
Wants=network-online.target
# Syncthing (Pi-Verteilung) hat Vorrang – Sync startet erst danach

[Service]
Type=oneshot
User=$TAF_USER
# Niedrige CPU- und I/O-Priorität: Syncthing-Übertragungen an Pis haben Vorrang
Nice=15
IOSchedulingClass=best-effort
IOSchedulingPriority=7
ExecStart=python3 $SYNC_SCRIPT_DIR/sync_onedrive.py
StandardOutput=append:/var/log/taf_sync.log
StandardError=append:/var/log/taf_sync.log
TimeoutStartSec=3600

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable taf-sync.service
echo "      ✓ Sync-Service aktiviert (startet nach jedem Boot, niedrige Priorität)"
echo "      → Log: /var/log/taf_sync.log"
echo "      → Manuell starten: sudo systemctl start taf-sync.service"

# QLab Web-UI als Service
cat > /etc/systemd/system/taf-qlab-web.service <<SERVICE
[Unit]
Description=TaF QLab Medienbibliothek Web-UI
After=network.target

[Service]
Type=simple
User=$TAF_USER
ExecStart=python3 $SYNC_SCRIPT_DIR/qlab_web.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable taf-qlab-web.service
systemctl start  taf-qlab-web.service
echo "      ✓ QLab Web-UI aktiviert (http://$(hostname -I | awk '{print $1}'):5000)"

# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------

SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "========================================="
echo "  Setup abgeschlossen!"
echo "========================================="
echo ""
echo "Noch manuell nötig:"
echo ""
echo "  1. OneDrive verbinden:"
echo "     rclone config"
echo "     → Typ: Microsoft OneDrive"
echo "     → RCLONE_PATH in sync_onedrive.py anpassen"
echo ""
echo "  2. Pi-IPs in Watchdog eintragen:"
echo "     nano /etc/taf/watchdog.conf"
echo ""
echo "  3. Syncthing mit Pis koppeln:"
echo "     http://$SERVER_IP:8384"
echo "     → Gerät hinzufügen → Pi-Geräte-ID eingeben"
echo "     → Ordner $MEDIA_DIR teilen          (Typ: Nur senden)"
echo "     → Ordner /srv/basismedien teilen    (Typ: Senden & Empfangen)"
echo "     → Ordner /srv/qlab_backup teilen    (Typ: Nur empfangen, vom Mac)"
echo ""
echo "  4. Ersten Sync manuell starten:"
echo "     python3 $SYNC_SCRIPT_DIR/sync_onedrive.py --list-folders"
echo "     # excluded_folders.txt bearbeiten"
echo "     python3 $SYNC_SCRIPT_DIR/sync_onedrive.py"
echo ""
echo "  5. WoL auf diesem Rechner im BIOS aktivieren"
echo "     (Kabelverbindung zum UDM-SE nötig)"
echo ""
