# TaF Mediaplayer – Benutzeranleitung

---

## 1. Überblick

Der TaF Mediaplayer zeigt Theaterfotografien auf einem Touchscreen-Display. Bilder sind nach Spielzeiten und Produktionen geordnet und laufen automatisch als Diashow. Die Bedienung erfolgt per Finger-Touch oder Maus – es sind keine Computerkenntnisse erforderlich.

---

## 2. Start

Die App startet automatisch beim Einschalten des Geräts. Sind Bilder vorhanden, beginnt sofort eine Diashow mit allen Fotos in zufälliger Reihenfolge.

Um zur Übersicht zurückzukehren, in der Diashow auf **← Zurück** tippen.

---

## 3. Startbildschirm (Spielzeiten-Übersicht)

Der Startbildschirm zeigt alle verfügbaren Spielzeiten als Bildkacheln. Oben in der Leiste befinden sich folgende Buttons:

| Button | Funktion |
|---|---|
| **▶ Alle Bilder** | Diashow mit sämtlichen Fotos in zufälliger Reihenfolge |
| **🎭 Genres** | Genre-Navigation (nur sichtbar wenn Genres vergeben sind) |
| **Kuratieren** | Kurationsmodus zum Verwalten von Fotos *(PIN erforderlich)* |
| **Autostart** | Automatischen Start konfigurieren *(PIN erforderlich)* |

Eine Spielzeit-Kachel antippen öffnet die Produktionen dieser Spielzeit.

---

## 4. Produktionen einer Spielzeit

Nach dem Antippen einer Spielzeit erscheinen alle Produktionen als Kacheln. Eine Kachel antippen startet sofort die Diashow dieser Produktion.

---

## 5. Genre-Navigation

Genres sind hierarchisch aufgebaut, z. B. »Junges Ensemble → Kinder«. Die Navigation funktioniert stufenweise:

1. Auf dem Startbildschirm **🎭 Genres** antippen.
2. Eine Oberkategorie antippen – die Unterkategorien werden angezeigt.
3. Auf jeder Ebene mit Bildern erscheint oben **▶ Alle anzeigen (N Bilder)** – startet die Diashow mit allen Bildern dieser und aller darunter liegenden Kategorien.
4. Eine Unterkategorie ohne weitere Unterebenen direkt antippen startet die Diashow.

---

## 6. Diashow

Bilder wechseln automatisch alle 5 Sekunden. Die Steuerleiste am unteren Bildschirmrand enthält:

| Button | Funktion |
|---|---|
| **← Zurück** | Zurück zur Übersicht |
| **‹ / ›** | Manuell zum vorherigen / nächsten Bild springen |
| **✗** *(grau)* | Schnell-Kuration aktivieren *(PIN erforderlich, siehe Abschnitt 7)* |

> *Gelegentlich erscheinen Förderbilder – sie werden mit »★ Unsere Förderer« gekennzeichnet und können nicht ausgeblendet werden.*

---

## 7. Fotos während der Diashow ausblenden (Schnell-Kuration)

Mit dieser Funktion können unpassende Fotos direkt während der Vorstellung ausgeblendet werden, ohne den laufenden Betrieb zu unterbrechen.

### Modus aktivieren

1. **✗** in der Steuerleiste antippen.
2. PIN eingeben.
3. Der Button wird rot und zeigt **✗ ausblenden** – der Modus ist aktiv.

### Foto ausblenden

**✗ ausblenden** antippen – das aktuelle Foto wird sofort entfernt und das nächste Bild erscheint automatisch.

### Modus beenden

**✗ ausblenden** antippen, ohne ein Bild auszuschließen – der Modus schaltet sich ab.

> *Automatische Deaktivierung: Der Modus schaltet sich 60 Sekunden nach dem letzten Ausschluss selbst ab.*

Ausgeblendete Bilder verschwinden aus der Diashow, bleiben aber im Kurationsmodus sichtbar und können dort jederzeit wieder eingeschlossen werden.

---

## 8. Kurationsmodus

Der Kurationsmodus ermöglicht die detaillierte Verwaltung aller Fotos.

**Zugang:** Startbildschirm → **Kuratieren** → PIN eingeben

