"""«Tu e Jenny»: il contratto della quarta stanza.

Grep e struttura, non comportamento — quello sta in `test_casa_switch_client.py`,
che le stanze le fa girare davvero. Qui ci sono le cose che si rompono in
silenzio: la porta cablata in `init()`, che nessun banco puo' istanziare, e
l'invariante che tiene insieme tre file — una stanza che il CSS sa accendere ma
da cui `BACK_TO` non sa uscire e' un vicolo cieco, e il tasto Indietro ci cade
dentro senza dire niente.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
INDEX = UI / "index.html"
ASSETS = UI / "assets"
APP_JS = ASSETS / "casa-app.js"
CSS = ASSETS / "casa-style.css"
I18N = ASSETS / "i18n"


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ── La porta ────────────────────────────────────────────────────────────────


def test_the_avatar_opens_you_and_jenny_and_the_workshop_is_a_long_press() -> None:
    """Il bottone in testa era una chiave inglese e portava in officina.

    Adesso e' l'avatar, e apre «Tu e Jenny» — dove l'officina sta in fondo,
    come nella tavola. La scorciatoia per chi in officina ci va dieci volte al
    giorno resta, ed e' quella che la tavola scrive sulla scheda: tenere
    premuto l'avatar.

    Il cablaggio vive in `init()`, che nessun banco puo' far girare senza un
    DOM vero: qui si misura sul sorgente.
    """
    app = _app()
    assert "setupLongPress(this.door, () => this._openInWorkshop(null));" in app, (
        "l'officina non e' piu' sotto il tocco lungo sull'avatar"
    )
    click = re.search(r"this\.door\.addEventListener\('click', \(\) => \{(.*?)\n    \}\);", app, re.S)
    assert click, "l'avatar non ha piu' un gestore del tocco"
    corpo = click.group(1)
    assert "this.openTu()" in corpo, "l'avatar non apre piu' «Tu e Jenny»"
    assert "this.door.dataset.longpress" in corpo and "delete" in corpo, (
        "il flag della pressione lunga non viene consumato: un tocco lungo "
        "aprirebbe l'officina **e** la pagina sotto"
    )
    assert "<i class=\"ti ti-user\">" in INDEX.read_text(encoding="utf-8"), (
        "l'icona non dice piu' dove porta il bottone"
    )


def test_the_workshop_card_is_the_other_way_in() -> None:
    """La stessa porta, dove la tavola la mette: in fondo alla pagina."""
    app = _app()
    assert "this.workshopBtn?.addEventListener('click', () => this._openInWorkshop(null));" in app
    html = INDEX.read_text(encoding="utf-8")
    for el_id in ("casa-workshop", "casa-workshop-name", "casa-workshop-hint", "casa-version"):
        assert f'id="{el_id}"' in html, f"{el_id} non esiste nel guscio"


# ── Nessuna stanza senza uscita ─────────────────────────────────────────────


def _rooms_in_css() -> set[str]:
    css = CSS.read_text(encoding="utf-8")
    return set(re.findall(r"\.casa-shell\[data-view='(\w+)'\]", css))


def _rooms_in_back_chain() -> dict[str, str]:
    m = re.search(r"(?ms)^const BACK_TO = \{(.*?)^\};", _app())
    assert m, "BACK_TO non trovata: la catena delle stanze e' sparita"
    return dict(re.findall(r"(\w+): '(\w+)'", m.group(1)))


def test_every_room_the_css_can_light_up_has_a_way_back() -> None:
    """Una stanza in piu' sono tre file: il CSS che la accende, l'HTML che la
    contiene, e la catena che ne esce.

    Dimenticare il terzo non rompe niente all'apertura — si rompe dopo, quando
    il tasto Indietro non trova la stanza nella tabella, ricade sul ramo «esci
    dal quaderno» e cambia conversazione invece di tornare indietro. Silenzioso
    all'occhio di chi scrive, non a quello di chi usa.
    """
    catena = _rooms_in_back_chain()
    accese = _rooms_in_css()
    assert accese, "nessuna regola `data-view` nel foglio: la grep non morde piu'"
    senza_uscita = accese - set(catena) - {"chat"}
    assert not senza_uscita, f"stanze da cui Indietro non sa uscire: {senza_uscita}"
    # E il contrario: una stanza in tabella che il CSS non sa accendere sarebbe
    # un `data-view` senza niente sotto — lo schermo resterebbe quello di prima.
    assert not set(catena) - accese, "la catena nomina stanze che il CSS non accende"


def test_the_back_chain_lands_somewhere_real() -> None:
    """Ogni salto arriva in una stanza che esiste, e nessuna torna in se'
    stessa: una stanza che rimanda a se' e' un tasto Indietro che non fa
    niente, ed e' peggio di un tasto che non c'e'."""
    catena = _rooms_in_back_chain()
    stanze = set(catena) | {"chat"}
    for da, a in catena.items():
        assert a in stanze, f"{da} torna a {a}, che non e' una stanza"
        assert da != a, f"{da} torna in se' stessa"


def test_the_room_is_in_the_shell_from_the_first_frame() -> None:
    """Come le altre: la sezione c'e' nell'HTML e la accende `data-view`, non
    un `hidden` che il JS deve togliere."""
    html = INDEX.read_text(encoding="utf-8")
    assert re.search(r'<section class="casa-tu" id="casa-tu">', html), (
        "la stanza non e' piu' nel guscio"
    )
    assert 'class="casa-tu" id="casa-tu" hidden' not in html, (
        "due meccanismi per la stessa cosa: la vista la accende gia' il CSS"
    )


# ── Le parole ───────────────────────────────────────────────────────────────


def test_the_fourth_room_speaks_both_languages() -> None:
    parole = {}
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        casa = data["casa"]
        for key in ("title", "open", "workshopHint", "version"):
            assert casa["tu"].get(key, "").strip(), f"casa.tu.{key} manca in {locale}.json"
        assert "{version}" in casa["tu"]["version"], "la riga della versione non interpola niente"
        parole[locale] = casa["tu"]
    assert parole["it"] != parole["en"], "una delle due lingue non e' stata tradotta"


def test_the_eyelet_has_a_phrase_for_every_landing() -> None:
    """L'occhiello nomina la stanza in cui si atterra, e le destinazioni sono
    quelle della catena: una frase che manca lascia a schermo `casa.back.tu`."""
    destinazioni = set(_rooms_in_back_chain().values())
    assert destinazioni, "la catena non porta piu' da nessuna parte"
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        frasi = data["casa"]["back"]
        for dove in destinazioni:
            assert frasi.get(dove, "").strip(), f"casa.back.{dove} manca in {locale}.json"
        assert len(set(frasi.values())) == len(frasi), (
            "due destinazioni con la stessa frase: l'occhiello ha smesso di dire dove porta"
        )
