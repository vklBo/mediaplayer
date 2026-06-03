#!/usr/bin/env python3
# =============================================================================
# qlab_web.py – Webinterface für die QLab-Medienbibliothek
#
# Startet einen kleinen Flask-Webserver auf Port 5000.
# Aufruf: python3 qlab_web.py
# Dann im Browser: http://<SERVER-IP>:5000
# =============================================================================

from flask import Flask, jsonify, request, send_from_directory, Response
from pathlib import Path
import json

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

QLAB_MEDIA_DIR = Path('/srv/qlab_media')
KATALOG_PATH   = QLAB_MEDIA_DIR / 'katalog.json'
PORT           = 5000

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__)


def lade_katalog() -> list:
    if not KATALOG_PATH.exists():
        return []
    try:
        return json.loads(KATALOG_PATH.read_text('utf-8'))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route('/api/katalog')
def api_katalog():
    katalog  = lade_katalog()
    q        = request.args.get('q', '').lower().strip()
    kategorie = request.args.get('kategorie', '').strip()
    projekt  = request.args.get('projekt', '').strip()
    typ      = request.args.get('typ', '').strip()

    ergebnisse = katalog

    if q:
        def treffer(e):
            suchfelder = [
                e.get('dateiname', ''),
                e.get('kategorie', ''),
                e.get('format', ''),
                ' '.join(e.get('projekte', [])),
                e.get('tags', {}).get('title', ''),
                e.get('tags', {}).get('comment', ''),
                e.get('tags', {}).get('genre', ''),
            ]
            return any(q in f.lower() for f in suchfelder)
        ergebnisse = [e for e in ergebnisse if treffer(e)]

    if kategorie:
        ergebnisse = [e for e in ergebnisse if e.get('kategorie') == kategorie]

    if typ:
        ergebnisse = [e for e in ergebnisse if e.get('typ') == typ]

    if projekt:
        ergebnisse = [e for e in ergebnisse if projekt in e.get('projekte', [])]

    # Sortierung: nach Kategorie, dann Dateiname
    ergebnisse.sort(key=lambda e: (e.get('kategorie', ''), e.get('dateiname', '')))

    return jsonify(ergebnisse)


@app.route('/api/filter-optionen')
def api_filter_optionen():
    katalog = lade_katalog()
    return jsonify({
        'kategorien': sorted({e.get('kategorie', '') for e in katalog if e.get('kategorie')}),
        'projekte':   sorted({p for e in katalog for p in e.get('projekte', [])}),
        'typen':      sorted({e.get('typ', '') for e in katalog if e.get('typ')}),
    })


@app.route('/media/<path:pfad>')
def serve_media(pfad):
    return send_from_directory(QLAB_MEDIA_DIR, pfad)


# ---------------------------------------------------------------------------
# HTML-Interface (alles in einer Datei, kein templates/-Ordner nötig)
# ---------------------------------------------------------------------------