### Produktionen ein- oder ausschließen

Nach dem Antippen einer Spielzeit werden alle Produktionen angezeigt. Für jede Produktion stehen folgende Aktionen zur Verfügung:

| Button | Funktion |
|---|---|
| **Bilder →** | Einzelbild-Kuration dieser Produktion öffnen |
| **✗ ausschl.** | Gesamte Produktion aus der Diashow ausblenden |
| **✓ einschl.** | Produktion wieder in die Diashow aufnehmen |
| **🗑** | Produktion unwiderruflich löschen *(PIN erforderlich)* |

> *Produktionen mit gelbem oder orangenem ⚠-Zeichen enthalten Bilder, die automatisch als qualitativ ungenügend erkannt wurden (unscharf, zu dunkel oder verrauscht).*

### Einzelbilder kuratieren

Nach dem Antippen von **Bilder →** werden alle Fotos als kleine Kacheln angezeigt:

| Button | Funktion |
|---|---|
| **✓ ein / ✗ aus** | Einzelnes Bild ein- oder ausschließen |
| **🗑 löschen / ↩ behalten** | Bild zum endgültigen Löschen markieren (oder Markierung aufheben) |
| **Alle ein / Alle aus** | Alle Bilder der Produktion auf einmal umschalten |
| **⚠ Nur markierte** | Nur Bilder mit erkannten Qualitätsproblemen anzeigen |
| **💾 Speichern** | Alle Änderungen übernehmen |

> *Wichtig: Das Löschen von Bildern ist unwiderruflich. Erst* ***💾 Speichern*** *drücken, wenn alle Entscheidungen getroffen sind.*

---

## 9. Autostart konfigurieren

Der Autostart legt fest, welche Diashow beim nächsten Einschalten automatisch startet. Ohne Konfiguration werden alle Bilder in zufälliger Reihenfolge gezeigt.

**Zugang:** Startbildschirm → **Autostart** → PIN eingeben

| Option | Funktion |
|---|---|
| **✕ Kein Autostart** | Beim Start alle Bilder zufällig anzeigen |
| **🎭 Genre auswählen** | Genre-Baum navigieren, Ebene mit **▶ Diese Kategorie** bestätigen |
| **📁 Produktion auswählen** | Spielzeit und dann Produktion antippen |

Die Einstellung wird sofort gespeichert und gilt ab dem nächsten Start.

Unterhalb der Auswahlbuttons wird angezeigt, welche Einstellung gerade aktiv ist und woher sie stammt:
- *Lokale Einstellung auf pi4* – am Gerät selbst gesetzt, hat Vorrang
- *Zentral (OneDrive) für pi4* – aus der zentralen Konfigurationsdatei
- *Keine Einstellung – alle Bilder* – kein Autostart konfiguriert

Ist eine lokale Einstellung aktiv und gleichzeitig eine zentrale vorhanden, erscheint zusätzlich der Button **↩ Zentrale Einstellung übernehmen** – damit wird die lokale Überschreibung gelöscht und die OneDrive-Vorgabe gilt wieder.

---

## 10.  Zentrale Autostart-Konfiguration (für Administratoren)

Die Intendanz kann den Autostart für alle Displays zentral über OneDrive steuern, ohne die Geräte vor Ort zu bedienen.

**Datei anlegen:** `Fotos/Konfiguration/autostart_config.txt` in OneDrive mit folgendem Inhalt:

```
# Autostart-Konfiguration pro Display
# Hostname = Einstellung
pi4 = genre:JungesEnsemble/Kinder
pi5 = folder:2024-25/Faust
* = genre:Drama
```

**Format:**
- `hostname = genre:Kategorie/Unterkategorie` – startet ein Genre (inkl. Unterkategorien)
- `hostname = folder:Spielzeit/Produktion` – startet eine bestimmte Produktion
- `*` als Hostname = Fallback für alle Displays ohne eigenen Eintrag
- Zeilen mit `#` sind Kommentare

Die Datei wird beim nächsten Sync-Lauf automatisch auf alle Displays übertragen. Eine am Display selbst gesetzte Einstellung (Abschnitt 9) hat immer Vorrang gegenüber der zentralen Konfiguration.
