"""Una riconnessione non deve lasciare la chat con un messaggio in meno.

Il thread si legge **una volta sola** per caricamento di pagina — il latch
``_initialHistoryLoaded`` in ``loadInitialHistory()`` — e da lì in poi la vista si
regge sui soli frame WebSocket accodati dal vivo. ``invalidateHistory()`` azzera
quel latch, ma il suo unico chiamante era ``mobile-jenny.js`` al cambio di
sessione: nessuno lo chiamava su una riconnessione. Un messaggio pubblicato mentre
il socket era giù non arrivava mai alla vista, e non ci arrivava più.

Misurato sul Titan 2 il 2026-08-17. Due comandi in due tempi di fila: la chat mostrava
l'ack, di nuovo l'ack, l'esito del secondo (``... in 9.6s``) e
**non** la risposta del primo run. L'API la serviva nell'ordine giusto — quindi
niente perdita di dati — e dopo un riavvio dell'app il messaggio compariva. Lo
paga chi risponde in due tempi (``cmd_dream``/``cmd_gardener``: ack sincrono, esito
da un task in background), perché fra i due passano secondi o minuti, cioè la
finestra in cui lo schermo si spegne e il socket cade. In chat resta l'ack senza
esito, che è indistinguibile da un comando piantato.

**Perché si scarta e si ricarica invece di riconciliare.** Non c'è un'ancora:
gli id del thread sono relativi alla finestra di fetch, non identità. Lo stesso
messaggio, misurato: ``as-37-225bb88a`` a ``limit=10``, ``as-210-f31d4859`` a 40,
``as-1853-e9dd4174`` a 160 — cambiano indice *e* hash, perché il thread è
riprodotto dal transcript e l'indice è quello della riga nella finestra. Per
accodare solo il nuovo servirebbero id stabili e un cursore ``after``, che l'API
non ha (ha ``before``): una modifica al replay lato server per un caso di bordo
della UI. ``invalidateHistory()`` è già l'idioma per "la copia a schermo può
essere stale".

**Perché solo incollati in fondo.** ``invalidateHistory()`` svuota anche le pagine
già caricate paginando indietro: a chi sta leggendo la storia il resync porterebbe
via ciò che guarda per rimediare a un messaggio in coda che non guarda. Il gate è
``_autoScroll``, lo stesso che decide il riallineamento al ritorno in foreground.

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS

CHAT_JS = ASSETS / "mobile-chat.js"
WS_JS = ASSETS / "shared" / "ws-manager.js"


def _chat() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def test_socket_open_still_emits_the_event_the_resync_hangs_off() -> None:
    """Il resync si aggancia a ``chat:open``, che deve continuare a esistere."""
    ws = WS_JS.read_text(encoding="utf-8")
    assert "new CustomEvent('chat:open')" in ws, (
        "il resync della chat dipende da questo evento: se cambia nome, "
        "la riconnessione torna a passare inosservata"
    )


def test_chat_open_triggers_the_resync() -> None:
    """Il gestore di ``chat:open`` non si limita più allo stato di connessione."""
    body = _chat()
    handler = re.search(
        r"this\._onChatOpen\s*=\s*\(\)\s*=>\s*\{(.*?)\};", body, re.S
    )
    assert handler is not None, "_onChatOpen deve essere un blocco, non più una riga sola"
    assert "_resyncThreadAfterReconnect" in handler.group(1)
    # Registrato una volta sola: ws-manager avverte che i listener non vanno
    # ribindati a ogni reconnect, e qui la guardia è `_wsListenersBound`.
    assert body.count("addEventListener('chat:open', this._onChatOpen)") == 1


def test_resync_discards_and_reloads() -> None:
    """Scarta e ricarica: nessun tentativo di accodare per id."""
    fn = re.search(
        r"async _resyncThreadAfterReconnect\(\)\s*\{(.*?)\n  \}", _chat(), re.S
    )
    assert fn is not None, "il resync deve esistere come metodo"
    src = fn.group(1)
    assert "this.invalidateHistory()" in src
    assert "this.loadInitialHistory()" in src
    assert src.index("this.invalidateHistory()") < src.index("this.loadInitialHistory()"), (
        "ricaricare prima di invalidare rimetterebbe il latch a true e il "
        "caricamento diventerebbe un no-op"
    )


def test_resync_is_gated_on_the_four_conditions() -> None:
    """Le quattro uscite anticipate, ognuna per una ragione diversa."""
    fn = re.search(
        r"async _resyncThreadAfterReconnect\(\)\s*\{(.*?)\n  \}", _chat(), re.S
    )
    assert fn is not None
    src = fn.group(1)
    # Una fetch già in volo: la seconda disegnerebbe lo stesso thread una seconda
    # volta (nessuna delle due è scaduta, la generazione non è cambiata).
    #
    # Qui c'era `if (!this._initialHistoryLoaded) return;`, scritta per il primo
    # collegamento ("ci pensa loadInitialHistory"). Ma quel latch torna giù anche
    # su un **fallimento**, e il caricamento che fallisce è quello che parte
    # mentre il gateway sale: la riconnessione — il solo momento in cui ritentare
    # ha senso — era il solo in cui non si ritentava. V.
    # `test_history_load_failure_client.py`, che lo esegue.
    assert "if (this._loadingInitialHistory) return;" in src
    assert "if (!this._initialHistoryLoaded) return;" not in src
    # niente rientri sovrapposti, e non si calpesta una paginazione in corso
    assert "this._resyncingThread" in src
    assert "this.isLoadingHistory" in src
    # e soprattutto: solo con la vista in fondo
    assert "if (!this._autoScroll) return;" in src, (
        "senza questo gate il resync cancella le pagine che l'utente sta leggendo"
    )


def test_autoscroll_gate_shares_the_foreground_realignment_signal() -> None:
    """``_autoScroll`` resta ciò che ``_isNearBottom()`` mantiene.

    Se qualcuno lo trasformasse in un flag scollegato dallo scroll reale, il gate
    del resync smetterebbe di significare "l'utente è in fondo" senza che nulla
    lo dica.
    """
    body = _chat()
    assert "this._autoScroll = this._isNearBottom();" in body
    assert "if (this._autoScroll) this.scrollToBottom(true);" in body
