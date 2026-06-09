#!/bin/bash
# =============================================================================
# mac_wol.sh – Medienserver per Wake-on-LAN wecken (läuft auf dem MacBook)
#
# Benötigt keine extra Tools – nutzt perl (auf jedem Mac vorhanden).
# Wird via launchd alle 5 Minuten und beim Login/Aufwachen ausgeführt.
#
# Einrichtung: bash mac_wol_setup.sh
# Manuell:     bash mac_wol.sh
# =============================================================================

# ---------------------------------------------------------------------------
# Konfiguration – hier anpassen
# ---------------------------------------------------------------------------

# MAC-Adresse des Medienservers (Dell Optiplex)
# Auf dem Server herausfinden mit: ip link show
SERVER_MAC="50:9A:4C:49:37:29"

# IP-Adresse des Medienservers
SERVER_IP="192.168.1.200"

# ---------------------------------------------------------------------------
# Wake-on-LAN per perl (kein extra Tool nötig)
# ---------------------------------------------------------------------------

send_wol() {
    local MAC
    MAC=$(echo "$1" | tr -d ':- ' | tr '[:upper:]' '[:lower:]')
    perl -e "
use Socket;
socket(S, AF_INET, SOCK_DGRAM, getprotobyname('udp')) or die;
setsockopt(S, SOL_SOCKET, SO_BROADCAST, 1) or die;
my \$mac = pack('H*', '$MAC');
my \$pkt = chr(0xff) x 6 . \$mac x 16;
my \$addr = sockaddr_in(9, INADDR_BROADCAST);
send(S, \$pkt, 0, \$addr) for 1..3;
print 'WoL gesendet.\n';
"
}

# ---------------------------------------------------------------------------
# Prüfen ob Server erreichbar
# ---------------------------------------------------------------------------

if ping -c 1 -W 3000 "$SERVER_IP" > /dev/null 2>&1; then
    echo "$(date): Server $SERVER_IP bereits erreichbar – kein WoL nötig."
    exit 0
fi

echo "$(date): Server $SERVER_IP nicht erreichbar – sende WoL …"

if [ "$SERVER_MAC" = "AA:BB:CC:DD:EE:FF" ]; then
    echo "SERVER_MAC nicht konfiguriert. Bitte in mac_wol.sh eintragen."
    exit 1
fi

send_wol "$SERVER_MAC"
