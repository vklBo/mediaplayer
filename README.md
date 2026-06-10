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

### 1. Repository auf den Server kopieren

```bash
scp -r mediaplayer/ taf@<SERVER-IP>:~/
```

### 2. Setup-Script ausführen

```bash
ssh taf@<SERVER-IP>
sudo bash ~/mediaplayer/server_setup.sh
```

Funktioniert auf allen Debian-basierten Systemen (Ubuntu, Linux Mint, Debian).  
Installiert: rclone, syncthing, ffmpeg, python3-opencv, pillow, imagehash, flask, nfs-kernel-server  
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
RCLONE_PATH   = 'Theater/Fotos'   # tatsächlichen Pfad in OneDrive eintragen
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
  *(optional – erst einrichten wenn Basismedien-Sync gewünscht)*
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

### 1. Repository auf den Pi klonen

```bash
ssh taf@<PI-IP>
git clone https://github.com/vklBo/mediaplayer.git
cd mediaplayer
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
SERVER_IP="192.168.1.100"
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

### 1. Dateien auf das MacBook kopieren

```bash
scp mac_wol.sh mac_wol_setup.sh benutzer@macbook.local:~/mediaplayer/
```

### 2. SERVER_MAC und SERVER_IP eintragen

```bash
nano ~/mediaplayer/mac_wol.sh
```
```bash
SERVER_MAC="AA:BB:CC:DD:EE:FF"   # MAC-Adresse des Optiplex (ip link show)
SERVER_IP="192.168.1.100"
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

### 5. QLab-Pfad in Kollektor eintragen

Sobald der QLab-Ordnerpfad bekannt ist, in `qlab_media_collector.py` anpassen:
```python
QLAB_BACKUP_DIR = Path('/srv/qlab_backup')   # bereits korrekt (Server-seitig)
```

---

## QLab-Medienbibliothek

### Katalog aufbauen (auf dem Server)

```bash
# Ersten Katalog erstellen
python3 ~/mediaplayer/qlab_media_collector.py

# Vorschau ohne Kopieren
python3 ~/mediaplayer/qlab_media_collector.py --dry-run

# Inkrementell aktualisieren (nach neuen QLab-Projekten)
python3 ~/mediaplayer/qlab_media_collector.py

# Komplett neu aufbauen
python3 ~/mediaplayer/qlab_media_collector.py --clean
```

### Webinterface

Läuft automatisch als systemd-Service auf Port 5000:  
**`http://<SERVER-IP>:5000`**

Features:
- Volltextsuche über Dateiname, Tags (Title, Comment, Genre), Projekte
- Filter nach Typ (Audio/Video/Bild) und Kategorie
- **▶ Play** – Audio direkt im Browser abspielen
- **↓** – Datei herunterladen
- Zeigt aus welchen QLab-Projekten eine Datei stammt

### Kategorisierung

| Kategorie | Kriterium | Typischer Inhalt |
|-----------|-----------|-----------------|
| `sfx` | Dauer < 5 Sek | Geräusche, Effekte |
| `stings` | Dauer 5–60 Sek | Übergangsklänge, kurze Musik |
| `musik_ambience` | Dauer > 60 Sek | Musik, Atmosphäre |
| `video` | Videodatei | Projektionsmaterial |
| `bilder` | Bilddatei | Grafiken, Fotos |

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
├── basismedien/     ← Sponsor-/Dauerbilder (werden immer eingestreut)
└── skripte/         ← Python-Dateien → werden ins Home-Verzeichnis kopiert
```
Pi erkennt Stick automatisch, kopiert Bilder und wirft ihn aus.  
Papierkorb-Ordner (`.Trashes`, `$RECYCLE.BIN`, `.Trash-*`) werden ignoriert.

### Genres zuweisen (hierarchisch)

Produktionen können hierarchischen Genres zugeordnet werden (mehrere möglich).
Auf dem Pi erscheint dann ein Button **🎭 Genres**, über den man alle Bilder
aller Produktionen eines Genres durchsehen kann – auch Oberkategorien
aggregieren ihre Unterkategorien.

**Auf dem Server:**
```bash
# 1. Vorlage aus vorhandenen Produktionen erzeugen
python3 ~/mediaplayer/genres.py scan

# 2. Genres eintragen (hierarchisch mit /)
nano ~/mediaplayer/genres.txt
#   2024-25/Raeuberkinder = JungesEnsemble/Kinder
#   2024-25/Romeo         = JungesEnsemble/Jugendliche, Drama
#   2024-25/Faust         = Drama/Klassiker

