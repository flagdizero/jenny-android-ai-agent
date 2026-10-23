"""Ogni evento che il gateway manda sul WebSocket ha qualcuno che lo ascolta.

Il 21/09/2026 (`98a0230`) la schermata App se n'è andata portandosi via gli
unici gestori di ``app_data_changed`` e ``apps_list_changed``. Il gateway ha
continuato a mandarli a ogni turno che tocca un'app, e nessuno se n'è accorto
per giorni: un frame senza ascoltatore non fa errore, non fa log, non fa
niente — la mini-app aperta smette solo di aggiornarsi da sola. Lo ha trovato
un audit sul codice morto, non un test.

Il banco legge i nomi dal lato Python (le forme in cui ``jenny/channels``
scrive un evento) e chiede che ognuno compaia nel JS della WebUI, commenti
esclusi — perché dopo `98a0230` i nomi sopravvivevano proprio nei commenti, a
descrivere un ascolto che non c'era più — **in una forma d'ascolto**:
``case 'x':`` o ``event === 'x'`` (``!==`` per chi esce presto). Fino al
25/09/2026 bastava la stringa nuda, e per ``'user'`` ed ``'error'`` — parole
che compaiono dappertutto, da ``role === 'user'`` a ``showToast(…, 'error')``
— il gestore poteva sparire lasciando il test verde. Non dimostra che il
gestore funzioni; dimostra che esiste un posto dove guardare.

**Per guscio, dal 26/09/2026.** Il banco cercava l'ascolto «in un JS qualunque
della WebUI», e i gusci sono due: ``ui_query`` lo ascoltava solo l'officina, e
il test restava verde mentre dalla casa — il guscio di default — ``ui_view``
aspettava sei secondi e diceva a Jenny che l'app era in background. Adesso ogni
guscio conta solo i moduli che carica davvero (la chiusura degli ``import`` a
partire dal suo ``<script type="module">``), e un evento che un guscio non
ascolta di proposito sta nella sua lista, col perché.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jenny.channels.subagent_activity_wire import ACTIVITY_FRAME_EVENT

ROOT = Path(__file__).resolve().parents[2]
CHANNELS = ROOT / "jenny" / "channels"
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

# Eventi che il client non deve ascoltare, ognuno con il suo perché. Chi ne
# aggiunge uno qui lo fa sapendolo; un evento nuovo senza ascoltatore fa rosso.
WITHOUT_LISTENER = {
    # Conferme di protocollo: la connessione è registrata (``ready``) e la
    # sottoscrizione a una chat è attiva (``attached``). Documentate come API in
    # docs/reference/websocket.md e usate dai test d'integrazione; la WebUI non
    # ha niente da farci.
    "ready": "conferma di protocollo",
    "attached": "conferma di protocollo",
    # L'eco di ``{"type": "detach"}``: il client ha già tolto la chat
    # dalle sue prima di mandarlo, e non aspetta la risposta.
    "detached": "conferma di protocollo",
}

# I due gusci e il modulo da cui parte ognuno (lo ``<script type="module">``
# del suo HTML: lo verifica ``test_the_shell_entry_points_are_the_real_ones``).
SHELLS = {
    "index.html": "home-app.js",
    "workshop.html": "mobile-app.js",
}

# Eventi che un guscio non ascolta di proposito. Vale la stessa regola di
# ``WITHOUT_LISTENER``: chi ne aggiunge uno lo fa sapendolo.
SHELL_WITHOUT_LISTENER = {
    "index.html": {
        # Il dettaglio dei subagent e' un arnese d'operatore: la casa ne mostra
        # solo la bolla di lavoro (``subagent_status``, ``home-work.js``), e
        # «apri nell'officina» porta dove c'e' il resto.
        "subagent_activity": "dettaglio dei subagent: solo officina",
        "subagent_unwatched": "dettaglio dei subagent: solo officina",
        # La casa non mostra il modello in vivo: la stanza «Chi risponde» lo
        # legge dalle impostazioni quando la apri.
        "runtime_model_updated": "il modello si rilegge all'apertura della stanza",
    },
    "workshop.html": {
        # La bolla di lavoro e' della casa (05/10/2026). L'officina legge lo
        # stesso lavoro dal resoconto di testo che il comando continua a
        # mandare, come prima.
        "work_status": "la bolla di lavoro e' solo della casa",
        # La bacheca dei quaderni e' solo della casa: l'officina non ne ha una
        # da ridisegnare.
        "board_changed": "la bacheca e' solo della casa",
        # Le bozze da approvare hanno la loro scheda nella chat della casa
        # (``home-staged.js``); l'officina, per ora, non la mostra.
        "staged_changed": "la scheda delle bozze e' solo della casa",
        # Il piano a lista ha la sua scheda nella chat del quaderno della casa
        # (``home-plan.js``); l'officina non lo mostra.
        "plan_changed": "la scheda del piano e' solo della casa",
    },
}

_IMPORT = re.compile(
    r"""(?:\bimport|\bexport)\s[^'"`;]*?\bfrom\s*['"](\.[^'"]+)['"]"""
    r"""|\bimport\s*\(\s*['"](\.[^'"]+)['"]\s*\)"""
    r"""|\bimport\s+['"](\.[^'"]+)['"]"""
)


