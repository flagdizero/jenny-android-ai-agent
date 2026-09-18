"""Le tabelle degli sprite della casa sono una copia: questo e' cio' che la tiene onesta.

``casa-mascot.js`` duplica ``BODY``, ``FACE`` e ``MOOD_FACES`` da
``mobile-jenny.js`` invece di importarle. Non e' pigrizia: quelle sono costanti
private di un modulo da 1.353 righe, e tirarsi dentro la companion intera per
due dizionari sarebbe il contrario di cio' che la casa e' (v.
``.agent/casa-plan.md``).

Il prezzo di una copia e' la deriva, e si paga in silenzio: un file d'arte
rinominato, o un umore nuovo lato Python, lascerebbero la casa con
un'immagine rotta o con una faccia che non arriva mai — mentre l'officina
continua a funzionare, quindi nessuno se ne accorge. Questi test sono il
prezzo pagato una volta sola.
"""

from __future__ import annotations

import re
from pathlib import Path

from jenny.session.mascot_mood import MOODS, NEUTRAL_MOOD
from jenny.utils.android_assets import _UI_MANIFEST

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CASA_JS = ASSETS / "casa-mascot.js"
JENNY_JS = ASSETS / "mobile-jenny.js"


def _table(source: str, name: str) -> dict[str, str]:
    """Estrae un dizionario ``const NOME = { chiave: 'valore', ... }``."""
    block = re.search(rf"const {name} = \{{(.*?)\n\}};", source, re.S)
    assert block, f"{name} non trovata"
    return dict(re.findall(r"(\w+):\s*'([^']+)'", block.group(1)))


def _list(source: str, name: str) -> list[str]:
    block = re.search(rf"const {name} = \[(.*?)\];", source, re.S)
    assert block, f"{name} non trovata"
    return re.findall(r"'([^']+)'", block.group(1))


def test_casa_uses_the_same_art_as_the_workshop():
    """Stessi file, non file somiglianti.

    Una copia che diverge da sola e' peggio di nessuna copia: l'officina
    continuerebbe a disegnare Jenny e la casa mostrerebbe un rettangolo rotto.
    """
    casa, officina = CASA_JS.read_text(), JENNY_JS.read_text()
    for table in ("BODY", "FACE"):
        mine, theirs = _table(casa, table), _table(officina, table)
        assert mine, f"{table} vuota in casa-mascot.js"
        for key, path in mine.items():
            assert key in theirs, f"{table}.{key} non esiste in mobile-jenny.js"
            assert path == theirs[key], f"{table}.{key}: la casa punta altrove"


def test_every_sprite_is_a_real_file_in_the_manifest():
    """Un percorso giusto ma fuori dal manifest non arriva sul telefono.

    `_serve_static` non da' 404 per un file fuori manifest: ricade sulla copia
    su disco, che su Android e' un mirror e non e' autoritativa. Il difetto si
    vede solo sul dispositivo, ed e' un'immagine mancante.
    """
    casa = CASA_JS.read_text()
    for table in ("BODY", "FACE"):
        for key, url in _table(casa, table).items():
            rel = url.removeprefix("/html-mobile/")
            assert (ASSETS.parent / rel).is_file(), f"{table}.{key}: {rel} non esiste"
            assert rel in _UI_MANIFEST, f"{table}.{key}: {rel} non e' nel manifest"


def test_moods_match_the_backend():
    """Gli umori che la casa sa disegnare sono quelli che il backend manda.

    ``neutral`` e' fuori perche' non produce nessun frame: e' l'assenza di
    reazione, non una faccia.
    """
    casa_moods = set(_list(CASA_JS.read_text(), "MOOD_FACES"))
    assert casa_moods == set(MOODS) - {NEUTRAL_MOOD}
    faces = _table(CASA_JS.read_text(), "FACE")
    for mood in casa_moods:
        assert mood in faces, f"l'umore {mood} non ha una faccia"