# 3. In die Produktionsordner schreiben (→ Syncthing verteilt an Pis)
python3 ~/mediaplayer/genres.py apply

# Übersicht + Genre-Baum anzeigen
python3 ~/mediaplayer/genres.py list
```

Die `genre.txt` in jedem Produktionsordner wird vom Sync erhalten und per
Syncthing an die Pis verteilt.

**Alternativ: genre.txt direkt in OneDrive pflegen.**
Du kannst die `genre.txt` (ein Genre pro Zeile) auch direkt im jeweiligen
Produktionsordner in OneDrive ablegen. Sie wird dann mitsynchronisiert und
interpretiert. Vorrang-Regel:
1. `genre.txt` aus OneDrive → maßgeblich, falls vorhanden
2. sonst die per `genres.py` gesetzte Zuweisung

Wenn du also für eine Produktion eine `genre.txt` in OneDrive anlegst,
überschreibt sie eine eventuelle `genres.py`-Zuweisung bei jedem Sync.

### Grundstock (Sponsor-/Dauerbilder)

Der Grundstock liegt zentral auf dem Server unter `/srv/basismedien/` und wird per Syncthing
auf alle Pis verteilt (`~/basismedien/`). Die Bilder erscheinen automatisch alle **5 Bilder**
in jeder Diashow (konfigurierbar: `GRUNDSTOCK_INTERVAL` in `mediaplayer_app.py`, 0 = aus).

**Grundstock aktualisieren per USB-Stick:**
Stick mit Ordner `basismedien/` an einen beliebigen Pi stecken.  
Die Inhalte werden zu `~/basismedien/` **hinzugefügt** (kein Löschen vorhandener Dateien)  
und von dort automatisch per Syncthing an Server + alle anderen Pis verteilt.

**Grundstock-Bilder entfernen:** Kurationsmodus → Ordner `basismedien` nicht vorhanden?  
Direkt auf dem Server löschen: `rm /srv/basismedien/<dateiname>` – Syncthing verteilt die Löschung.

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

## Personen ausblenden (Gesichtserkennung)

Bestimmte Personen können automatisch aus allen Diashows ausgeblendet werden.
Das Skript `face_exclude.py` vergleicht alle Bilder gegen Referenzfotos und
trägt Treffer in `excluded.txt` ein. Es läuft auf dem Server und wird
**manuell angestossen** – nie automatisch.

### Einrichtung

```bash
# Verzeichnis anlegen (bereits durch server_setup.sh erstellt)
mkdir -p ~/mediaplayer/faces/<name>

# Referenzfotos hineinkopieren (mehrere Fotos = bessere Erkennung)
cp foto1.jpg foto2.jpg ~/mediaplayer/faces/<name>/
```

### Ausführen

```bash
# Vorschau – alle Personen in faces/ prüfen, nichts schreiben
python3 ~/mediaplayer/face_exclude.py

# Nur eine bestimmte Person prüfen
python3 ~/mediaplayer/face_exclude.py --ref <name>

# Treffer in excluded.txt eintragen
python3 ~/mediaplayer/face_exclude.py --apply

# Anderen Medienordner (z.B. lokal auf Mac)
python3 ~/mediaplayer/face_exclude.py --media /Users/vk/media
```

### Ausgabe (Vorschau)

```
Person: alex
==============================
  2024-25/Faust (3 Treffer):
    - bild001.jpg
    - bild047.jpg
    - bild112.jpg

  2023-24/Hamlet (1 Treffer):
    - bild003.jpg
```

### Hinweise

- **Threshold:** Standard 0.45 – großzügig (lieber zu viele als zu wenige).
  Mit `--threshold 0.35` strenger, mit `--threshold 0.55` noch großzügiger.
- **Referenzfotos:** Je mehr und vielfältiger (verschiedene Winkel, Beleuchtungen),
  desto besser die Erkennungsrate bei Theaterbeleuchtung.
- **Datenschutz:** Der `faces/`-Ordner ist in `.gitignore` – Referenzfotos
  werden nie ins Repository eingecheckt.
- **Erster Lauf:** DeepFace lädt beim ersten Start das Facenet512-Modell herunter
  (~250 MB) – das dauert einmalig etwas länger.

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
192.168.1.102    # Pi 1
192.168.1.105    # Pi 2
192.168.1.110    # MacBook
TIMEOUT_MINUTES=15
```

