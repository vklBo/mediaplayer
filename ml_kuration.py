#!/usr/bin/env python3
# =============================================================================
# ml_kuration.py – Lerndaten aus Kurationsentscheidungen aufbereiten und
#                  einen Bildqualitäts-Klassifikator trainieren
#
# Aufruf:
#   python3 ml_kuration.py                        # Daten + Modell + Report
#   python3 ml_kuration.py --log pfad/log.jsonl   # anderer Log-Pfad
#   python3 ml_kuration.py --stats-only           # Nur Statistiken, kein Training
#   python3 ml_kuration.py --predict bild.jpg     # Einzelbild bewerten (nach Training)
#
# Voraussetzungen:
#   pip install pandas scikit-learn matplotlib
#
# Ausgabe:
#   kuration_modell.pkl      – trainiertes Modell (Random Forest)
#   kuration_training.csv    – aufbereiteter Trainingsdatensatz
# =============================================================================

import argparse
import json
import sys
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score, train_test_split
    from sklearn.metrics import classification_report, confusion_matrix
    from sklearn.preprocessing import StandardScaler
    import pickle
except ImportError:
    print("Fehlende Pakete. Bitte installieren:")
    print("  pip install pandas scikit-learn")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

DEFAULT_LOG  = Path.home() / 'kuration_log.jsonl'
MODEL_PATH   = Path('kuration_modell.pkl')
CSV_PATH     = Path('kuration_training.csv')

# Features die für das Modell genutzt werden
FEATURES = ['sharpness', 'noise', 'brightness', 'auto_flagged']

# ---------------------------------------------------------------------------
# Daten laden und aufbereiten
# ---------------------------------------------------------------------------

def lade_log(log_path: Path) -> pd.DataFrame:
    """Liest kuration_log.jsonl und gibt einen DataFrame zurück."""
    if not log_path.exists():
        print(f'Log-Datei nicht gefunden: {log_path}')
        print('→ Erst Bilder auf dem Pi kurationieren, dann erneut ausführen.')
        sys.exit(1)

    eintraege = []
    with open(log_path, encoding='utf-8') as f:
        for zeile in f:
            zeile = zeile.strip()
            if zeile:
                try:
                    eintraege.append(json.loads(zeile))
                except json.JSONDecodeError:
                    continue

    if not eintraege:
        print('Log-Datei ist leer.')
        sys.exit(1)

    df = pd.DataFrame(eintraege)
    print(f'  {len(df)} Einträge geladen aus {log_path}')
    return df


def aufbereiten(df: pd.DataFrame) -> pd.DataFrame:
    """
    Erstellt den Trainingsdatensatz mit Label und Features.

    Label (menschliche Entscheidung):
      1 = Bild behalten  (aktion=unveraendert/wiederhergestellt + war nicht ausgeschlossen)
      0 = Bild entfernen (aktion=ausgeschlossen/geloescht,
                          oder unveraendert aber war_ausgeschlossen=True)
    """
    df = df.copy()

    # Duplikate: bei mehrfacher Kuration desselben Bildes nur den letzten Eintrag nehmen
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.sort_values('timestamp')
    df = df.drop_duplicates(subset=['dateiname', 'saison', 'produktion'], keep='last')

    # Label berechnen
    def label(row):
        if row['aktion'] in ('ausgeschlossen', 'geloescht'):
            return 0
        if row['aktion'] == 'wiederhergestellt':
            return 1
        # unveraendert: Vorher-Zustand war ausgeschlossen → bleibt entfernt
        if row.get('war_ausgeschlossen', False):
            return 0
        return 1  # war eingeschlossen und blieb eingeschlossen

    df['label'] = df.apply(label, axis=1)
    df['label_text'] = df['label'].map({1: 'behalten', 0: 'entfernt'})

    # Features bereinigen
    for col in ['sharpness', 'noise', 'brightness']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df['auto_flagged'] = df['auto_flagged'].astype(bool).astype(int)

    # Zeilen mit fehlenden Feature-Werten entfernen (ffprobe hatte Fehler)
    vorher = len(df)
    df = df.dropna(subset=FEATURES)
    if len(df) < vorher:
        print(f'  {vorher - len(df)} Einträge ohne Qualitätsdaten entfernt')

    return df