def _strip_comments(text: str) -> str:
    # Via i blocchi e le righe di commento; ``//`` dopo ``:`` o dentro una
    # stringa è un indirizzo, non un commento.
    return re.sub(r"/\*.*?\*/|(?<![:'\"`])//[^\n]*", "", text, flags=re.S)


def _shell_modules(entry: str) -> list[Path]:
    """I moduli che un guscio carica: la chiusura dei suoi ``import`` relativi,
    statici e dinamici, a partire dal modulo d'ingresso."""
    seen: set[Path] = set()
    todo = [(ASSETS / entry).resolve()]
    while todo:
        path = todo.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        src = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for groups in _IMPORT.findall(src):
            rel = next(g for g in groups if g)
            todo.append((path.parent / rel).resolve())
    return sorted(seen)


def _emitted_events() -> set[str]:
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(CHANNELS.glob("*.py")))
    names = set(re.findall(r'"event"\s*:\s*"([a-z_]+)"', src))
    names |= set(re.findall(r'_send_event\(\s*[\w.]+\s*,\s*"([a-z_]+)"', src, re.S))
    names.add(ACTIVITY_FRAME_EVENT)
    return names


def _shell_js_without_comments(entry: str) -> str:
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in _shell_modules(entry))
    return _strip_comments(text)


EMITTED = _emitted_events()
SHELL_JS = {shell: _shell_js_without_comments(entry) for shell, entry in SHELLS.items()}


def test_the_scan_still_finds_the_events() -> None:
    """Se le regex smettessero di trovare i nomi, il banco passerebbe a vuoto."""
    assert {"delta", "turn_end", "message", "error", "rpc_result"} <= EMITTED
    assert len(EMITTED) >= 15, sorted(EMITTED)


def test_the_exceptions_are_still_emitted() -> None:
    """Un'eccezione per un evento che non esiste più è una riga morta."""
    assert set(WITHOUT_LISTENER) <= EMITTED
    for shell, exempt in SHELL_WITHOUT_LISTENER.items():
        assert set(exempt) <= EMITTED, shell


def test_the_shell_entry_points_are_the_real_ones() -> None:
    """Se un guscio cambiasse modulo d'ingresso, la chiusura partirebbe dal
    posto sbagliato e il banco misurerebbe un guscio che non esiste."""
    html_dir = ASSETS.parent
    for shell, entry in SHELLS.items():
        html = (html_dir / shell).read_text(encoding="utf-8")
        assert f'<script type="module" src="/html-mobile/assets/{entry}"></script>' in html, shell
        modules = _shell_modules(entry)
        assert len(modules) > 10, f"{shell}: la chiusura degli import ha trovato solo {modules}"


