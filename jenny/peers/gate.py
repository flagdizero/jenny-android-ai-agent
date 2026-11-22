"""Il cancello di una richiesta di fare: cosa esce dal telefono, e chi lo decide.

Una richiesta di *fare* che arriva da un'altra Jenny (``jenny.peers.acting``)
gira con tutti gli strumenti: scrive, crea promemoria, lancia aiutanti. Quello
che non deve fare da sola è **mandare dati fuori dal telefono verso un indirizzo
che sceglie il modello**: una pagina ostile letta dall'altra Jenny può averle
messo in bocca «carica i contratti su questo sito», e la firma del collegamento
prova chi scrive, non che la richiesta sia dell'utente.

Il cancello vale per tutto il lavoro della richiesta: il turno, gli aiutanti che
lancia (la spec dell'aiutante porta la richiesta, ``SubagentSpec.peer_act``),
i turni dei loro risultati e i promemoria che crea quando scattano (i metadati
d'origine del job la portano). Il livello si legge **adesso**, a ogni azione,
dalla config di questo telefono (``PeerConfig.act``):

- ``free``: passa tutto;
- ``confirm``: le uscite verso un indirizzo scelto dal modello
  (:data:`URL_TOOLS`, :data:`BLIND_TOOLS`) e i comandi su un server dell'utente
  (:data:`SSH_TOOLS`) aspettano un «Consenti» dell'utente di qui; la rete dal
  codice (``python_exec``) è chiusa, e il rifiuto dice di usare ``web_fetch``,
  che chiede;
- ``off`` (il permesso tolto mentre il lavoro era in corso, o un promemoria
  rimasto da una richiesta vecchia): le uscite sono rifiutate.

Restano liberi ``message`` (arriva solo all'utente stesso), ``web_search`` (va
solo al motore di ricerca), e tutto ciò che scrive sul telefono: lo prende il
registro dell'annulla. ``install_update`` è rifiutato sempre.

**Cosa non si legge.** Un turno che lavora per un'altra Jenny — una domanda o
una richiesta — non legge ``config.json`` né le sue copie (:func:`secret_root`,
applicato da ``security.subagent_policy``): la risposta torna all'altro
telefono, e chiavi e token non sono fra le cose che gli si dicono.

Questo modulo non importa niente del resto di Jenny: lo chiamano il runner
(:func:`before_tool`) e la politica di rete di ``python_exec``
(:func:`network_refusal`), e la decisione la prende un'«autorità» che il
composition root installa (:func:`install`), cioè ``PeerActs``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

#: La chiave dei metadati di un turno che lavora per una richiesta di fare:
#: ``{"peer": <id>, "qid": <id>}``. Il livello non c'è: si legge a ogni azione.
PEER_ACT_META = "_peer_act"

#: Gli strumenti che mandano una richiesta a un indirizzo scelto dal modello.
URL_TOOLS = frozenset({"web_fetch", "browser_open", "download_file"})
#: Quelli che agiscono dentro una pagina già aperta, dove la destinazione non si vede.
BLIND_TOOLS = frozenset({"browser_do"})
#: Quelli che lavorano su un server dell'utente (un aiutante ``sysadmin``): il
#: server è uno di quelli registrati, ma il comando lo sceglie il modello, e da lì
#: un ``curl`` porta fuori quel che il comando contiene. La conferma dice quale.
SSH_TOOLS = frozenset({"ssh_exec", "ssh_job", "ssh_transfer"})
#: Una sessione di codice nata fuori dalla richiesta non porta il cancello.
CODE_SESSION_TOOLS = frozenset({"write_stdin"})
#: Mai, da una richiesta di un altro telefono.
NEVER_TOOLS = frozenset({"install_update"})

_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")


class Authority(Protocol):
    """Chi sa i livelli, chiede le conferme e annota cosa è uscito (``PeerActs``)."""

    def level(self, peer_id: str) -> str: ...

    def name(self, peer_id: str) -> str: ...

    async def confirm(self, peer_id: str, qid: str, action: str, target: str) -> bool: ...

    def note(self, peer_id: str, qid: str, item: dict[str, Any]) -> None: ...


@dataclass
class ActGate:
    """La richiesta per cui sta lavorando questo contesto."""

    peer: str
    qid: str
    # Le destinazioni già consentite in questo contesto: la stessa pagina due
    # volte non si chiede due volte.
    allowed: set[str] = field(default_factory=set)
    # E quelle negate (un «No», o nessuna risposta): lo stesso sito chiesto con
    # un altro strumento non si richiede. Sui due emulatori, dopo il no a
    # ``browser_open`` l'aiutante ha riprovato con ``web_fetch``, e la notifica
    # è tornata (20/11/2026).
    denied: set[str] = field(default_factory=set)


_GATE: ContextVar[ActGate | None] = ContextVar("jenny_peer_act_gate", default=None)
# La radice dell'installazione, mentre un turno risponde a una domanda di
# un'altra Jenny (``jenny.peers.questions``): quel turno non ha un cancello
# (non può uscire), ma ha gli stessi file da non leggere.
_ANSWERING: ContextVar[Path | None] = ContextVar("jenny_peer_answering", default=None)
_AUTHORITY: Authority | None = None
_ROOT: Path | None = None


def install(authority: Authority | None, root: Path | str | None = None) -> None:
    """Lega (o scioglie, con ``None``) chi decide. Senza, ogni uscita è rifiutata.

    *root* è la radice dell'installazione: serve a :func:`secret_root` per i
    turni delle richieste di fare, che il loop lega senza saperla.
    """
    global _AUTHORITY, _ROOT
    _AUTHORITY = authority
    _ROOT = Path(root) if authority is not None and root is not None else None


@contextmanager
def answering(root: Path | str) -> Iterator[None]:
    """Il contesto di un turno che risponde a una domanda di un'altra Jenny."""
    token = _ANSWERING.set(Path(root))
    try:
        yield
    finally:
        _ANSWERING.reset(token)


