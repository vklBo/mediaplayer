# TaF Mediaplayer

Automatisches Bilderanzeigesystem für Touch-Displays des Theater am Fluss (TaF).
Zeigt Produktionsfotos geordnet nach Spielzeit und Produktion, mit Kurationsmodus zur Bildauswahl.

---

## Architektur

```
OneDrive / SharePoint
        │
        │  rclone (beim Serverstart, niedrige Priorität)
        ▼
Dell Optiplex (Medienserver, kabelgebunden)
  /srv/media/
    2024-25/
      Faust/          ← optimiert auf 1920×1080, EXIF entfernt
      Hamlet/         ← quality_scores.json (Schärfe, Rauschen, Duplikate)
        │
        │  Syncthing (WLAN, hat Vorrang vor OneDrive-Sync)
        ▼
Raspberry Pi 4 (Touch-Kiosk)         Raspberry Pi 4 (Touch-Kiosk)
  ~/media/  (lokale Kopie)             ~/media/  (lokale Kopie)
  mediaplayer_app.py                   mediaplayer_app.py
```

**Ablauf beim Start:**
1. Pi sendet Wake-on-LAN an den Server
2. Pi wartet bis Server erreichbar ist (max. 60 Sek.)
3. Syncthing überträgt neue/geänderte Bilder an den Pi (WLAN)
4. Server synchronisiert mit OneDrive – mit niedriger CPU/IO-Priorität,
   damit Syncthing-Übertragungen an Pis Vorrang haben
5. Watchdog prüft alle 5 Min: Pis erreichbar? Sync fertig? Syncthing fertig? → ggf. Shutdown

---

## Voraussetzungen

| Gerät | Anforderungen |
|-------|---------------|
| Dell Optiplex | Ubuntu/Debian, kabelgebunden am UDM-SE, WoL im BIOS aktiviert |
| Raspberry Pi 4 | Pi OS Lite 64-bit, WLAN, Touch-Display |
| Netzwerk | Pis und Server im gleichen Subnetz (WLAN + LAN, WoL-Broadcast muss passieren) |
| Cloud | Microsoft 365-Konto mit OneDrive/SharePoint-Zugang |

---

## Einrichtung Server (Dell Optiplex)

### 1. Repository auf den Server kopieren

```bash
scp -r mediaplayer/ taf@<SERVER-IP>:~/
```

### 2. Setup-Script ausführen

```bash
ssh taf@<SERVER-IP>
sudo bash ~/mediaplayer/server_setup.sh
```

Installiert: rclone, syncthing, python3-opencv, pillow, imagehash, nfs-kernel-server  
Richtet ein: `/srv/media`, Syncthing-Service, Watchdog-Timer, Sync-Boot-Service

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
RCLONE_PATH   = 'Theater/Fotos'   # tatsächlichen Pfad in OneDrive eintragen
```

### 4. Pi-IPs in Watchdog eintragen

```bash
sudo nano /etc/taf/watchdog.conf
```
```
192.168.1.102
192.168.1.105
TIMEOUT_MINUTES=15
```

### 5. Syncthing einrichten

Web-UI öffnen: `http://<SERVER-IP>:8384`

- **Geräte-ID des Servers notieren** (Aktionen → Identität anzeigen)
- Für jeden Pi: Gerät hinzufügen → Pi-Geräte-ID eingeben
- Ordner `/srv/media` mit allen Pis teilen (Typ: **Nur senden**)
- Ordner `/srv/medienbasis` mit allen Pis teilen (Typ: **Senden & Empfangen**)

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

### 1. Dateien auf den Pi kopieren

```bash
scp mediaplayer_app.py pi_setup.sh taf@<PI-IP>:~/
```

### 2. Setup-Script ausführen

```bash
ssh taf@<PI-IP>
sudo bash ~/pi_setup.sh
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
SERVER_IP="192.168.1.100"
SERVER_WAIT_TIMEOUT=60
```

### 4. Syncthing koppeln

Web-UI öffnen: `http://<PI-IP>:8384`

- **Geräte-ID des Pi notieren** (Aktionen → Identität anzeigen)
- Diese ID auf dem Server unter `http://<SERVER-IP>:8384` als neues Gerät eintragen
- Geteilten Ordner `~/media` vom Server akzeptieren (Typ: **Nur empfangen**)
- Geteilten Ordner `~/medienbasis` vom Server akzeptieren (Typ: **Senden & Empfangen**)

### 5. Neustart und Test

```bash
sudo reboot
journalctl -u taf_service.service -f
```

---

## Alltägliche Nutzung

### Bilder über OneDrive aktualisieren

Neue Bilder in OneDrive in der Struktur `Spielzeit/Produktion/bilder.jpg` ablegen.  
Beim nächsten Start des Servers werden sie automatisch synchronisiert.

Manuell auf dem Server:
```bash
python3 ~/mediaplayer/sync_onedrive.py
```

### Bilder über USB-Stick einspielen

USB-Stick mit folgender Struktur einstecken:
```
USB-Stick/
├── 2024-25/
│   ├── Faust/
│   └── Hamlet/
├── medienbasis/     ← Sponsor-/Dauerbilder (werden immer eingestreut)
└── skripte/         ← Python-Dateien → werden ins Home-Verzeichnis kopiert
```
Pi erkennt Stick automatisch, kopiert Bilder und wirft ihn aus.  
Papierkorb-Ordner (`.Trashes`, `$RECYCLE.BIN`, `.Trash-*`) werden ignoriert.

### Grundstock (Sponsor-/Dauerbilder)

Der Grundstock liegt zentral auf dem Server unter `/srv/medienbasis/` und wird per Syncthing
auf alle Pis verteilt (`~/medienbasis/`). Die Bilder erscheinen automatisch alle **5 Bilder**
in jeder Diashow (konfigurierbar: `GRUNDSTOCK_INTERVAL` in `mediaplayer_app.py`, 0 = aus).

