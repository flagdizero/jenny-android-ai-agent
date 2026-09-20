"""I quattro cassetti dell'officina: nessuna sezione persa, nessuna inventata.

Il dock e' passato da cinque voci per sottosistema a quattro per domanda — una
console e tre facolta' (`.agent/officina-tavole-plan.md`). Il meccanismo e' una
tabella sola, `CASSETTI`, e questo file misura le tre cose che quella tabella
puo' sbagliare **in silenzio**:

* **una sezione senza cassetto** sparisce dalla schermata. Non da' errore, non
  lascia un buco: semplicemente non si disegna, e te ne accorgi il giorno in
  cui la cerchi. E' il difetto che questo giro puo' introdurre a ogni passo,
  perche' ogni passo sposta stringhe da un elenco all'altro;
* **un cassetto che nomina una sezione che non esiste** esplode al primo
  tocco (`sezioni[id]()` su `undefined`), e solo su quel cassetto;
* **la stessa sezione in due cassetti** e' il doppione che il giro esiste per
  togliere, letto dalla parte del codice invece che dal disegno.

E' la stessa forma dell'invariante `BACK_TO` della casa: una tabella che dice
dove si va, e un banco che la incrocia con quel che esiste davvero.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
SETTINGS_JS = ASSETS / "mobile-settings.js"
APP_JS = ASSETS / "mobile-app.js"
OFFICINA = ROOT / "jenny" / "templates" / "ui" / "officina.html"


def _src(nome: str) -> str:
    return (ASSETS / nome).read_text(encoding="utf-8")


def _cassetti() -> dict[str, dict[str, list[str]]]:
    """La tabella `CASSETTI`, letta dal sorgente."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?ms)^export const CASSETTI = \{(.*?)^\};", src)
    assert m, "CASSETTI non si trova piu': il meccanismo dei cassetti e' sparito"
    corpo = m.group(1)
    fuori = {}
    for nome, dentro in re.findall(r"(\w+): \{(.*?)\n  \}", corpo, re.S):
        def elenco(chiave: str) -> list[str]:
            voci = re.search(rf"{chiave}: \[([^\]]*)\]", dentro)
            return re.findall(r"'([^']+)'", voci.group(1)) if voci else []
        fuori[nome] = {"sezioni": elenco("sezioni"), "porte": elenco("porte")}
    assert fuori, "la tabella e' vuota"
    return fuori


def _sezioni_disegnabili() -> set[str]:
    """Gli id che `render()` sa costruire."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?s)const sezioni = \{(.*?)\n    \};", src)
    assert m, "l'elenco delle sezioni dentro render() non si trova piu'"
    return set(re.findall(r"(\w+): \(\) =>", m.group(1)))


def test_every_section_has_exactly_one_drawer() -> None:
    """Il conto nei due versi.

    Una sezione che nessun cassetto nomina non si disegna piu' — e non lo dice
    nessuno. Una che ne nomina due e' il doppione che stiamo togliendo.
    """
    disegnabili = _sezioni_disegnabili()
    collocate: dict[str, list[str]] = {}
    for cassetto, contenuto in _cassetti().items():
        for sezione in contenuto["sezioni"]:
            collocate.setdefault(sezione, []).append(cassetto)

    senza_casa = sorted(disegnabili - set(collocate))
    assert not senza_casa, (
        f"queste sezioni non stanno in nessun cassetto e spariscono dalla "
        f"schermata senza dirlo: {senza_casa}"
    )
    doppie = {s: c for s, c in collocate.items() if len(c) > 1}
    assert not doppie, f"la stessa sezione in piu' cassetti: {doppie}"


def test_no_drawer_names_a_section_that_does_not_exist() -> None:
    """`sezioni[id]()` su un id sconosciuto e' un `TypeError` al primo tocco,
    e solo su quel cassetto: gli altri tre restano verdi."""
    disegnabili = _sezioni_disegnabili()
    for cassetto, contenuto in _cassetti().items():
        fantasmi = [s for s in contenuto["sezioni"] if s not in disegnabili]
        assert not fantasmi, f"il cassetto «{cassetto}» nomina sezioni che non esistono: {fantasmi}"


def test_every_drawer_in_the_table_is_a_dock_voice_and_the_other_way_round() -> None:
    """Un cassetto nella tabella che il dock non sa aprire e' codice morto; una
    voce del dock senza cassetto disegna la schermata intera — cioe' il difetto
    da cui questo giro parte."""
    tabella = set(_cassetti())
    html = OFFICINA.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="dock"'):html.index("</nav>")]
    voci = [m for m in re.findall(r'data-mode="([a-z]+)"', nav) if m != "onboarding"]

    app = _src("mobile-app.js")
    m = re.search(r"const VISTA_DI = \{([^}]*)\}", app)
    assert m, "VISTA_DI non si trova piu'"
    condivisi = set(re.findall(r"(\w+): 'settings'", m.group(1)))

    assert tabella == condivisi, (
        f"la tabella dei cassetti e la mappa delle viste non dicono la stessa "
        f"cosa: {tabella ^ condivisi}"
    )
    assert condivisi <= set(voci), f"cassetti che il dock non apre: {condivisi - set(voci)}"
    # La console e' l'unica voce che non e' un cassetto: e' la chat.
    assert set(voci) - condivisi == {"chat"}, set(voci) - condivisi


def test_the_views_that_left_the_dock_are_still_reachable() -> None:
    """App, file e wiki avevano una voce ciascuna e adesso no. Restano vive:
    senza una porta sarebbero raggiungibili solo da un `switchMode` scritto a
    mano, cioe' da nessuno."""
    app = _src("mobile-app.js")
    fabbriche = re.search(r"(?s)this\.controllerFactories = \{(.*?)\n    \};", app)
    assert fabbriche, "le fabbriche dei controller non si trovano piu'"
    modi = set(re.findall(r"(\w+):\s", fabbriche.group(1)))

    html = OFFICINA.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="dock"'):html.index("</nav>")]
    sul_dock = set(re.findall(r'data-mode="([a-z]+)"', nav))

    porte = {p for c in _cassetti().values() for p in c["porte"]}
    cassetti = set(_cassetti())
    # `settings` e' il contenitore dei tre cassetti, `onboarding` e `wiki` si
    # aprono da dentro (il primo avvio, e una pagina del grafo).
    esenti = {"settings", "onboarding", "wiki"} | cassetti
    orfani = sorted(modi - sul_dock - porte - esenti)
    assert not orfani, (
        f"queste viste non hanno piu' nessun modo di aprirsi: {orfani}. "
        f"Vanno messe fra le `porte` di un cassetto finche' non hanno la loro riga."
    )


