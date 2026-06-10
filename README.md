# TaF Mediaplayer

Automatisches Bilderanzeigesystem für Touch-Displays des Theater am Fluss (TaF).
Zeigt Produktionsfotos geordnet nach Spielzeit und Produktion, mit Kurationsmodus zur Bildauswahl.

---

## Inhaltsverzeichnis

- [Architektur](#architektur)
- [Teil 1: Installation & Einrichtung](#teil-1-installation--einrichtung)
- [Teil 2: Betrieb & Verwaltung](#teil-2-betrieb--verwaltung)
- [Konfigurationsübersicht](#konfigurationsübersicht)
- [Releases & Updates](#releases--updates)
- [Logs und Diagnose](#logs-und-diagnose)
- [Fehlerbehebung](#fehlerbehebung)
- [Dateien im Repository](#dateien-im-repository)

---

## Architektur

```
OneDrive / SharePoint
        │
        │  rclone (beim Serverstart, niedrige Priorität)
        ▼
Dell Optiplex (Medienserver, kabelgebunden)
  /srv/media/          ← Produktionsfotos (Syncthing → Pis)
  /srv/basismedien/    ← Sponsor-/Logos   (Syncthing ↔ Pis + Mac)
  /srv/qlab_backup/    ← QLab-Projekte    (Syncthing ← Mac)
  /srv/qlab_media/     ← Medienbibliothek (Web-UI Port 5000)
        │
        │  Syncthing (WLAN)
        ├──────────────────────────────────────┐
        ▼                                      ▼
Raspberry Pi 4 (Touch-Kiosk)           MacBook (QLab)
  ~/media/      ← vom Server              ~/Documents/QLab → Server
  ~/basismedien ↔ bidirektional           ~/basismedien    ↔ bidirektional
  mediaplayer_app.py                      mac_wol.sh (weckt Server)
```

**Ablauf beim Start (Pis):**
1. Pi sendet Wake-on-LAN an den Server
2. Pi wartet bis Server erreichbar ist (max. 60 Sek.)
3. Syncthing überträgt neue/geänderte Bilder an den Pi (WLAN)
4. Server synchronisiert mit OneDrive – mit niedriger CPU/IO-Priorität,
   damit Syncthing-Übertragungen an Pis Vorrang haben
5. Watchdog prüft alle 5 Min: Pis/Mac erreichbar? Sync fertig? → ggf. Shutdown

**Ablauf beim Start (MacBook):**
1. launchd startet `mac_wol.sh` beim Login und alle 5 Minuten
2. Script prüft ob Server erreichbar → falls nicht: sendet WoL-Paket
3. Syncthing synchronisiert QLab-Projekte automatisch zum Server
4. Solange MacBook erreichbar: Server bleibt aktiv (Watchdog)

---

# Teil 1: Installation & Einrichtung

## Voraussetzungen

| Gerät | Anforderungen |
|-------|---------------|
| Dell Optiplex | Debian-basiertes Linux (Ubuntu, Linux Mint, Debian), kabelgebunden am UDM-SE, WoL im BIOS aktiviert |
| Raspberry Pi 4 | Pi OS Lite 64-bit, WLAN, Touch-Display |
| MacBook | macOS, WLAN, QLab 5 |
| Netzwerk | Alle Geräte im gleichen Subnetz (WoL-Broadcast muss Grenze passieren) |
| Cloud | Microsoft 365-Konto mit OneDrive/SharePoint-Zugang |

---

## Einrichtung Server (Dell Optiplex)

### 1. Repository klonen

```bash
ssh taf@<SERVER-IP>
git clone https://github.com/vklBo/mediaplayer.git
```

### 2. Setup-Script ausführen

```bash
sudo bash ~/mediaplayer/server_setup.sh
```

Funktioniert auf allen Debian-basierten Systemen (Ubuntu, Linux Mint, Debian).  
Installiert: rclone, syncthing, ffmpeg, python3-opencv, pillow, imagehash, flask, deepface, nfs-kernel-server  
Richtet ein: `/srv/media`, `/srv/basismedien`, `/srv/qlab_backup`, Syncthing-Service, Watchdog-Timer, Sync-Boot-Service, QLab-Web-UI

### 3. OneDrive verbinden

```bash
rclone config
```
→ Typ: **Microsoft OneDrive**  
→ Im Browser mit dem TaF-M365-Konto anmelden  
→ Konfigurationsname notieren (z.B. `onedrive`)

Danach in `sync_onedrive.py` anpassen:
```python
RCLONE_REMOTE = 'onedrive'
RCLONE_PATH   = 'Fotos'   # tatsächlichen Pfad in OneDrive eintragen
```

### 4. Geräte-IPs in Watchdog eintragen

Der Watchdog fährt den Server herunter wenn **keines** der eingetragenen Geräte
mehr erreichbar ist – also wenn weder Pis noch MacBook im Netz sind.

```bash
sudo nano /etc/taf/watchdog.conf
```
```
192.168.1.102    # Pi 1
192.168.1.105    # Pi 2
192.168.1.110    # MacBook (feste IP per DHCP-Reservierung im UDM-SE empfohlen)
TIMEOUT_MINUTES=15
```

> **Tipp:** Hostnamen funktionieren auch, z.B. `macbook.local` – aber feste IPs
> sind zuverlässiger.

### 5. Syncthing einrichten

Web-UI öffnen: `http://<SERVER-IP>:8384`

- **Geräte-ID des Servers notieren** (Aktionen → Identität anzeigen)
- Für jeden Pi: Gerät hinzufügen → Pi-Geräte-ID eingeben
- Ordner `/srv/media` mit allen Pis teilen (Typ: **Nur senden**)
- Ordner `/srv/basismedien` mit Pis + MacBook teilen (Typ: **Senden & Empfangen**)
- Ordner `/srv/qlab_backup` mit MacBook teilen (Typ: **Nur empfangen**)

### 6. Ersten Sync durchführen

```bash
# Ordnerstruktur auflisten – alle Ordner werden zunächst ausgeschlossen
python3 ~/mediaplayer/sync_onedrive.py --list-folders

# excluded_folders.txt bearbeiten: Zeilen der GEWÜNSCHTEN Ordner LÖSCHEN
nano ~/mediaplayer/excluded_folders.txt

# Vorschau (ohne Änderungen)
python3 ~/mediaplayer/sync_onedrive.py --dry-run

# Ersten Sync starten
python3 ~/mediaplayer/sync_onedrive.py
```

---

## Einrichtung Raspberry Pi

### 1. Repository klonen

```bash
ssh taf@<PI-IP>
git clone https://github.com/vklBo/mediaplayer.git
```

### 2. Setup-Script ausführen

```bash
sudo bash ~/mediaplayer/pi_setup.sh
```

Installiert: syncthing, wakeonlan, kivy, pillow  
Richtet ein: WoL-Boot-Service, Syncthing-Service, mediaplayer-Service

### 3. Server-MAC und IP eintragen

```bash
# MAC-Adresse des Servers herausfinden (auf dem Server ausführen):
ip link show | grep "link/ether"

sudo nano /etc/taf/pi.conf
```
```
SERVER_MAC="AA:BB:CC:DD:EE:FF"
SERVER_IP="192.168.1.200"
SERVER_WAIT_TIMEOUT=60
```

### 4. Syncthing koppeln

Web-UI öffnen: `http://<PI-IP>:8384`

- **Geräte-ID des Pi notieren** (Aktionen → Identität anzeigen)
- Diese ID auf dem Server unter `http://<SERVER-IP>:8384` als neues Gerät eintragen
- Geteilten Ordner `~/media` vom Server akzeptieren (Typ: **Nur empfangen**)
- Geteilten Ordner `~/basismedien` vom Server akzeptieren (Typ: **Senden & Empfangen**)

### 5. Neustart und Test

```bash
sudo reboot
journalctl -u taf_service.service -f
```

---

## Einrichtung MacBook

### 1. Repository klonen

```bash
git clone https://github.com/vklBo/mediaplayer.git ~/mediaplayer
```

### 2. SERVER_MAC und SERVER_IP eintragen

```bash
nano ~/mediaplayer/mac_wol.sh
```
```bash
SERVER_MAC="AA:BB:CC:DD:EE:FF"   # MAC-Adresse des Optiplex (ip link show)
SERVER_IP="192.168.1.200"
```

### 3. launchd-Agent einrichten

```bash
bash ~/mediaplayer/mac_wol_setup.sh
```

Richtet automatisch ein:
- WoL beim Login
- WoL alle 5 Minuten (hält Server wach + weckt nach Schlaf)
- Log: `~/Library/Logs/taf_server_wol.log`

Deinstallieren:
```bash
launchctl unload ~/Library/LaunchAgents/de.theateramfluss.server-wol.plist
rm ~/Library/LaunchAgents/de.theateramfluss.server-wol.plist
```

### 4. Syncthing einrichten

Syncthing installieren: https://syncthing.net/downloads/ (macOS-App)  
Web-UI öffnen: `http://localhost:8384`

- **Geräte-ID des MacBook notieren**
- Diese ID auf dem Server eintragen
- Geteilten Ordner `/srv/basismedien` akzeptieren (Typ: **Senden & Empfangen**)
- Eigenen QLab-Ordner hinzufügen (Pfad: `~/Documents/QLab` o.ä., Typ: **Nur senden**)
  → Server empfängt unter `/srv/qlab_backup/`

---

# Teil 2: Betrieb & Verwaltung

## Bilder aktualisieren

### Über OneDrive (automatisch)

Neue Bilder in OneDrive in der Struktur `Spielzeit/Produktion/bilder.jpg` ablegen.  
Beim nächsten Start des Servers werden sie automatisch synchronisiert.

Manuell auf dem Server anstoßen:
```bash
python3 ~/mediaplayer/sync_onedrive.py
```

### Über USB-Stick (direkt am Pi)

USB-Stick mit folgender Struktur einstecken:
```
USB-Stick/
├── 2024-25/
│   ├── Faust/
│   └── Hamlet/
├── basismedien/     ← Sponsor-/Dauerbilder (werden immer eingestreut)
└── skripte/         ← Python-Dateien → werden ins Home-Verzeichnis kopiert
```
Pi erkennt Stick automatisch, kopiert Bilder und wirft ihn aus.  
Papierkorb-Ordner (`.Trashes`, `$RECYCLE.BIN`, `.Trash-*`) werden ignoriert.

---

## Genres zuweisen

Produktionen können hierarchischen Genres zugeordnet werden (mehrere möglich).
Auf dem Pi erscheint dann ein Button **🎭 Genres**, über den man alle Bilder
aller Produktionen eines Genres durchsehen kann.

**Empfohlen: genre.txt direkt in OneDrive pflegen.**  
Eine `genre.txt` (ein Genre pro Zeile) im jeweiligen Produktionsordner in OneDrive
ablegen – sie wird beim nächsten Sync automatisch übernommen:

```
JungesEnsemble/Kinder
Drama
```

**Alternativ: Skript auf dem Server:**
```bash
# 1. Vorlage erzeugen
python3 ~/mediaplayer/genres.py scan

# 2. Genres eintragen
nano ~/mediaplayer/genres.txt
#   2024-25/Faust = Drama/Klassiker
#   2024-25/Romeo = JungesEnsemble/Jugendliche, Drama

# 3. In Produktionsordner schreiben (Syncthing verteilt an Pis)
python3 ~/mediaplayer/genres.py apply

# Übersicht anzeigen
python3 ~/mediaplayer/genres.py list
```

> **Vorrang:** `genre.txt` aus OneDrive überschreibt eine Zuweisung per `genres.py`.

---

## Autostart konfigurieren

### Zentral über OneDrive (für alle Displays)

Datei `Fotos/Konfiguration/autostart_config.txt` in OneDrive anlegen:

```
# hostname = genre:Pfad  oder  folder:Spielzeit/Produktion
pi4 = genre:JungesEnsemble/Kinder
pi5 = folder:2024-25/Faust
*   = genre:Drama          # Fallback für alle übrigen Displays
```

Wird beim nächsten Sync nach `/srv/media/konfiguration/` kopiert und
per Syncthing an alle Pis verteilt.

### Lokal am Display (überschreibt zentrale Einstellung)

Startbildschirm → **Autostart** → PIN → gewünschte Show auswählen.  
Die lokale Einstellung hat immer Vorrang. Im Autostart-Screen ist ersichtlich
ob eine lokale oder die zentrale Einstellung aktiv ist.

---

## Grundstock (Sponsor-/Dauerbilder)

Der Grundstock liegt auf dem Server unter `/srv/basismedien/` und wird per Syncthing
auf alle Pis verteilt. Die Bilder erscheinen automatisch alle **15 Bilder** in jeder
Diashow (konfigurierbar: `GRUNDSTOCK_INTERVAL` in `mediaplayer_app.py`, 0 = aus).

**Aktualisieren per USB-Stick:** Stick mit Ordner `basismedien/` an einen Pi stecken.  
Inhalte werden **hinzugefügt** (kein Löschen) und per Syncthing verteilt.

**Entfernen:** Direkt auf dem Server löschen:
```bash
rm /srv/basismedien/<dateiname>
```
Syncthing verteilt die Löschung automatisch.

---

## Kurationsmodus (am Display)

Zugang: Hauptbildschirm → **Kuratieren** → PIN eingeben (Standard: **`1313`**)  
PIN ändern: `KURATION_PIN` in `mediaplayer_app.py`

### Produktions-Ebene

| Anzeige | Farbe | Bedeutung |
|---------|-------|-----------|
| `✓ 47 Bilder` | grün | Keine Qualitätsprobleme |
| `⚠ 8 / 47` | gelb | Einige Bilder auffällig |
| `⚠ 23 / 47 – viele schlecht` | orange | > 40% flagged → Ordner ausschließen? |

Buttons je Kachel:
- **Bilder →** Einzelbilder kuratieren
- **✗ ausschl. / ✓ einschl.** Ganzen Ordner aus der Anzeige nehmen
- **🗑** Ordner dauerhaft löschen (PIN-Bestätigung)

### Bild-Ebene

- **✓ ein / ✗ aus** Bild ein- oder ausblenden
- **🗑 löschen** Bild dauerhaft löschen
- **⚠ Nur markierte** zeigt ausschließlich Problembilder → schnelles Durcharbeiten
- **💾 Speichern** Änderungen übernehmen

### Schnell-Kuration während der Diashow

**✗** in der Steuerleiste antippen → PIN → Modus aktiv (Button rot).  
Aktuelles Bild antippen → sofort ausgeblendet.  
Automatische Deaktivierung 60 Sek. nach dem letzten Ausschluss.

---

## Qualitätsanalyse

Läuft automatisch beim Sync auf dem Server.  
Theaterangepasst: Schärfe auf **hellen Bereichen** (Bühne), Rauschen auf **dunklen Bereichen**.

| Kriterium | Methode | Standard-Schwellwert |
|-----------|---------|----------------------|
| Unscharf | Laplacian-Varianz auf hellen Bereichen | < 80 |
| Zu dunkel | Anteil heller Pixel am Gesamtbild | < 5 % |
| Verrauscht | Immerkaer-Sigma in dunklen Bereichen | > 9.0 |
| Duplikat | Perceptual Hash (Hamming-Distanz) | < 8 |
| Beschädigt | PIL verify() schlägt fehl | – |

Schwellwerte neu anwenden (ohne neuen Sync):
```bash
python3 ~/mediaplayer/sync_onedrive.py --retag
```

---

## Personen ausblenden

Bestimmte Personen können automatisch aus allen Diashows ausgeblendet werden.
Läuft auf dem Server, wird **manuell angestossen**.

### Einrichtung

```bash
mkdir -p ~/mediaplayer/faces/<name>
# Referenzfotos hineinkopieren (mehrere = bessere Erkennung)
```

### Ausführen

```bash
# Vorschau – alle Personen prüfen, nichts schreiben
python3 ~/mediaplayer/face_exclude.py

# Nur eine Person prüfen
python3 ~/mediaplayer/face_exclude.py --ref <name>

# Treffer in excluded.txt eintragen
python3 ~/mediaplayer/face_exclude.py --apply
```

Ausgabe gruppiert nach Produktion:
```
Person: alex
  2024-25/Faust (3 Treffer):  bild001.jpg, bild047.jpg, bild112.jpg
  2023-24/Hamlet (1 Treffer): bild003.jpg
```

> Der `faces/`-Ordner ist in `.gitignore` – Referenzfotos werden nie eingecheckt.  
> Beim ersten Lauf lädt DeepFace das Facenet512-Modell herunter (~250 MB).

---

## QLab-Medienbibliothek

### Katalog aufbauen (auf dem Server)

```bash
python3 ~/mediaplayer/qlab_media_collector.py           # Katalog erstellen/aktualisieren
python3 ~/mediaplayer/qlab_media_collector.py --dry-run # Vorschau
python3 ~/mediaplayer/qlab_media_collector.py --clean   # Komplett neu aufbauen
```

### Webinterface

Läuft automatisch als systemd-Service: **`http://<SERVER-IP>:5000`**

Features: Volltextsuche, Filter nach Typ, Audio direkt abspielen, Herunterladen,
Zuordnung zu QLab-Projekten.

---

## Konfigurationsübersicht

### `mediaplayer_app.py`
```python
KURATION_PIN        = '1313'   # PIN für Kurationsmodus
SLIDESHOW_INTERVAL  = 5        # Sekunden pro Bild
GRUNDSTOCK_INTERVAL = 15       # Jedes N-te Bild = Grundstock (0 = aus)
SHARPNESS_LOW       = 80       # Schärfe-Schwellwert (wird live angewendet)
NOISE_THRESHOLD     = 9.0      # Rausch-Schwellwert (wird live angewendet)
TILE_COLS           = 4        # Kacheln nebeneinander
```

### `sync_onedrive.py`
```python
RCLONE_REMOTE    = 'onedrive'
RCLONE_PATH      = 'Fotos'
JPEG_QUALITY     = 88          # Bildqualität nach Optimierung (0–95)
SHARPNESS_LOW    = 80          # Schärfe-Schwellwert
NOISE_THRESHOLD  = 9.0         # Rausch-Schwellwert
```

### `/etc/taf/watchdog.conf` (Server)
```
192.168.1.102    # Pi 1
192.168.1.110    # MacBook
TIMEOUT_MINUTES=15
```

### `/etc/taf/pi.conf` (Pi)
```
SERVER_MAC="AA:BB:CC:DD:EE:FF"
SERVER_IP="192.168.1.200"
SERVER_WAIT_TIMEOUT=60
```

---

## Releases & Updates

Server und Pis stellen **beim Start** automatisch auf die neueste Release um.
Eine Release ist ein Git-Tag der Form `vX.Y.Z`.

### Neue Release veröffentlichen

```bash
git commit -am "..."
git push
git tag v1.2.0
git push origin v1.2.0
```

Beim nächsten Neustart ziehen Server und Pis automatisch die neue Version.

### Manuell aktualisieren

```bash
~/mediaplayer/taf-pull.sh          # wechselt auf main und pullt
sudo systemctl restart taf_service # Pi: Mediaplayer neu starten
sudo systemctl restart taf-sync    # Server: Sync neu starten
```

> `taf-pull.sh` löst das „detached HEAD"-Problem das durch den automatischen
> Tag-Checkout beim Start entsteht.

---

## Logs und Diagnose

```bash
# Sync-Log (Server)
tail -f /var/log/taf_sync.log

# Watchdog (Server)
journalctl -u taf-watchdog.service -n 50

# Syncthing (Server oder Pi)
journalctl -u syncthing@taf.service -n 50

# Mediaplayer (Pi)
journalctl -u taf_service.service -f

# WoL-Service (Pi)
journalctl -u taf-wol.service
```

---

## Fehlerbehebung

**Pi zeigt keine Bilder**
→ `systemctl status syncthing@taf` auf Pi und Server  
→ `ls ~/media/` – sind Bilder vorhanden?  
→ `ping <SERVER-IP>` vom Pi

**Server startet nicht per WoL**
→ WoL im BIOS aktiviert? (Power Management → Deep Sleep = Disabled)  
→ Kabelverbindung am Server vorhanden?  
→ `SERVER_MAC` in `/etc/taf/pi.conf` korrekt? (`ip link show` auf Server)

**Sync schlägt fehl**
→ `rclone config show` – ist OneDrive konfiguriert?  
→ `rclone lsd onedrive:` – ist der Pfad erreichbar?  
→ `tail /var/log/taf_sync.log`

**Server fährt nicht herunter**
→ Sync noch aktiv? `ls /tmp/taf_sync_running`  
→ Pi- und MacBook-IPs in `/etc/taf/watchdog.conf` eingetragen?  
→ `journalctl -u taf-watchdog.service -n 20`

**MacBook weckt Server nicht**
→ `SERVER_MAC` in `mac_wol.sh` korrekt?  
→ `SERVER_IP` erreichbar? (`ping <SERVER-IP>` vom Mac)  
→ Log prüfen: `cat ~/Library/Logs/taf_server_wol.log`

**Syncthing koppelt nicht**
→ Geräte-IDs korrekt eingetragen?  
→ Firewall: Port 22000 (TCP+UDP) freigeben  
→ `.stfolder` vorhanden? (`touch /srv/media/.stfolder`)

---

## Dateien im Repository

| Datei | Gerät | Zweck |
|-------|-------|-------|
| `mediaplayer_app.py` | Pi | Kivy-App: Touch-UI, Diashow, Kuration |
| `sync_onedrive.py` | Server | OneDrive → /srv/media, Optimierung, Qualitätsanalyse |
| `face_exclude.py` | Server | Personen per Gesichtserkennung aus Diashows ausblenden |
| `genres.py` | Server | Hierarchische Genres an Produktionen zuweisen |
| `qlab_media_collector.py` | Server | QLab-Backup scannen, Katalog aufbauen |
| `qlab_web.py` | Server | Webinterface Medienbibliothek (Port 5000) |
| `server_setup.sh` | Server | Einmalige Einrichtung des Dell Optiplex |
| `server_watchdog.sh` | Server | Automatischer Shutdown wenn keine Geräte aktiv |
| `pi_setup.sh` | Pi | Einmalige Einrichtung eines Raspberry Pi |
| `update_to_release.sh` | Server + Pi | Beim Start auf neueste Release (Git-Tag) umstellen |
| `taf-pull.sh` | Server + Pi | Manuelles Git-Update aus detached HEAD |
| `mac_wol.sh` | MacBook | Server per WoL wecken |
| `mac_wol_setup.sh` | MacBook | launchd-Agent für automatischen WoL einrichten |
| `Anleitung_Mediaplayer.md` | – | Benutzeranleitung für Theatermitarbeiter |