HTML = '''<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TaF Medienbibliothek</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0; min-height: 100vh; }
  header { background: #16213e; padding: 1.2rem 2rem; display: flex; align-items: center; gap: 1rem;
           border-bottom: 2px solid #0f3460; }
  header h1 { font-size: 1.4rem; color: #e94560; }
  header span { color: #888; font-size: 0.9rem; }

  .toolbar { padding: 1rem 2rem; background: #16213e; display: flex; gap: .8rem;
             flex-wrap: wrap; align-items: center; border-bottom: 1px solid #0f3460; }
  input[type=search] { flex: 1; min-width: 200px; padding: .5rem .8rem; border-radius: 6px;
                       border: 1px solid #0f3460; background: #1a1a2e; color: #e0e0e0;
                       font-size: 1rem; }
  select { padding: .5rem .8rem; border-radius: 6px; border: 1px solid #0f3460;
           background: #1a1a2e; color: #e0e0e0; font-size: .9rem; }
  .count { color: #888; font-size: .9rem; margin-left: auto; white-space: nowrap; }

  table { width: 100%; border-collapse: collapse; }
  thead { position: sticky; top: 0; background: #16213e; z-index: 10; }
  th { padding: .6rem 1rem; text-align: left; font-size: .8rem; text-transform: uppercase;
       letter-spacing: .05em; color: #888; border-bottom: 1px solid #0f3460; }
  td { padding: .5rem 1rem; border-bottom: 1px solid #0f3460; vertical-align: top;
       font-size: .9rem; }
  tr:hover td { background: #16213e; }

  .kat-badge { display: inline-block; padding: .15rem .5rem; border-radius: 4px;
               font-size: .75rem; font-weight: 600; }
  .kat-sfx            { background: #2d4a7a; color: #7eb8f7; }
  .kat-stings         { background: #2d4a3a; color: #7ef7a8; }
  .kat-musik_ambience { background: #4a2d5a; color: #c87ef7; }
  .kat-video          { background: #4a3a2d; color: #f7c87e; }
  .kat-bilder         { background: #3a4a2d; color: #c8f77e; }

  .projekt-tag { display: inline-block; background: #0f3460; color: #a0c0e0;
                 padding: .1rem .4rem; border-radius: 3px; font-size: .75rem;
                 margin: .1rem .1rem 0 0; }

  .play-btn { background: #e94560; border: none; color: white; border-radius: 4px;
              padding: .2rem .5rem; cursor: pointer; font-size: .8rem; white-space: nowrap; }
  .play-btn:hover { background: #c73050; }

  .meta { color: #888; font-size: .8rem; }
  .filename { font-weight: 500; color: #e0e0e0; }
  .tag-title { color: #a0c8e0; font-size: .8rem; }

  audio { width: 100%; margin-top: .3rem; height: 28px; }
  .audio-row { display: none; }
  .audio-row td { padding: .3rem 1rem .6rem; background: #16213e; }

  #player-bar { position: fixed; bottom: 0; left: 0; right: 0; background: #16213e;
                border-top: 1px solid #0f3460; padding: .5rem 2rem;
                display: none; align-items: center; gap: 1rem; }
  #player-bar .filename { font-size: .9rem; min-width: 200px; }
  #player-bar audio { flex: 1; height: 32px; }

  .empty { text-align: center; padding: 4rem; color: #555; }
  .wrapper { padding-bottom: 70px; }
</style>
</head>
<body>
<header>
  <h1>🎭 TaF Medienbibliothek</h1>
  <span id="subtitle">QLab-Medien</span>
</header>

<div class="toolbar">
  <input type="search" id="suche" placeholder="Suchen nach Dateiname, Tag, Projekt …" oninput="suchen()">
  <select id="filter-typ" onchange="suchen()">
    <option value="">Alle Typen</option>
  </select>
  <select id="filter-kat" onchange="suchen()">
    <option value="">Alle Kategorien</option>
  </select>
  <select id="filter-projekt" onchange="suchen()">
    <option value="">Alle Projekte</option>
  </select>
  <span class="count" id="count">–</span>
</div>

<div class="wrapper">
<table id="tabelle">
  <thead>
    <tr>
      <th>Datei</th>
      <th>Kategorie</th>
      <th>Dauer</th>
      <th>Details</th>
      <th>Projekte</th>
      <th></th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
<div class="empty" id="leer" style="display:none">Keine Einträge gefunden.</div>
</div>

<div id="player-bar">
  <span class="filename" id="player-name"></span>
  <audio id="player" controls></audio>
</div>

<script>
let katalog = [];
let aktiveAudioRow = null;

async function init() {
  const opts = await fetch('/api/filter-optionen').then(r => r.json());

  const selTyp = document.getElementById('filter-typ');
  opts.typen.forEach(t => selTyp.add(new Option(t, t)));

  const selKat = document.getElementById('filter-kat');
  const katLabels = {sfx:'SFX (< 5s)', stings:'Stings (5–60s)',
                     musik_ambience:'Musik / Ambience', video:'Video', bilder:'Bilder'};
  opts.kategorien.forEach(k => selKat.add(new Option(katLabels[k] || k, k)));

  const selPrj = document.getElementById('filter-projekt');
  opts.projekte.forEach(p => selPrj.add(new Option(p, p)));

  document.getElementById('subtitle').textContent =
    `${opts.projekte.length} Projekte`;

  await suchen();
}

async function suchen() {
  const q   = document.getElementById('suche').value;
  const typ = document.getElementById('filter-typ').value;
  const kat = document.getElementById('filter-kat').value;
  const prj = document.getElementById('filter-projekt').value;

  const params = new URLSearchParams();
  if (q)   params.set('q', q);
  if (typ) params.set('typ', typ);
  if (kat) params.set('kategorie', kat);
  if (prj) params.set('projekt', prj);

  katalog = await fetch('/api/katalog?' + params).then(r => r.json());
  renderTabelle(katalog);
}

function renderTabelle(daten) {
  const tbody = document.getElementById('tbody');
  const leer  = document.getElementById('leer');
  document.getElementById('count').textContent =
    daten.length === 1 ? '1 Datei' : `${daten.length} Dateien`;

  if (daten.length === 0) {
    tbody.innerHTML = '';
    leer.style.display = '';
    return;
  }
  leer.style.display = 'none';

  tbody.innerHTML = daten.map((e, i) => {
    const kat = e.kategorie || '';
    const istAudio = e.typ === 'audio';
    const istVideo = e.typ === 'video';
    const tagTitle = e.tags?.title ? `<div class="tag-title">${esc(e.tags.title)}</div>` : '';
    const tagComment = e.tags?.comment
      ? `<div class="meta">${esc(e.tags.comment.substring(0,80))}</div>` : '';
    const projekte = (e.projekte || [])
      .map(p => `<span class="projekt-tag">${esc(p)}</span>`).join('');

    let details = '';
    if (e.typ === 'audio') {
      const ch = e.kanaele === 1 ? 'Mono' : e.kanaele === 2 ? 'Stereo' : `${e.kanaele}ch`;
      details = `<span class="meta">${e.audio_codec || ''} · ${ch}`;
      if (e.samplerate) details += ` · ${(e.samplerate/1000).toFixed(1)} kHz`;
      details += `</span>`;
    } else if (e.typ === 'video') {
      details = `<span class="meta">${e.video_codec || ''} · ${e.breite}×${e.hoehe}</span>`;
    } else {
      details = `<span class="meta">${e.breite ? e.breite+'×'+e.hoehe : ''}</span>`;
    }

    const groesse = e.groesse_bytes > 1048576
      ? `${(e.groesse_bytes/1048576).toFixed(1)} MB`
      : `${Math.round(e.groesse_bytes/1024)} KB`;

    const playBtn = (istAudio || istVideo)
      ? `<button class="play-btn" onclick="togglePlay(${i})">▶ Play</button>` : '';
    const downloadBtn =
      `<a class="play-btn" style="text-decoration:none;margin-left:.3rem"
         href="/media/${esc(e.pfad_relativ)}" download>↓</a>`;

    return `
      <tr id="row-${i}">
        <td><div class="filename">${esc(e.dateiname)}</div>${tagTitle}${tagComment}</td>
        <td><span class="kat-badge kat-${kat}">${kat}</span></td>
        <td><span class="meta">${e.dauer_formatiert || ''}</span><br>
            <span class="meta">${groesse}</span></td>
        <td>${details}</td>
        <td>${projekte}</td>
        <td>${playBtn}${downloadBtn}</td>
      </tr>
      <tr class="audio-row" id="audio-row-${i}">
        <td colspan="6">
          <audio controls src="/media/${esc(e.pfad_relativ)}"
                 onended="closePlayer(${i})"></audio>
        </td>
      </tr>`;
  }).join('');
}

function togglePlay(i) {
  const row = document.getElementById(`audio-row-${i}`);
  if (aktiveAudioRow && aktiveAudioRow !== row) {
    aktiveAudioRow.style.display = 'none';
    aktiveAudioRow.querySelector('audio').pause();
  }
  if (row.style.display === 'table-row') {
    row.style.display = 'none';
    row.querySelector('audio').pause();
    aktiveAudioRow = null;
  } else {
    row.style.display = 'table-row';
    row.querySelector('audio').play();
    aktiveAudioRow = row;
  }
}

function closePlayer(i) {
  document.getElementById(`audio-row-${i}`).style.display = 'none';
}

function esc(s) {
  return String(s || '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

init();
</script>
</body>
</html>'''


@app.route('/')
def index():
    return Response(HTML, mimetype='text/html')


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if not KATALOG_PATH.exists():
        print(f'Katalog nicht gefunden: {KATALOG_PATH}')
        print('→ Zuerst ausführen: python3 qlab_media_collector.py')
    print(f'Webinterface läuft auf http://0.0.0.0:{PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False)