### `/etc/taf/pi.conf` (Pi)
```
SERVER_MAC="AA:BB:CC:DD:EE:FF"
SERVER_IP="192.168.1.100"
SERVER_WAIT_TIMEOUT=60
```

---

## Releases / Automatische Updates

Server und Pis stellen **beim Start** automatisch auf die neueste Release um
(nicht zwischendurch). Eine Release ist ein Git-Tag der Form `vX.Y.Z`.

### Neue Release veröffentlichen (am Entwicklungsrechner)

```bash
# Änderungen committen und pushen
git commit -am "..."
git push

# Release-Tag setzen und pushen
git tag v1.1.0
git push origin v1.1.0
```

Beim nächsten Neustart ziehen Server und Pis automatisch `v1.1.0`.

### Wie es funktioniert

- `update_to_release.sh` läuft beim Boot (Server: `taf-update.service`,
  Pi: `ExecStartPre` im mediaplayer-Service)
- Es holt die Tags, ermittelt das höchste `v*`-Tag und checkt es aus
- Bricht den Start **nie** ab: kein Netz / keine Tags / lokale Änderungen
  → die aktuell installierte Version läuft weiter
- Lokale Konfig (`excluded_folders.txt`, `genres.txt`, rclone-Config,
  `/etc/taf/*`) bleibt unberührt

### Manuell aktualisieren (ohne Neustart)

```bash
cd ~/mediaplayer && ./update_to_release.sh
sudo systemctl restart taf_service.service      # Pi
sudo systemctl restart taf-sync.service         # Server (oder neu syncen)
```

> Hinweis: Ändert eine Release die systemd-`.service`-Dateien selbst, greift das
> erst nach erneutem `sudo bash server_setup.sh` bzw. `pi_setup.sh` (systemd lädt
> Unit-Dateien nicht automatisch neu). Code-Änderungen greifen dagegen sofort.

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
→ Pi- und MacBook-IPs in `/etc/taf/watchdog.conf` eingetragen?  
→ `journalctl -u taf-watchdog.service -n 20`

**MacBook weckt Server nicht**
→ `SERVER_MAC` in `mac_wol.sh` korrekt? (`ip link show` auf Server)  
→ `SERVER_IP` erreichbar? (`ping 192.168.1.100` vom Mac)  
→ WoL im BIOS des Servers aktiviert? (Power Management)  
→ Log prüfen: `cat ~/Library/Logs/taf_server_wol.log`

**QLab-Katalog leer**
→ Syncthing läuft? QLab-Projekte in `/srv/qlab_backup/` vorhanden?  
→ `python3 qlab_media_collector.py --dry-run` auf dem Server ausführen  
→ `ffmpeg` installiert? (`ffprobe -version`)

**Syncthing koppelt nicht**
→ Geräte-IDs korrekt eingetragen?  
→ Firewall: Port 22000 (TCP+UDP) freigeben  
→ Beide Geräte im gleichen Netz?

---

## Dateien im Repository

| Datei | Gerät | Zweck |
|-------|-------|-------|
| `mediaplayer_app.py` | Pi | Kivy-App: Touch-UI, Diashow, Kuration |
| `sync_onedrive.py` | Server | OneDrive → /srv/media, Optimierung, Qualitätsanalyse |
| `genres.py` | Server | Hierarchische Genres an Produktionen zuweisen |
| `qlab_media_collector.py` | Server | QLab-Backup scannen, Katalog aufbauen |
| `qlab_web.py` | Server | Webinterface Medienbibliothek (Port 5000) |
| `server_setup.sh` | Server | Einmalige Einrichtung des Dell Optiplex |
| `server_watchdog.sh` | Server | Automatischer Shutdown wenn keine Geräte aktiv |
| `pi_setup.sh` | Pi | Einmalige Einrichtung eines Raspberry Pi |
| `update_to_release.sh` | Server + Pi | Beim Start auf neueste Release (Git-Tag) umstellen |
| `mac_wol.sh` | MacBook | Server per WoL wecken (perl, kein Python nötig) |
| `mac_wol_setup.sh` | MacBook | launchd-Agent für automatischen WoL einrichten |
| `face_exclude.py` | Server | Personen per Gesichtserkennung aus Diashows ausblenden |
| `taf-pull.sh` | Server + Pi | Manuelles Git-Update aus detached HEAD |
| `Anleitung_Mediaplayer.md` | – | Benutzeranleitung für Theatermitarbeiter |