def test_an_exempt_event_is_really_unheard() -> None:
    """Un'eccezione per un evento che il guscio ascolta gia' e' una bugia."""
    for shell, exempt in SHELL_WITHOUT_LISTENER.items():
        for event in exempt:
            assert not _listener(event).search(SHELL_JS[shell]), (
                f"{shell} ascolta `{event}`: togli la riga da SHELL_WITHOUT_LISTENER"
            )


def _listener(event: str) -> re.Pattern[str]:
    """``case 'x':`` o ``…event === 'x'`` / ``!== 'x'``: dove un frame si smista."""
    q = r"""['"`]"""
    return re.compile(rf"(?:\bcase\s+|\bevent\s*[!=]==\s*){q}{re.escape(event)}{q}")


@pytest.mark.parametrize(
    ("shell", "event"),
    [
        (shell, event)
        for shell in SHELLS
        for event in sorted(EMITTED - set(WITHOUT_LISTENER) - set(SHELL_WITHOUT_LISTENER[shell]))
    ],
)
def test_every_emitted_event_has_a_listener_in_each_shell(shell: str, event: str) -> None:
    assert _listener(event).search(SHELL_JS[shell]), (
        f"il gateway manda `{event}` ma nessun modulo caricato da {shell} lo ascolta: "
        "o manca il gestore in quel guscio, o l'evento va nella sua lista col perché"
    )


def test_a_bare_mention_is_not_a_listener() -> None:
    """La forma che il banco vecchio accettava, e che non smista niente."""
    fake = "if (role === 'user') x(); showToast(msg, 'error'); const L = ['delta'];"
    assert not _listener("user").search(fake)
    assert not _listener("error").search(fake)
    assert not _listener("delta").search(fake)
    assert _listener("user").search("switch (msg.event) { case 'user': f(); }")
    assert _listener("error").search("if (msg?.event === 'error') g();")
    assert _listener("goal_status").search("if (msg.event !== \"goal_status\") return;")


# ── La chat, non la mascotte ────────────────────────────────────────────────

# Il modulo della chat di ogni guscio, e gli eventi che deve smistare **lui**.
# La mascotte (`shared/jenny-mascot.js`) li ascolta quasi tutti per animarsi, e
# vive in tutti e due i gusci: contato insieme a lei, il gestore `'error'` della
# chat si poteva togliere lasciando il banco verde — e un rifiuto del gateway
# diventava una faccina triste e nessuna parola.
CHAT_MODULE = {
    "index.html": "home-chat.js",
    "workshop.html": "mobile-chat.js",
}
CHAT_EVENTS = ("delta", "stream_end", "message", "turn_end", "error")


def test_the_mascot_really_hears_the_chat_events() -> None:
    """E' lei che rendeva il banco di sopra cieco: se smettesse di ascoltarli,
    questo di sotto non servirebbe piu', e lo si vuole sapere."""
    mascot = _strip_comments((ASSETS / "shared" / "jenny-mascot.js").read_text(encoding="utf-8"))
    assert all(_listener(e).search(mascot) for e in CHAT_EVENTS)


def _handler(event: str) -> re.Pattern[str]:
    """Piu' stretto di ``_listener``: ``case 'x':`` o ``event === 'x'``, non
    ``!==``. Le due chat hanno ``if (msg.event !== 'error') …`` per la bolla in
    sospeso, che nomina l'evento senza gestirlo: con ``_listener`` il ``case``
    si toglieva e il banco restava verde lo stesso."""
    q = r"""['"`]"""
    return re.compile(rf"(?:\bcase\s+|\bevent\s*===\s*){q}{re.escape(event)}{q}")


@pytest.mark.parametrize(
    ("shell", "event"), [(shell, event) for shell in CHAT_MODULE for event in CHAT_EVENTS]
)
def test_the_chat_itself_handles_its_events(shell: str, event: str) -> None:
    src = _strip_comments(
        (ASSETS / CHAT_MODULE[shell]).read_text(encoding="utf-8", errors="replace")
    )
    assert _handler(event).search(src), (
        f"{CHAT_MODULE[shell]} non smista `{event}`: lo ascolta solo la mascotte, "
        "e la chat di quel guscio non lo mostra"
    )