# ---------------------------------------------------------------------------
# Statistiken
# ---------------------------------------------------------------------------

def zeige_statistiken(df: pd.DataFrame):
    print('\n' + '='*60)
    print('  DATENSATZ-ÜBERSICHT')
    print('='*60)
    print(f'\nGesamt:     {len(df)} Bilder')
    print(f'Behalten:   {(df.label==1).sum()} ({(df.label==1).mean()*100:.1f} %)')
    print(f'Entfernt:   {(df.label==0).sum()} ({(df.label==0).mean()*100:.1f} %)')

    if 'pi' in df.columns:
        print(f'\nNach Pi:')
        for pi, n in df.groupby('pi').size().items():
            print(f'  {pi}: {n} Einträge')

    if 'saison' in df.columns:
        print(f'\nNach Spielzeit:')
        for saison, n in df.groupby('saison').size().items():
            print(f'  {saison}: {n} Bilder')

    print(f'\nFeature-Mittelwerte nach Entscheidung:')
    print(df.groupby('label_text')[['sharpness', 'noise', 'brightness']].mean().round(1).to_string())

    print(f'\nAuto-Flagging vs. menschliche Entscheidung:')
    kreuz = pd.crosstab(
        df['auto_flagged'].map({1: 'auto: flagged', 0: 'auto: ok'}),
        df['label_text'],
        margins=True,
    )
    print(kreuz.to_string())

    # Übereinstimmung Auto-Flagging ↔ Mensch
    auto_richtig = ((df['auto_flagged'] == 1) == (df['label'] == 0)).mean()
    print(f'\nAuto-Flagging trifft menschliche Entscheidung: {auto_richtig*100:.1f} %')


# ---------------------------------------------------------------------------
# Modell trainieren
# ---------------------------------------------------------------------------

