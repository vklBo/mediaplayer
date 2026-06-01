# Python-Script zur automatischen Anzeige von Videos und Bildern auf einem Display
#
# Prüft, ob ein USB-Stick gemountet ist und kopiert ggf. die Dateien lokal
# Durchsucht das Bilderverzeichnis nach Videos und Bildern
# Zeigt zunächst alle Videos, dann alle Bilder
# Wiederholt das Ganze in Endlosschleife


import os
import subprocess
import asyncio
import time
import shutil
import psutil


VIDEO_EXTENSIONS = ['*.mp4', '*.avi', '*.mkv', '*.mov']
IMAGE_EXTENSIONS = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tiff', '*.gif']
ALLOWED_EXTENSIONS = [ext.lstrip('*') for ext in VIDEO_EXTENSIONS] + [ext.lstrip('*') for ext in IMAGE_EXTENSIONS]


BASIS_NAME = "medienbasis"
MEDIA_DIR = os.path.join(os.path.expanduser("~"), "media")
BASIS_DIR = os.path.join(os.path.expanduser("~"), BASIS_NAME)

SKRIPTE_NAME = "skripte"
MEDIA_DIR = os.path.join(os.path.expanduser("~"), "media")
SKRIPTE_DIR = os.path.join(os.path.expanduser("~"), SKRIPTE_NAME)