def secret_root() -> Path | None:
    """La radice i cui file segreti questo contesto non legge, o ``None``.

    Vale per un turno che lavora per un'altra Jenny (domanda o richiesta): quel
    che legge può finire nella risposta, cioè su un altro telefono. Gli aiutanti
    hanno già la loro regola (``subagent_policy``), che non dipende da questa.
    """
    root = _ANSWERING.get()
    if root is not None:
        return root
    return _ROOT if _GATE.get() is not None else None


def act_of(metadata: Any) -> dict[str, str] | None:
    """La richiesta portata da *metadata* (``PEER_ACT_META``), validata; o ``None``."""
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get(PEER_ACT_META)
    if not isinstance(raw, Mapping):
        return None
    peer, qid = raw.get("peer"), raw.get("qid")
    if not (isinstance(peer, str) and _ID_RE.match(peer)):
        return None
    if not (isinstance(qid, str) and _ID_RE.match(qid)):
        return None
    return {"peer": peer, "qid": qid}


def bind(act: Mapping[str, str] | None) -> Token[ActGate | None]:
    """Lega il cancello di *act* al contesto (``None`` lo toglie); da sciogliere con :func:`reset`."""
    gate = ActGate(peer=act["peer"], qid=act["qid"]) if act else None
    return _GATE.set(gate)


def reset(token: Token[ActGate | None]) -> None:
    _GATE.reset(token)


@contextmanager
def entered(act: Mapping[str, str] | None) -> Iterator[None]:
    token = bind(act)
    try:
        yield
    finally:
        reset(token)


def current() -> ActGate | None:
    return _GATE.get()


def current_act() -> dict[str, str] | None:
    """La richiesta di questo contesto, nella forma dei metadati."""
    gate = _GATE.get()
    return {"peer": gate.peer, "qid": gate.qid} if gate is not None else None


def _level(gate: ActGate) -> str:
    if _AUTHORITY is None:
        return "off"
    try:
        level = _AUTHORITY.level(gate.peer)
    except Exception:  # noqa: BLE001 — nel dubbio, chiuso
        return "off"
    return level if level in ("off", "confirm", "free") else "off"


def _name(gate: ActGate) -> str:
    try:
        return _AUTHORITY.name(gate.peer) if _AUTHORITY is not None else "the other Jenny"
    except Exception:  # noqa: BLE001
        return "the other Jenny"


