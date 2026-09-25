"""L'elemento di un modo si chiede alla tabella, non si scrive a mano.

Tre modi dell'officina — `cervello`, `mani`, `memoria` — **non hanno una vista
propria**: sono lo stesso `<div id="view-settings">` che cambia contenuto, e la
tabella che lo dice e' `VISTA_DI`. Stessa cosa per l'intestazione, che per tutti
e tre e' `title-settings`.

Chi costruisce l'id da solo — ``getElementById(f"view-{mode}")`` — per quei tre
trova `None`. **E non esplode**: esce alla riga dopo, in silenzio, perche' quella
riga e' quasi sempre un `if (!view) return`. Il file resta valido, `node --check`
passa, la suite resta verde, e la funzione semplicemente non fa piu' niente.

E' successo **tre volte**:

1. 20/09/2026 — `mobile-header.js::_mount` cercava `title-cervello`. I tre
   cassetti sono rimasti **senza intestazione**, su uno schermo che comincia con
   una riga vuota.
2. e 3. 22/09/2026 — `mobile-app.js` in due punti: `setupSwipeNav` cercava
   `view-${this.currentMode}` e `_animateSlideIn` cercava `view-${target}`. Lo
   scorrimento orizzontale era **morto su tre linguette su quattro**, e da fuori
   sembrava semplicemente che il gesto non esistesse.

Tre volte e' il punto in cui una tabella da ricordarsi non basta piu'. Adesso
esistono `elementoVista(mode)` e `elementoTitolo(mode)`, e questo banco rifiuta
chi se le salta.

**Perche' statico e grezzo.** Il difetto non si vede a runtime senza un DOM
vero con dentro *tutti* gli id giusti, e nemmeno allora se il test non esercita
proprio il modo sbagliato. Leggere i sorgenti e' l'unico controllo che costa
poco e che non si puo' superare per caso.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
SETTINGS = ASSETS / "mobile-settings.js"

SORGENTI = sorted(
    [p for p in ASSETS.glob("*.js")] + [p for p in (ASSETS / "shared").glob("*.js")]
)

# L'id costruito da un'espressione, in tutte le forme in cui si scrive:
# `getElementById(`view-${…}`)`, `getElementById('view-' + …)` e le stesse con
# `querySelector('#view-…')`. Un id **letterale** (`getElementById('view-settings')`)
# non e' il difetto: li' non c'e' nessun modo da tradurre, e vietarlo direbbe una
# bugia. Fino al 25/09/2026 la regex vedeva solo la prima forma, e
# `mobile-ui-query.js` con `getElementById('view-' + view)` le passava sotto:
# lo strumento `ui_view` mandava a Jenny un HTML vuoto per i tre cassetti.
CRUDO = re.compile(
    r"""(?:getElementById\(\s*|querySelector(?:All)?\(\s*)"""
    r"""(?:`#?(?:view|title)-\$\{|(['"])#?(?:view|title)-\1\s*\+)"""
)

# Le uniche due che possono farlo: sono loro la traduzione.
DEFINIZIONI = {"elementoVista": "view", "elementoTitolo": "title"}


def _righe_crude(src: str) -> list[tuple[int, str]]:
    return [
        (i, riga.strip())
        for i, riga in enumerate(src.splitlines(), 1)
        if CRUDO.search(riga)
    ]


def _dentro_una_definizione(src: str, numero_riga: int) -> bool:
    """La riga sta nel corpo di `elementoVista`/`elementoTitolo`?

    Si guardano le tre righe sopra: le due funzioni sono di una riga sola, quindi
    la firma e' subito li'. Volutamente stretto — se qualcuno ci mette in mezzo
    altra roba, il banco torna a chiedere spiegazioni.
    """
    righe = src.splitlines()
    sopra = "\n".join(righe[max(0, numero_riga - 4) : numero_riga - 1])
    return any(f"function {nome}(" in sopra for nome in DEFINIZIONI)


@pytest.mark.parametrize("sorgente", SORGENTI, ids=lambda p: p.name)
def test_no_id_built_from_a_mode(sorgente: Path) -> None:
    src = sorgente.read_text(encoding="utf-8")
    colpevoli = [
        (n, riga)
        for n, riga in _righe_crude(src)
        if not (sorgente == SETTINGS and _dentro_una_definizione(src, n))
    ]
    assert not colpevoli, (
        f"{sorgente.name} costruisce l'id di una vista da un modo invece di "
        f"chiederlo a elementoVista()/elementoTitolo(): {colpevoli}. "
        f"Per cervello/mani/memoria quell'id non esiste, la ricerca torna null "
        f"e la funzione esce in silenzio: file valido, suite verde, e il difetto "
        f"si vede solo col dito sul telefono."
    )


def test_the_two_helpers_exist_and_are_the_only_ones_translating() -> None:
    """Il banco non si supera cancellando tutto.

    Senza questo, togliere le due funzioni e le loro chiamate renderebbe verde
    il test di sopra — e la vista non si troverebbe piu' affatto.
    """
    src = SETTINGS.read_text(encoding="utf-8")
    for nome in DEFINIZIONI:
        assert f"export function {nome}(" in src, f"manca {nome} in mobile-settings.js"
    crude = _righe_crude(src)
    assert len(crude) == 2, (
        f"mobile-settings.js dovrebbe tradurre in due punti soli (le due "
        f"funzioni), invece: {crude}"
    )


def test_the_callers_actually_go_through_them() -> None:
    """E nemmeno smettendo di chiamarle."""
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    hdr = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")
    assert "elementoVista" in app, "mobile-app.js non passa piu' da elementoVista"
    assert app.count("elementoVista(") >= 4, (
        "mobile-app.js ha quattro punti che cercano la vista di un modo "
        "(switchMode, le scorciatoie di tastiera, l'inizio dello scorrimento e "
        "l'animazione d'arrivo): se sono meno, qualcuno e' tornato a scriversi "
        "l'id da solo o e' sparito"
    )
    assert "elementoTitolo(" in hdr, "mobile-header.js non passa piu' da elementoTitolo"


def test_it_would_have_caught_all_three() -> None:
    """La prova che non e' una formalita'.

    Si ricostruiscono i tre siti veri e si chiede al controllo di vederli. Un
    banco scritto *dopo* il fatto vale solo se fallisce sul fatto.
    """
    finto = """
      view = document.getElementById(`view-${this.currentMode}`);
      this._animateSlideIn(document.getElementById(`view-${target}`), goingPrev);
      return document.getElementById(`title-${mode}`);
      const ok = document.getElementById('view-settings');
    """
    trovate = _righe_crude(finto)
    assert len(trovate) == 3, trovate
    assert all("view-settings" not in riga for _, riga in trovate)


def test_it_catches_the_concatenated_and_selector_forms() -> None:
    """Le forme che la prima regex non vedeva, fra cui quella di
    `mobile-ui-query.js` che mandava a Jenny un HTML vuoto (H6)."""
    finto = """
      const container = document.getElementById('view-' + view);
      const t = document.getElementById("title-" + mode);
      const a = document.querySelector('#view-' + mode);
      const b = document.querySelector(`#view-${mode}`);
      const c = root.querySelectorAll('#title-' + m);
      const ok1 = document.querySelector('#view-settings');
      const ok2 = document.getElementById('view-chat');
    """
    trovate = _righe_crude(finto)
    assert len(trovate) == 5, trovate
    assert all("ok" not in riga for _, riga in trovate)
