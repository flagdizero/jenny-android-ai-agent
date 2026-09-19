"""Toccare Jenny in casa: se ne va al bordo, e ritoccandola torna fuori.

Il gesto e' lo stesso dell'officina e il modulo che lo ascolta e' lo stesso
(`shared/mascot-drag.js`); quel che cambia fra i due gusci e' cosa si disegna
nei due stati, e sta qui. Tre cose che si vedono solo provandole:

* **al bordo il disegno e' uno solo.** La posa di profilo ha la faccia gia'
  dentro, e sporge da li' meno di meta' Jenny: lasciare acceso il livello del
  volto frontale vorrebbe dire incollarle una faccia sulla nuca. Non e' un caso
  di stile — e' un difetto che si vede a colpo d'occhio e che nessun test
  sull'ancoraggio prenderebbe;
* **la bocca si muove lo stesso**, perche' messa via non vuol dire zittita: se
  sta rispondendo lo si deve poter vedere anche dal bordo;
* **il dondolio del pensa invece no.** Mezza Jenny che oscilla contro il bordo
  dello schermo somiglia a un guasto della pagina.

I membri si estraggono dal sorgente e girano in node, come gli altri banchi
della casa: `casa-mascot.js` importa i moduli condivisi, e importarlo davvero
vorrebbe dire tirarsi dentro `localStorage` e il ponte nativo per provare due
`src` di immagine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MASCOT_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "casa-mascot.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


def _initial_class(source: str) -> str:
    """La classe con cui lo sprite nasce.

    Si legge dal sorgente invece di scriverla nel banco: e' li' che vive la
    decisione «la casa si apre con Jenny fuori, non gia' messa via», e un banco
    che se la ricopia non la sta piu' misurando.
    """
    m = re.search(r"this\.el\.className = '([^']+)';", source)
    assert m, "lo sprite non riceve piu' una classe alla nascita"
    return m.group(1)


def _const_block(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^const {re.escape(name)} = \{{.*?^\}};", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


_HARNESS = """
import assert from 'node:assert/strict';

__TABELLE__

/* Un nodo finto con la sola meccanica che il disegno usa: le classi e `src`.
   Niente stili, niente layout: cosa *diventa* l'ancoraggio lo dice il CSS e
   non questo banco. */
function makeEl() {
  const el = { src: '', hidden: false, className: '' };
  el.classList = {
    add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
    remove(n) {
      el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
    },
    contains(n) { return String(el.className).split(' ').includes(n); },
    toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
  };
  return el;
}

class Jenny {
  constructor() {
    this.el = makeEl();
    this.el.className = '__CLASSE__';
    this.body = makeEl();
    this.face = makeEl();
    this.visible = true;
    this.state = 'idle';
    this.mood = null;
    this._moodUntil = 0;
    this._mouthOpen = false;
    this.aree = 0;
  }
  _updateGestureExclusion() { this.aree += 1; }
  _activeMood() {
    if (!this.mood || Date.now() >= this._moodUntil) return null;
    return this.mood;
  }
__MEMBRI__
}

__CORPO__
console.log('OK');
"""


def _run(corpo: str) -> str:
    src = MASCOT_JS.read_text(encoding="utf-8")
    tabelle = "\n".join(_const_block(src, n) for n in ("ART", "BODY", "FACE"))
    membri = "\n".join(_member(src, n) for n in ("setOut", "_paint"))
    script = (
        _HARNESS.replace("__TABELLE__", tabelle)
        .replace("__MEMBRI__", membri)
        .replace("__CLASSE__", _initial_class(src))
        .replace("__CORPO__", corpo)
    )
    done = subprocess.run(
        [_NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr or done.stdout
    return done.stdout


def test_a_tap_puts_her_away_and_another_brings_her_back() -> None:
    """Il giro completo. Le due chiamate sono quelle che il tocco fa fare a
    `setOut`, e la classe e' l'unico appiglio che il CSS ha per ancorarla."""
    _run(
        """
