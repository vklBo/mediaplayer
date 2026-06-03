#!/bin/bash
# Doppelklick auf diese Datei startet den Medienserver per Wake-on-LAN.

SERVER_MAC="50:9A:4C:49:37:29"
SERVER_IP="192.168.1.100"   # ← Server-IP hier eintragen

# Wake-on-LAN senden (kein extra Tool nötig)
echo "Sende Wake-on-LAN an $SERVER_MAC ..."
perl -e "
use Socket;
socket(S, AF_INET, SOCK_DGRAM, getprotobyname('udp')) or die;
setsockopt(S, SOL_SOCKET, SO_BROADCAST, 1) or die;
my \$mac = pack('H*', '$(echo $SERVER_MAC | tr -d ':')');
my \$pkt = chr(0xff) x 6 . \$mac x 16;
my \$addr = sockaddr_in(9, INADDR_BROADCAST);
send(S, \$pkt, 0, \$addr) for 1..3;
"

# Prüfen ob Server antwortet
echo "Warte auf Server $SERVER_IP ..."
for i in {1..12}; do
    if ping -c 1 -W 2000 "$SERVER_IP" > /dev/null 2>&1; then
        echo "✓ Server ist erreichbar!"
        exit 0
    fi
    echo "  ... ($((i*5))s)"
    sleep 5
done

echo "⚠ Server nach 60s noch nicht erreichbar – WoL-Einstellungen prüfen."