def trainiere_modell(df: pd.DataFrame):
    X = df[FEATURES].values
    y = df['label'].values

    if len(df) < 20:
        print(f'\n⚠ Nur {len(df)} Trainingsbeispiele – mehr Kurationsdaten sammeln für ein aussagekräftiges Modell.')
        print('  (Empfehlung: mindestens 200 Bilder kurationieren)')

    # Zwei Modelle vergleichen
    print('\n' + '='*60)
    print('  MODELL-TRAINING')
    print('='*60)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    modelle = {
        'Random Forest':     RandomForestClassifier(n_estimators=100, random_state=42),
        'Logistische Regr.': LogisticRegression(max_iter=1000, random_state=42),
    }

    bestes_modell = None
    bester_score  = 0

    for name, modell in modelle.items():
        X_train_data = X_scaled if name == 'Logistische Regr.' else X
        scores = cross_val_score(modell, X_train_data, y, cv=min(5, len(df)//5 or 2),
                                 scoring='f1')
        print(f'\n{name}:')
        print(f'  F1-Score (Cross-Val): {scores.mean():.3f} ± {scores.std():.3f}')

        if scores.mean() > bester_score:
            bester_score  = scores.mean()
            bestes_modell = (name, modell, X_train_data)

    # Bestes Modell auf gesamtem Datensatz trainieren
    name, modell, X_data = bestes_modell
    modell.fit(X_data, y)
    print(f'\n→ Bestes Modell: {name} (F1={bester_score:.3f})')

    # Feature Importance (Random Forest)
    if hasattr(modell, 'feature_importances_'):
        print('\nFeature-Wichtigkeit:')
        for feat, imp in sorted(zip(FEATURES, modell.feature_importances_),
                                key=lambda x: -x[1]):
            balken = '█' * int(imp * 40)
            print(f'  {feat:15s} {balken} {imp:.3f}')

    # Modell speichern
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump({'modell': modell, 'scaler': scaler, 'features': FEATURES,
                     'name': name}, f)
    print(f'\n✓ Modell gespeichert: {MODEL_PATH}')

    return modell, scaler


# ---------------------------------------------------------------------------
# Einzelbild bewerten
# ---------------------------------------------------------------------------

def bewerte_bild(bild_pfad: str):
    if not MODEL_PATH.exists():
        print(f'Kein Modell gefunden ({MODEL_PATH}). Erst trainieren.')
        sys.exit(1)

    with open(MODEL_PATH, 'rb') as f:
        gespeichert = pickle.load(f)

    modell  = gespeichert['modell']
    scaler  = gespeichert['scaler']
    name    = gespeichert['name']

    try:
        import cv2
        import numpy as np
    except ImportError:
        print('opencv-python fehlt: pip install opencv-python')
        sys.exit(1)

    path = Path(bild_pfad)
    if not path.exists():
        print(f'Datei nicht gefunden: {path}')
        sys.exit(1)

    img = cv2.imread(str(path))
    if img is None:
        print('Bild konnte nicht gelesen werden.')
        sys.exit(1)

    gray         = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bright_mask  = gray > 60
    dark_mask    = gray < 80
    bright_ratio = float(bright_mask.sum()) / gray.size

    if bright_ratio < 0.05:
        sharpness = 0.0
        brightness = int(gray.mean())
    else:
        lap       = cv2.Laplacian(gray, cv2.CV_64F)
        sharpness = float(lap[bright_mask].var())
        brightness = int(gray[bright_mask].mean())

    # Noise (Immerkaer)
    n_dark = int(dark_mask.sum())
    if n_dark >= 2000:
        kernel = np.array([[1,-2,1],[-2,4,-2],[1,-2,1]], dtype=np.float64)
        conv   = cv2.filter2D(gray.astype(np.float64), -1, kernel)
        noise  = float(np.sum(np.abs(conv[dark_mask])) * (0.5*3.14159)**0.5 / (6*n_dark))
    else:
        noise = 0.0

    auto_flagged = int(sharpness < 80 or noise > 9.0 or bright_ratio < 0.05)
    X = np.array([[sharpness, noise, brightness, auto_flagged]])

    if 'Logistische' in name:
        X = scaler.transform(X)

    pred  = modell.predict(X)[0]
    proba = modell.predict_proba(X)[0]

    print(f'\nBewertung: {path.name}')
    print(f'  Schärfe:     {sharpness:.1f}')
    print(f'  Rauschen:    {noise:.2f}')
    print(f'  Helligkeit:  {brightness}')
    print(f'  Auto-Flag:   {"ja" if auto_flagged else "nein"}')
    print(f'\n  Modell ({name}): {"✓ BEHALTEN" if pred == 1 else "✗ ENTFERNEN"}')
    print(f'  Wahrscheinlichkeit: behalten {proba[1]*100:.0f} % / entfernen {proba[0]*100:.0f} %')


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='QLab-Kuration ML-Training')
    parser.add_argument('--log',        default=str(DEFAULT_LOG),
                        help='Pfad zur kuration_log.jsonl')
    parser.add_argument('--stats-only', action='store_true',
                        help='Nur Statistiken, kein Modell trainieren')
    parser.add_argument('--predict',    metavar='BILD',
                        help='Einzelbild mit trainiertem Modell bewerten')
    args = parser.parse_args()

    if args.predict:
        bewerte_bild(args.predict)
        return

    print('Lade Kurationsdaten …')
    df_roh = lade_log(Path(args.log))
    df     = aufbereiten(df_roh)

    zeige_statistiken(df)

    # CSV speichern
    df.to_csv(CSV_PATH, index=False, encoding='utf-8')
    print(f'\n✓ Trainingsdaten gespeichert: {CSV_PATH}')

    if not args.stats_only:
        if len(df) < 10:
            print('\n⚠ Zu wenige Daten für Training (< 10 Einträge).')
            print('  Mehr Bilder kurationieren und dann erneut ausführen.')
        else:
            trainiere_modell(df)

    print('\nFertig.')


if __name__ == '__main__':
    main()