const j = new Jenny();
assert.equal(j.el.classList.contains('out'), true, 'nasce fuori');
j.setOut(false);
assert.equal(j.el.classList.contains('out'), false, 'il tocco non la mette via');
j.setOut(true);
assert.equal(j.el.classList.contains('out'), true, 'il secondo tocco non la riporta fuori');
"""
    )


def test_at_the_edge_she_is_one_drawing_with_the_face_already_in_it() -> None:
    """La posa di profilo e il livello del volto spento. Sono due asserzioni e
    non una: tenere acceso il volto sopra un'arte che ce l'ha gia' dentro e' il
    difetto vero, e il solo `src` del corpo non lo vedrebbe."""
    _run(
        """
const j = new Jenny();
j.setOut(false);
assert.equal(j.body.src, ART.side, 'al bordo non disegna la posa di profilo');
assert.equal(j.face.classList.contains('off'), true, 'la faccia frontale resta accesa');
j.setOut(true);
assert.equal(j.body.src, BODY.idle, 'fuori non torna il corpo frontale');
assert.equal(j.face.classList.contains('off'), false, 'fuori la faccia resta spenta');
assert.equal(j.face.src, FACE.normal, 'fuori la faccia non e\\u2019 quella normale');
"""
    )


def test_the_mouth_still_moves_when_she_is_put_away() -> None:
    """Messa via non vuol dire zittita: la coppia cotta side/side-talk e' il
    solo modo che ha di dire, da li', che sta rispondendo."""
    _run(
        """
const j = new Jenny();
j.state = 'talking';
j.setOut(false);
assert.equal(j.body.src, ART.side, 'bocca chiusa: e\\u2019 la posa ferma');
j._mouthOpen = true;
j._paint();
assert.equal(j.body.src, ART.sideTalk, 'bocca aperta: la posa non cambia');
j._mouthOpen = false;
j._paint();
assert.equal(j.body.src, ART.side, 'la bocca non si richiude piu\\u2019');
"""
    )


def test_she_does_not_sway_against_the_edge() -> None:
    """Il dondolio del pensa e' della Jenny venuta fuori: al bordo la classe se
    ne va, ed e' quella che il CSS legge per animarla."""
    _run(
        """
const j = new Jenny();
j.state = 'thinking';
j._paint();
assert.equal(j.el.classList.contains('thinking'), true, 'fuori non dondola piu\\u2019');
j.setOut(false);
assert.equal(j.el.classList.contains('thinking'), false, 'dondola anche dal bordo');
assert.equal(j.body.src, ART.side, 'al bordo disegna ancora il corpo del pensa');
j.setOut(true);
assert.equal(j.el.classList.contains('thinking'), true, 'tornata fuori non dondola piu\\u2019');
"""
    )


def test_a_mood_does_not_leak_onto_the_edge_pose() -> None:
    """L'umore e' una faccia, e al bordo la faccia non c'e'. Senza il ritorno
    anticipato la riga dell'umore scriverebbe un volto frontale sopra la posa
    di profilo — visibile solo nei dodici secondi dopo una risposta."""
    _run(
        """
const j = new Jenny();
j.mood = 'happy';
j._moodUntil = Date.now() + 10000;
j.setOut(false);
assert.equal(j.body.src, ART.side);
assert.equal(j.face.classList.contains('off'), true, 'l\\u2019umore riaccende la faccia al bordo');
j.setOut(true);
assert.equal(j.face.src, FACE.happy, 'fuori l\\u2019umore non si vede piu\\u2019');
"""
    )


def test_putting_her_away_redeclares_her_area_to_android() -> None:
    """Il rettangolo di Jenny e' escluso dalle gesture di sistema: se cambia
    posto e nessuno lo ridice, uno swipe dal bordo torna a essere Indietro
    invece che il suo trascinamento."""
    _run(
        """
const j = new Jenny();
const prima = j.aree;
j.setOut(false);
assert.equal(j.aree, prima + 1, 'l\\u2019area esclusa non viene ridichiarata');
"""
    )


def test_hidden_she_stays_hidden() -> None:
    """Spenta dalle impostazioni non si disegna, e il ramo del bordo non deve
    scavalcare quella decisione."""
    _run(
        """
const j = new Jenny();
j.visible = false;
j.setOut(false);
assert.equal(j.el.hidden, true, 'spenta ma disegnata');
assert.equal(j.body.src, '', 'spenta e disegna lo stesso');
"""
    )