USB_BASE_PATH = '/media/taf/'
MEDIA_COMMAND = ["python3", "/home/taf/play_mediafiles.py"]
                            
                            
def is_x_server_running():
    try:
        # Überprüfe mit xset, ob der X-Server läuft
        xset = subprocess.run(['xset', 'q'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if xset.returncode == 0:
            return True

    except Exception as e:
        print(f"Fehler beim Überprüfen des X-Servers: {e}")

    return False


# Funktion zum Beenden eines Prozesses
def kill_process(process):
    try:
        process.terminate()  # Versuche, den Prozess zu beenden
    except psutil.NoSuchProcess:  # Wenn der Prozess nicht gefunden wird
        pass  # Fortfahren, ohne einen Fehler auszulösen

def kill_medienprozesse():
    # Durchlaufe alle laufenden Prozesse
    for process in psutil.process_iter(['pid', 'name']):
        try:
            # Überprüfe, ob der Prozess feh oder vlc ist
            if process.name() == 'feh' or process.name() == 'vlc':
                # Beende den Prozess
                print (f"Töte {process}")
                kill_process(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass  # Fortfahren, wenn auf den Prozess nicht zugegriffen werden kann

                              
def get_raspberry_pi_model():
    # Pfad zur Datei, in der das Ergebnis gespeichert wird
    file_path = "/home/taf/pi_version.txt"
    
    try:
        # Versuche, das Ergebnis aus der Datei zu lesen
        with open(file_path, "r") as file:
            version = file.read().strip()
            if version:
                return version
    except FileNotFoundError:
        pass
    
    try:
        # Führe das lshw-Befehl aus und lese die Ausgabe
        result = subprocess.run(['lshw', '-C', 'system'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        output = result.stdout
        
        product_line = "unbekannt"
        for line in output.split('\n'):
            print (line)
            if 'product:' in line:
                product_line = line.split(':')[1].strip()
        
        if product_line != "unbekannt":
            with open(file_path, "w") as file:
                file.write(product_line)
        return product_line

    except subprocess.CalledProcessError:
        return "Fehler beim Ausführen von lshw"

def get_usb_path():
    # Sucht nach dem USB-Stick unter /media/pi/
    #print(".", end='')

    for device in os.listdir(USB_BASE_PATH):
        device_path = os.path.join(USB_BASE_PATH, device)
        if os.path.ismount(device_path):
            return device_path
    return None

def clear_directory(directory):
    # Löscht alle Dateien und Verzeichnisse im angegebenen Verzeichnis
    print (f'Lösche Verzeichnis {directory}')
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Fehler beim Löschen {file_path}. Grund: {e}')


def copy_usb_contents_to_directory(usb_path, target_directory):
    # Kopiert den Inhalt des USB-Sticks in das Zielverzeichnis und konvertiert Datei-Endungen zu Kleinbuchstaben
    print(f'Kopiere {usb_path} nach {target_directory}.')
    for item in os.listdir(usb_path):
        s = os.path.join(usb_path, item)

        if os.path.isdir(s):
            copy_usb_contents_to_directory(s, target_directory)
            continue

        # Konvertiere Dateinamen-Endung in Kleinbuchstaben
        if '.' in item:
            name, ext = os.path.splitext(item)
            ext = ext.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue

            d = os.path.join(target_directory, name + ext)
        else:
            continue  # Wenn keine Endung vorhanden ist, wird die Datei übersprungen
        print (item)

        try:
            #print(f'Kopiere {s} nach {d}.')
            shutil.copy2(s, d)
        except Exception as e:
            print(f'Fehler beim Kopieren {s} nach {d}. Grund: {e}')


def unmount_usb(usb_path):
    # Unmount den USB-Stick
    try:
        #subprocess.run(['sudo', 'umount', usb_path], check=True)
        subprocess.run(['umount', usb_path], check=True)

        print(f'USB-Stick {usb_path} erfolgreich ausgeworfen. Der Stick kann abgezogen werden.')
    except subprocess.CalledProcessError as e:
        print(f'Fehler beim Unmounten von {usb_path}. Grund: {e}')


async def starte_medienanzeige_async():
    print (f"Starte Medienanzeige {MEDIA_COMMAND}")
    process = await asyncio.create_subprocess_exec(*MEDIA_COMMAND)
    print (f"Prozess: {process}")
    return process

# Hauptlogik
async def main():
    model = get_raspberry_pi_model()
    PLAY_VIDEOS = False
    
    if "Pi 4" in model or "Pi 5" in model:
        PLAY_VIDEOS = True

    print(f"Model: {model} erkannt. Videos werden {'' if PLAY_VIDEOS else 'NICHT '}abgespielt!")

    print("Prüfe X-Server")
    while not is_x_server_running():
        print("X-Server läuft noch nicht.")
        time.sleep(10)

    medien_prozess = None

    while True:        
        time.sleep(5)
        usb_path = get_usb_path()
        if usb_path:
            print(f'USB-Stick gefunden unter {usb_path}. Aktualisiere {MEDIA_DIR}...')
            if medien_prozess:
                print("Stoppe Mediendarstellung.")
                medien_prozess.kill()
                kill_medienprozesse()
                medien_prozess = None
            
            if not os.path.exists(MEDIA_DIR):
                os.makedirs(MEDIA_DIR)
            if not os.path.exists(BASIS_DIR):
                os.makedirs(BASIS_DIR)
            
            # Ggf. Grundstock löschen und kopieren
            basis_usb_path = os.path.join(usb_path, BASIS_NAME)
            if os.path.exists(basis_usb_path):
                print(f'{basis_usb_path} gefunden. {BASIS_DIR} wird aktualisiert.')
                clear_directory(BASIS_DIR)
                copy_usb_contents_to_directory(basis_usb_path, BASIS_DIR)
            
            # Ggf. Skripte kopieren
            skripte_usb_path = os.path.join(usb_path, SKRIPTE_NAME)
            if os.path.exists(skripte_usb_path):
                print(f'{skripte_usb_path} gefunden. Programm wird aktualisiert.')
                copy_usb_contents_to_directory(skripte_usb_path, "/home/taf/")

            clear_directory(MEDIA_DIR)
            copy_usb_contents_to_directory(usb_path, MEDIA_DIR)
            copy_usb_contents_to_directory(BASIS_DIR, MEDIA_DIR)
            print(f'Inhalt von {MEDIA_DIR} erfolgreich aktualisiert.')
            unmount_usb(usb_path)
            
        if not medien_prozess:
            print ("Starte Medienprozess")
            medien_prozess = await starte_medienanzeige_async()
            


if __name__ == "__main__":
    asyncio.run(main())
    

