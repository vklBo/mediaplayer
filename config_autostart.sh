#!/bin/bash

sudo apt-get -y update
sudo apt-get -y upgrade
sudo apt-get -y install feh lshw

# Schritt 1: Erstelle eine Service-Datei
cat <<EOF | sudo tee /etc/systemd/system/taf_service.service >/dev/null
[Unit]
Description=TaF Anzeige von Bildern und Videos
After=network.target

[Service]
Type=simple
User=taf
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/taf/.Xauthority
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStart=python3 /home/taf/show_media_main.py

[Install]
WantedBy=multi-user.target
EOF

# Schritt 2: Lade den Service und aktiviere ihn
sudo systemctl daemon-reload
sudo systemctl enable taf_service.service

echo "Systemd-Service für das Ausführen von Prozessen beim Systemstart wurde eingerichtet."