**Grundstock aktualisieren per USB-Stick:**
Stick mit Ordner `medienbasis/` an einen beliebigen Pi stecken.  
Die Inhalte werden zu `~/medienbasis/` **hinzugefügt** (kein Löschen vorhandener Dateien)  
und von dort automatisch per Syncthing an Server + alle anderen Pis verteilt.

**Grundstock-Bilder entfernen:** Kurationsmodus → Ordner `medienbasis` nicht vorhanden?  
Direkt auf dem Server löschen: `rm /srv/medienbasis/<dateiname>` – Syncthing verteilt die Löschung.

---

## Kurationsmodus

Zugang: Hauptbildschirm → **✏ Kuration** → PIN eingeben (Standard: **`1234`**)  
PIN ändern: `KURATION_PIN` in `mediaplayer_app.py`

### Ordner-Übersicht

Jede Produktionskachel zeigt eine Qualitätsstatistik:

| Anzeige | Farbe | Bedeutung |
|---------|-------|-----------|
| `✓ 47 Bilder` | grün | Keine Qualitätsprobleme |
| `⚠ 8 / 47` | gelb | Einige Bilder auffällig |
| `⚠ 23 / 47 – viele schlecht` | orange | > 40% flagged → Ordner ausschließen? |

Buttons je Kachel:
- **Bilder →** Einzelbilder kurationieren
- **✗ ausschließen / ✓ einschließen** Ganzen Ordner aus der Anzeige nehmen
- **🗑** Ordner dauerhaft löschen (PIN-Bestätigung)

### Bild-Ebene

- `✓ ein / ✗ aus` Bild ein- oder ausblenden (`excluded.txt`)
- `🗑 löschen` Bild dauerhaft vom Pi löschen
- `⚠ Unscharf / Verrauscht / Duplikat` automatisch erkannte Probleme
- **⚠ Nur markierte** zeigt ausschließlich Problembilder → schnelles Durcharbeiten

Nach dem Speichern Ausschlüsse zurück nach OneDrive übertragen:
```bash
python3 ~/mediaplayer/sync_onedrive.py --push-excluded
```

---

## Qualitätsanalyse

Läuft automatisch beim Sync auf dem Server.  
Theaterangepasst: Schärfe auf **hellen Bereichen** (Bühne), Rauschen auf **dunklen Bereichen** (Hintergrund/Zuschauerraum bei hoher ISO).

| Kriterium | Methode | Standard-Schwellwert |
|-----------|---------|----------------------|
| Unscharf | Laplacian-Varianz auf hellen Bereichen | < 35 |
| Zu dunkel | Anteil heller Pixel am Gesamtbild | < 5 % |
| Verrauscht | Immerkaer-Sigma in dunklen Bereichen | > 9.0 |
| Duplikat | Perceptual Hash (Hamming-Distanz) | < 8 |

Ergebnisse in `quality_scores.json` je Produktionsordner – werden mit Syncthing auf die Pis übertragen.

---

## Konfigurationsübersicht

### `mediaplayer_app.py`
```python
KURATION_PIN        = '1234'   # PIN für Kurationsmodus
SLIDESHOW_INTERVAL  = 5        # Sekunden pro Bild
GRUNDSTOCK_INTERVAL = 5        # Jedes N-te Bild = Grundstock (0 = aus)
TILE_COLS           = 4        # Kacheln nebeneinander
```

### `sync_onedrive.py`
```python
RCLONE_REMOTE    = 'onedrive'
RCLONE_PATH      = 'Theater/Fotos'
JPEG_QUALITY     = 88          # Bildqualität nach Optimierung (0–95)
SHARPNESS_LOW    = 35          # Schärfe-Schwellwert
NOISE_THRESHOLD  = 9.0         # Rausch-Schwellwert
```

### `/etc/taf/watchdog.conf` (Server)
```
192.168.1.102
192.168.1.105
TIMEOUT_MINUTES=15
```

### `/etc/taf/pi.conf` (Pi)
```
SERVER_MAC="AA:BB:CC:DD:EE:FF"
SERVER_IP="192.168.1.100"
SERVER_WAIT_TIMEOUT=60
```

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
→ WoL im BIOS aktiviert? (Power Management)  
→ Kabelverbindung am Server vorhanden?  
→ `SERVER_MAC` in `/etc/taf/pi.conf` korrekt? (`ip link show` auf Server)

**Sync schlägt fehl**
→ `rclone config show` – ist OneDrive konfiguriert?  
→ `rclone lsd onedrive:` – ist der Pfad erreichbar?  
→ `tail /var/log/taf_sync.log`

**Server fährt nicht herunter**
→ Sync noch aktiv? `ls /tmp/taf_sync_running`  
→ Pi-IPs in `/etc/taf/watchdog.conf` eingetragen?  
→ `journalctl -u taf-watchdog.service -n 20`

**Syncthing koppelt nicht**
→ Geräte-IDs korrekt eingetragen?  
→ Firewall: Port 22000 (TCP+UDP) freigeben  
→ Beide Geräte im gleichen Netz?

---

## Dateien im Repository

| Datei | Zweck |
|-------|-------|
| `mediaplayer_app.py` | Kivy-App für den Pi (Touch-UI, Diashow, Kuration) |
| `sync_onedrive.py` | Server: OneDrive → /srv/media, Optimierung, Qualitätsanalyse |
| `server_setup.sh` | Einmalige Einrichtung des Dell Optiplex |
| `server_watchdog.sh` | Automatischer Shutdown wenn keine Pis aktiv |
| `pi_setup.sh` | Einmalige Einrichtung eines Raspberry Pi |