def test_a_drawer_only_builds_what_it_shows() -> None:
    """Disegnare tutte e undici le sezioni e poi nasconderne otto vorrebbe dire
    costruire ogni volta anche il catalogo dei modelli e la storia degli
    snapshot — su un telefono, a ogni apertura."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?s)this\.contentEl\.innerHTML = \[(.*?)\]\.join\(''\);", src)
    assert m, "il corpo di render() non si trova piu'"
    corpo = m.group(1)
    assert "quali.map((id) => sezioni[id]())" in corpo, (
        "render() non disegna piu' per cassetto: se le costruisce tutte, le "
        "costruisce tutte anche quando ne mostra una"
    )
    assert "this._renderModelSettings(" not in corpo, (
        "una sezione viene costruita fuori dalla tabella: torna a pagarsi sempre"
    )


def test_the_three_drawers_share_one_screen_and_one_fetch() -> None:
    """Tre istanze vorrebbero dire tre `/api/settings` e due copie dello stesso
    stato che invecchiano mentre guardi la terza."""
    app = _src("mobile-app.js")
    assert "const impostazioni = () => (this._impostazioni ||= new SettingsController());" in app
    for modo in ("settings", "cervello", "mani", "memoria"):
        assert re.search(rf"{modo}:\s+impostazioni,", app), f"«{modo}» non condivide il controller"
    # E il cassetto va detto **prima** di activate(), o il primo frame mostra
    # quello di prima.
    i = app.index("next.setCassetto?.(")
    j = app.index("next.activate()")
    assert i < j, "il cassetto viene scelto dopo che la schermata si e' gia' disegnata"


def test_every_door_says_its_name_in_words() -> None:
    """Una chiave i18n che non esiste non fallisce: `i18n.t` torna la chiave, e
    a schermo compare «nav.graph». Visto sul rig il 20/09/2026.

    Il modo non e' sempre il nome della cosa — la vista del grafo si chiama
    `graph` e l'utente la conosce come Wiki — quindi il nome di una porta puo'
    avere bisogno di una riga sua.
    """
    import json

    src = _src("mobile-settings.js")
    m = re.search(r"const PORTE_ETICHETTE = \{([^}]*)\}", src)
    alias = dict(re.findall(r"(\w+): '([^']+)'", m.group(1))) if m else {}

    porte = {p for c in _cassetti().values() for p in c["porte"]}
    for locale in ("it", "en"):
        voci = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))["nav"]
        for porta in sorted(porte):
            chiave = alias.get(porta, f"nav.{porta}").removeprefix("nav.")
            assert voci.get(chiave, "").strip(), (
                f"la porta «{porta}» non ha un nome in {locale}.json: a schermo "
                f"comparirebbe «nav.{chiave}»"
            )


def test_every_door_wears_an_icon() -> None:
    """Il fallback e' una freccia generica: due porte con la stessa freccia si
    distinguono solo leggendo, che e' quel che un'icona serve a evitare."""
    src = _src("mobile-settings.js")
    m = re.search(r"const PORTE_ICONE = \{([^}]*)\}", src)
    assert m, "PORTE_ICONE non si trova piu'"
    icone = dict(re.findall(r"(\w+): '([^']+)'", m.group(1)))
    porte = {p for c in _cassetti().values() for p in c["porte"]}
    senza = sorted(porte - set(icone))
    assert not senza, f"porte senza icona, tutte uguali fra loro: {senza}"
