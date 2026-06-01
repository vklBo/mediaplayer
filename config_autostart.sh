#!/bin/bash

sudo apt-get -y update
sudo apt-get -y upgrade
sudo apt-get -y install feh lshw python3-pip python3-dev libsdl2-dev \
    libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
    libmtdev-dev libgl1-mesa-dev

# Kivy und Pillow (für Thumbnail-Generierung)
pip3 install --break-system-packages kivy[base] pillow

# Schritt 1: Erstelle eine Service-Datei
cat <<EOF | sudo tee /etc/systemd/system/taf_service.service >/dev/null
[Unit]
Description=TaF Interaktiver Mediaplayer
After=local-fs.target

[Service]
Type=simple
User=taf
# KMS/DRM: kein X-Server nötig, direkte Ausgabe auf den Framebuffer
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=KIVY_WINDOW=sdl2
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStart=python3 /home/taf/mediaplayer_app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Schritt 2: Lade den Service und aktiviere ihn
sudo systemctl daemon-reload
sudo systemctl enable taf_service.service

echo "Systemd-Service eingerichtet. Starten mit: sudo systemctl start taf_service.service"
