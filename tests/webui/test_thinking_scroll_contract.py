"""Il box del ragionamento scorre come la chat, non come un francobollo fermo.

``.chat-thinking-body`` ha un ``max-height``, quindi è un contenitore di scroll a
sé: durante lo stream il testo cresceva sotto la piega e il box restava in cima —
e il rimpiazzo dell'``innerHTML`` a ogni frame gli riazzerava lo scroll, quindi
nemmeno risalire a leggere reggeva. La regola è quella che la chat
(``_autoScroll`` + FAB) e la lista dei subagent (``_saStick`` + pillola) hanno
già: si insegue l'ultima riga finché si è in fondo, si smette appena l'utente
risale, e una pillola dichiarata riporta giù.

Asserzioni sul sorgente, nello stile di ``test_subagent_panel_wiring.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
CHAT_JS = UI_DIR / "assets" / "mobile-chat.js"
CSS = UI_DIR / "assets" / "mobile-style.css"


def _chat() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _method(name: str) -> str:
    body = re.search(rf"\n  {name}\([^)]*\)\s*\{{(.*?)\n  \}}", _chat(), re.S)
    assert body, f"{name} non trovato in mobile-chat.js"
    return body.group(1)


def test_the_reasoning_body_is_rendered_through_the_scroll_discipline() -> None:
    """Nessun percorso scrive l'innerHTML del corpo scavalcando la disciplina.

    Era il bug: due punti (il flush per frame e la chiusura dello stream)
    assegnavano ``body.innerHTML`` direttamente, e l'assegnazione riporta
    ``scrollTop`` a zero.
    """
    source = _chat()
    direct = re.findall(r"\.chat-thinking-body'\)\s*;?\s*\n?\s*.*innerHTML\s*=", source)
    assert not direct, "un percorso scrive il corpo del ragionamento fuori da _renderReasoningBody"
    for caller in ("_flushRender", "_handleReasoningEnd"):
        assert "_renderReasoningBody()" in _method(caller), (
            f"{caller} non passa dalla disciplina dello scroll"
        )


def test_the_render_keeps_the_position_of_who_scrolled_up() -> None:
    body = _method("_renderReasoningBody")
    assert "_thinkAtBottom(body)" in body, "la posizione va misurata prima di toccare il corpo"
    assert "body.scrollTop" in body and "innerHTML" in body, (
        "senza salvare e rimettere scrollTop il rimpiazzo dell'innerHTML sbatte in cima chi legge"
    )
    assert "_thinkScrollToLatest(body)" in body, "in fondo si resta in fondo"
    assert "_syncThinkingJump()" in body


def test_scrolling_up_detaches_and_shows_the_pill() -> None:
    delta = _method("_handleReasoningDelta")
    assert "addEventListener('scroll'" in delta, "senza listener lo stacco non si nota"
    assert "e.deltaY < 0" in delta, "la rotella del Titan 2 deve staccare come in chat"
    live = _method("_setThinkingLive")
    assert "chat-thinking-jump" in live, "manca la pillola 'vai in fondo'"
    assert "chat.scrollToBottom" in live, "la pillola usa l'etichetta già localizzata della FAB"


def test_only_the_live_block_commands_the_flag() -> None:
    """I blocchi dei turni passati restano nel DOM con i loro listener.

    Senza guardia, scorrere dentro un ragionamento vecchio mentre ne arriva uno
    nuovo staccherebbe l'inseguimento di quello nuovo.
    """
    assert "this._currentThinking === thinking" in _method("_handleReasoningDelta")


def test_the_pill_goes_away_when_nothing_more_is_coming() -> None:
    """A segmento chiuso la pillola prometterebbe un 'dopo' che non arriva."""
    assert "_setThinkingLive(this._currentThinking, false)" in _method("_handleReasoningEnd")
    live = _method("_setThinkingLive")
    assert ".remove()" in live, "la pillola non viene tolta quando il blocco si conclude"
    assert "ti-brain" in live and "ti-check" in live, (
        "il blocco torna vivo quando il modello riprende a pensare: l'icona va nei due sensi"
    )


def test_a_new_reasoning_segment_does_not_wipe_the_previous_one() -> None:
    """``reasoning_end`` chiude un segmento, non il ragionamento del turno.

    Il modello ne apre uno nuovo ogni volta che riprende a pensare dopo un tool
    (``request_execution.py``). Azzerare il buffer alla chiusura faceva ripartire
    da vuoto il segmento dopo, e il render — che rimpiazza l'innerHTML — cancellava
    dal box il ragionamento già letto. Lo storico invece i segmenti li concatena,
    quindi un reload "riparava" il testo: la prova che il vivo mentiva.
    """
    end = _method("_handleReasoningEnd")
    assert "_reasoningBuffer = ''" not in end, (
        "azzerare il buffer a fine segmento cancella il ragionamento già mostrato"
    )
    assert "_reasoningSegmentClosed = true" in end

    delta = _method("_handleReasoningDelta")
    assert "_reasoningBuffer += '\\n\\n'" in delta, (
        "without lo stacco i due segmenti si incollano ('...right.Alright, let me...')"
    )


def test_the_body_does_not_drag_the_chat_underneath() -> None:
    css = CSS.read_text(encoding="utf-8")
    rule = re.search(r"\n\.chat-thinking-body\s*\{(.*?)\}", css, re.S)
    assert rule
    assert "overscroll-behavior: contain" in rule.group(1), (
        "uno swipe in fondo al ragionamento proseguiva trascinando la chat"
    )
