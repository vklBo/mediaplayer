# Python-Script zur automatischen Anzeige von Videos und Bildern auf einem Display
#
# Prüft, ob ein USB-Stick gemountet ist und kopiert ggf. die Dateien lokal
# Durchsucht das Bilderverzeichnis nach Videos und Bildern
# Zeigt zunächst alle Videos, dann alle Bilder
# Wiederholt das Ganze in Endlosschleife


import os
import subprocess
import random
from pathlib import Path

VIDEO_EXTENSIONS = ['*.mp4', '*.avi', '*.mkv', '*.mov']
IMAGE_EXTENSIONS = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tiff', '*.gif']
ALLOWED_EXTENSIONS = [ext.lstrip('*') for ext in VIDEO_EXTENSIONS] + [ext.lstrip('*') for ext in IMAGE_EXTENSIONS]

MEDIA_DIR = os.path.join(os.path.expanduser("~"), "media")  

SLIDESHOW_DELAY = "5"                            
                              
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


def play_videos_and_images(video_files, image_files):
    # Endlosschleife, die abwechselnd die Videos und Bilder zeigt
    while True:
        print ("Starte Video-Darstellung")
        subprocess.run(['cvlc', '--fullscreen', '--no-osd', '--no-video-title-show', '--one-instance', '--play-and-exit'] + video_files)
        print ("Starte Bilder-Darstellung")
        subprocess.run(['feh', '--fullscreen', '--slideshow-delay', SLIDESHOW_DELAY, '-Y', '-x', '-q', '-D', '5', '-Z', '--auto-zoom', '--image-bg', 'black', '--on-last-slide', 'quit'] + image_files)

# Funktion zum Abspielen von Videos auf einem spezifischen HDMI-Ausgang
def play_videos(video_files):
    print ("Starte Video-Darstellung (endlos)")
    subprocess.run(['cvlc', '--fullscreen', '--no-osd', '--no-video-title-show', '--one-instance', '--loop'] + video_files)

# Funktion zum Abspielen von Bildern auf einem spezifischen HDMI-Ausgang
def play_images(image_files):
    print ("Starte Bilder-Darstellung (endlos)")
#    subprocess.run(['feh',  '--slideshow-delay', '5',  '-D', '5',  '--image-bg', 'black'] + image_files)
    subprocess.run(['feh', '--fullscreen', '--slideshow-delay', SLIDESHOW_DELAY, '-Y', '-x', '-q', '-D', '5', '-Z', '--auto-zoom', '--image-bg', 'black'] + image_files)


# Funktion zum Abspielen von Medien auf einem spezifischen HDMI-Ausgang
def starte_medienanzeige(media_dir, can_play_videos):
    video_files = []
    image_files = []

    if can_play_videos:
        for ext in VIDEO_EXTENSIONS:
            video_files.extend(Path(media_dir).rglob(ext))

    for ext in IMAGE_EXTENSIONS:
        image_files.extend(Path(media_dir).rglob(ext))
    
    print(f'{len(video_files)} Videos und {len(image_files)} Bilder gefunden.')

    if not (video_files or image_files):
        print('Nicht anzuzeigen. Schließe USB-Stick an, um Videos und Bilder zu kopieren.')
        return None

    # Dateien zufällig sortieren
    random.shuffle(video_files)
    random.shuffle(image_files)


    if video_files and image_files:
        play_videos_and_images([str(img) for img in video_files], [str(img) for img in image_files])
    elif video_files:
        play_videos([str(img) for img in video_files])
    elif image_files:
        # Bilder als Diashow anzeigen
        play_images([str(img) for img in image_files])


# Hauptlogik
def main():
    print ("Medienprozess:")
    PLAY_VIDEOS = False
    model = get_raspberry_pi_model()
    if "Pi 4" in model or "Pi 5" in model:
        PLAY_VIDEOS = True

    print(f"Model: {model} erkannt. Videos werden {'' if PLAY_VIDEOS else 'NICHT '}abgespielt!")
    
    starte_medienanzeige(MEDIA_DIR, PLAY_VIDEOS)
        

if __name__ == "__main__":
    main()