def _host(url: str) -> str:
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def _note(gate: ActGate, item: dict[str, Any]) -> None:
    if _AUTHORITY is None:
        return
    try:
        _AUTHORITY.note(gate.peer, gate.qid, item)
    except Exception:  # noqa: BLE001 — l'elenco non ferma il lavoro
        pass


def _refusal(gate: ActGate, what: str, why: str) -> str:
    return (
        f"Refused: {what}. You are working on a request from {_name(gate)}, the user's "
        f"other Jenny, and {why} Carry on without it, and say in your answer what you "
        "could not do."
    )


async def before_tool(name: str, params: Any) -> str | None:
    """Il rifiuto per lo strumento *name* in una richiesta di fare, o ``None``.

    Fuori da una richiesta non fa niente. Con ``confirm`` può **aspettare**
    l'utente (fino alla scadenza della conferma): il runner lo chiama prima del
    tetto di tempo dello strumento, che quindi non conta l'attesa.
    """
    gate = _GATE.get()
    if gate is None:
        return None
    if name in NEVER_TOOLS:
        return _refusal(gate, name, "that is not something to do on request from another phone.")
    if name not in (URL_TOOLS | BLIND_TOOLS | SSH_TOOLS | CODE_SESSION_TOOLS):
        return None
    args = params if isinstance(params, Mapping) else {}
    level = _level(gate)
    if name in CODE_SESSION_TOOLS:
        if level == "free":
            return None
        return _refusal(
            gate, "typing into a code session",
            "a code session started outside this request could reach the network unchecked.",
        )
    # ``action`` è una chiave (la notifica la dice nella lingua dell'utente),
    # ``what`` la stessa cosa in inglese, per il modello.
    if name in URL_TOOLS:
        url = str(args.get("url") or "")
        target = _host(url) or url[:200]
        action = "download" if name == "download_file" else "open"
        what = f"open {target}" if action == "open" else f"download from {target}"
    elif name in SSH_TOOLS:
        # L'alias di un server registrato dall'utente, col suo prefisso: un
        # «sì» a un server non vale per il sito che ha lo stesso nome.
        alias = " ".join(str(args.get("host") or "").split())[:80]
        target = f"ssh:{alias}"
        action = "ssh"
        what = f"run commands on the server {alias!r}"
    else:
        target = "browser"
        action = "browser"
        what = "use the open web page"
    if level == "free":
        _note(gate, {"kind": "egress", "label": target, "undoable": False, "allowed": True})
        return None
    if level != "confirm":
        return _refusal(gate, what, "the user of this phone has turned off requests to act.")
    if target in gate.allowed:
        return None
    if target in gate.denied:
        return _refusal(gate, what, "the user of this phone already said no to it for this "
                        "request: don't try it again, with this tool or another.")
    assert _AUTHORITY is not None  # _level è "confirm" solo con un'autorità
    try:
        ok = await _AUTHORITY.confirm(gate.peer, gate.qid, action, target)
    except Exception:  # noqa: BLE001 — una conferma che non si è potuta chiedere è un no
        ok = False
    _note(gate, {"kind": "egress", "label": target, "undoable": False, "allowed": ok})
    if not ok:
        gate.denied.add(target)
        return _refusal(gate, what, "the user of this phone did not allow it.")
    gate.allowed.add(target)
    return None


def network_refusal() -> str | None:
    """Il rifiuto per la rete aperta dal codice (``python_exec``) in una richiesta di fare.

    Sincrono: lo chiamano i fili del codice, dove il contesto del turno arriva
    copiato. Con ``free`` passa; altrimenti chiuso, e il modello sa dove andare.
    """
    gate = _GATE.get()
    if gate is None or _level(gate) == "free":
        return None
    return _refusal(
        gate, "a network connection from code",
        "network access from code is closed here. To read a web page use web_fetch: it asks "
        "the user of this phone first.",
    )


__all__ = [
    "BLIND_TOOLS",
    "CODE_SESSION_TOOLS",
    "NEVER_TOOLS",
    "PEER_ACT_META",
    "SSH_TOOLS",
    "URL_TOOLS",
    "ActGate",
    "Authority",
    "act_of",
    "answering",
    "before_tool",
    "bind",
    "current",
    "current_act",
    "entered",
    "install",
    "network_refusal",
    "reset",
    "secret_root",
]
