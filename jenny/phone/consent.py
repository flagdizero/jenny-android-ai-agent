"""Il consenso alle mani di Jenny: «sto per usare WhatsApp, ok?».

Prima che ``phone_do`` tocchi un'app, il turno passa di qui
(``agent/tool_execution.py``, accanto al cancello delle altre Jenny e prima
del tetto di tempo dello strumento, che l'attesa non consuma).

La regola, in ordine:

1. ``tools.phone.mode = off`` → rifiuto col motivo;
2. per ogni app che i passi toccano (quella in primo piano, quelle che
   ``open_app`` apre, l'host di un ``open_link``): se l'utente l'ha già
   approvata in questo turno, o l'ha messa su «Sempre»
   (``tools.phone.trustedApps``), o la modalità è ``free`` → si procede;
   altrimenti si **chiede**: Ok (questa richiesta, quest'app) · Sempre · No;
3. un tocco su un tasto che costa — paga, compra, elimina, trasferisci
   (``security/costly_words.py``) — si chiede **sempre**, anche in ``free`` e
   anche per un'app su «Sempre»: è l'unica difesa contro una pagina che dà
   ordini a Jenny, e una risposta possibile è solo Ok o No;
4. un campo password non si scrive, mai.

Chi non ha nessuno davanti (un promemoria) non può chiedere: usa solo le app
già approvate, e un tasto che costa lo rifiuta.

**La domanda la compone il codice.** Il nome dell'app viene dal pacchetto, il
nome del tasto dall'ultima lettura dello schermo; del modello c'è solo il
``goal``, mostrato come citazione. Quello che si chiede lo tiene il server
(:class:`PhoneAsks`): chi preme un tasto manda un identificativo casuale e
buono una volta, come per le conferme delle altre Jenny (``peers/confirm.py``).

**La scadenza è sull'ora vera**, per la stessa ragione di là: il tempo di
asyncio si ferma quando Android sospende la CPU.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from loguru import logger

from jenny.bus.events import (
    FLOATING_CHANNEL,
    NOTIFICATION_CHANNEL,
    WEAR_CHANNEL,
    WEBSOCKET_CHANNEL,
)
from jenny.runtime import phone as runtime
from jenny.security.costly_words import COSTLY_RE
from jenny.session.keys import CRON_SESSION_PREFIX, is_peer_act_session_key

if TYPE_CHECKING:
    # Solo per i tipi: ``jenny.agent`` importa il runner, che importa questo
    # modulo. A freddo sarebbe un ciclo, e il gateway non partirebbe.
    from jenny.agent.tools.context import RequestContext

#: Lo strumento che agisce. ``phone_snapshot`` legge soltanto e non passa di qui.
ACT_TOOL = "phone_do"

#: Quanto aspetta una domanda prima di valere «nessuna risposta».
ASK_TTL_S = 600.0
#: Ogni quanto, del tempo da sveglio, l'attesa guarda l'ora vera.
CHECK_EVERY_S = 30.0

#: Le azioni che cadono sull'app in primo piano.
_TOUCHING = frozenset({"tap", "long_press", "type", "scroll", "swipe"})

#: Quanti turni tenere in memoria con le loro approvazioni.
_TURNS_KEPT = 32


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Ask:
    """Una domanda in attesa. ``kind`` è ``app`` (usare un'app) o ``costly``
    (un tasto che costa); ``origin`` dice da dove è partita la richiesta, e
    quindi dove va mostrata la domanda."""

    cid: str
    kind: str
    target: str
    app: str
    goal: str
    what: str
    origin: str
    options: tuple[str, ...]
    at_ms: int
    session_key: str
    channel: str
    chat_id: str
    metadata: dict[str, Any]
    future: asyncio.Future[str] = field(repr=False)

    def public(self) -> dict[str, Any]:
        """La domanda come la vede un'interfaccia: niente che non serva a
        disegnarla."""
        return {
            "cid": self.cid, "kind": self.kind, "app": self.app, "goal": self.goal,
            "what": self.what, "origin": self.origin, "options": list(self.options),
            "at_ms": self.at_ms,
        }


class PhoneAsks:
    """Le domande in attesa, in memoria: il lavoro che le aspetta è vivo.

    ``posters`` e ``clearers`` sono i posti in cui una domanda compare e
    sparisce (la notifica, la scheda in chat, la pillola, Telegram, l'altra
    Jenny): li aggiunge chi costruisce il gateway. Uno che fallisce non ferma
    gli altri né la domanda.
    """

    def __init__(self, *, ttl_s: float = ASK_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._pending: dict[str, Ask] = {}
        self.posters: list[Callable[[Ask], Any]] = []
        self.clearers: list[Callable[[Ask], Any]] = []

    def pending(self) -> list[dict[str, Any]]:
        """Le domande in attesa, dalla più vecchia."""
        return [a.public() for a in sorted(self._pending.values(), key=lambda a: a.at_ms)]

    def get(self, cid: str) -> Ask | None:
        return self._pending.get(cid)

    async def ask(
        self, *, kind: str, target: str, app: str, goal: str, what: str, origin: str,
        options: tuple[str, ...], ctx: RequestContext | None,
    ) -> str:
        """Chiede e aspetta. Torna una delle *options*, oppure ``"timeout"``."""
        loop = asyncio.get_running_loop()
        ask = Ask(
            cid=secrets.token_hex(16), kind=kind, target=target, app=app[:80],
            goal=goal[:240], what=what[:80], origin=origin, options=options,
            at_ms=_now_ms(), session_key=(ctx.session_key or "") if ctx else "",
            channel=ctx.channel if ctx else "", chat_id=ctx.chat_id if ctx else "",
            metadata=dict(ctx.metadata) if ctx else {}, future=loop.create_future(),
        )
        self._pending[ask.cid] = ask
        self._each(self.posters, ask)
        deadline_ms = ask.at_ms + int(self._ttl_s * 1000)
        try:
            while True:
                left_s = (deadline_ms - _now_ms()) / 1000
                if left_s <= 0:
                    logger.info("phone: a question about {} went unanswered", ask.app)
                    return "timeout"
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(ask.future), min(left_s, CHECK_EVERY_S)
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            self._pending.pop(ask.cid, None)
            if not ask.future.done():
                ask.future.cancel()
            self._each(self.clearers, ask)

    def resolve(self, cid: str, choice: str) -> bool:
        """La risposta dell'utente; falso se la domanda non c'è (più) o la
        risposta non è fra quelle offerte."""
        ask = self._pending.get(cid)
        if ask is None or ask.future.done() or choice not in ask.options:
            return False
        ask.future.set_result(choice)
        return True

    def deny_all(self) -> int:
        """Tutto diventa un no: l'utente ha fermato Jenny."""
        denied = 0
        for ask in list(self._pending.values()):
            if not ask.future.done():
                ask.future.set_result("no")
                denied += 1
        return denied

    def expire_due(self, *, now: int | None = None) -> int:
        """Chiude le domande scadute mentre il telefono dormiva."""
        cutoff = (_now_ms() if now is None else now) - int(self._ttl_s * 1000)
        expired = 0
        for ask in list(self._pending.values()):
            if ask.at_ms <= cutoff and not ask.future.done():
                ask.future.set_result("timeout")
                expired += 1
        return expired

    @staticmethod
    def _each(calls: list[Callable[[Ask], Any]], ask: Ask) -> None:
        for call in list(calls):
            try:
                call(ask)
            except Exception:  # noqa: BLE001 — un posto che fallisce non ferma la domanda
                logger.opt(exception=True).debug("phone: a question could not be shown")


class Settings(Protocol):
    """Quello che il cancello legge e scrive della scelta dell'utente."""

    def mode(self) -> str: ...

    def trusted(self) -> set[str]: ...

    async def trust(self, target: str) -> None: ...


class ConfigSettings:
    """Le scelte dell'utente in ``config.json`` (``tools.phone``), lette al
    momento: un cambio dalle Impostazioni vale dall'azione dopo."""

    def _phone(self) -> Any:
        from jenny.config.loader import load_config

        return load_config().tools.phone

    def mode(self) -> str:
        try:
            return str(self._phone().mode)
        except Exception:  # noqa: BLE001 — una config che non si legge vale «spento»
            logger.opt(exception=True).warning("phone: config unreadable, treated as off")
            return "off"

    def trusted(self) -> set[str]:
        try:
            return {str(t) for t in self._phone().trusted_apps}
        except Exception:  # noqa: BLE001
            return set()

    async def trust(self, target: str) -> None:
        from jenny.config.store import mutate

        def apply(config: Any) -> bool | None:
            apps = config.tools.phone.trusted_apps
            if target in apps:
                return False
            apps.append(target)
            return None

        await mutate(apply)


_SETTINGS: Settings | None = None
_ASKS: PhoneAsks | None = None
_TURNS: OrderedDict[str, set[str]] = OrderedDict()


def install(settings: Settings | None, asks: PhoneAsks | None) -> None:
    """Lega il cancello alle scelte dell'utente e alle domande. Lo chiama chi
    costruisce il gateway; ``None`` lo stacca."""
    global _SETTINGS, _ASKS
    _SETTINGS, _ASKS = settings, asks
    _TURNS.clear()


def asks() -> PhoneAsks | None:
    return _ASKS


def origin_of(ctx: RequestContext | None) -> str:
    """Da dove parte la richiesta: ``app``, ``floating``, ``telegram``,
    ``notification``, ``wear``, ``peer`` (un'altra Jenny), ``cron`` (nessuno
    davanti) o ``other``."""
    if ctx is None:
        return "other"
    key = ctx.session_key or ""
    if is_peer_act_session_key(key):
        return "peer"
    if key.startswith(CRON_SESSION_PREFIX):
        return "cron"
    return {
        WEBSOCKET_CHANNEL: "app", FLOATING_CHANNEL: "floating",
        NOTIFICATION_CHANNEL: "notification", WEAR_CHANNEL: "wear", "telegram": "telegram",
    }.get(ctx.channel, "other")


async def availability() -> str | None:
    """Perché adesso Jenny non può usare il telefono, o ``None`` se può."""
    if _SETTINGS is None:
        return "Using the phone is not available here."
    if _SETTINGS.mode() == "off":
        return (
            "Using the phone is turned off. The user turns it on from Settings → Jenny → "
            "Uses the phone. Say so; do not try another way."
        )
    state = await runtime.phone_state()
    if state == "off":
        return (
            "The accessibility service that lets me use the phone is off. The user turns "
            "it on from Android Settings → Accessibility → «Jenny — using the phone». "
            "Say so; do not try another way."
        )
    if state != "on":
        return "Using the phone is not supported on this device (it needs Android 14)."
    return None


def _approved(ctx: RequestContext | None) -> set[str]:
    from jenny.agent.tools.context import current_turn_id

    # Il turno, non la chat: un «Ok» vale per la richiesta in corso e basta.
    key = (ctx.turn_id if ctx else None) or current_turn_id() or (ctx.session_key if ctx else "") or ""
    found = _TURNS.get(key)
    if found is None:
        found = _TURNS[key] = set()
        while len(_TURNS) > _TURNS_KEPT:
            _TURNS.popitem(last=False)
    return found


def _link_target(url: str) -> str:
    host = urlsplit(url if "://" in url else f"https://{url}").hostname or "a link"
    return f"link:{host}"


async def _targets(steps: list[dict[str, Any]]) -> list[str]:
    """Le app che questi passi toccano, nell'ordine in cui le toccano."""
    found: list[str] = []
    opened = False
    for step in steps:
        action = str(step.get("action") or "")
        if action == "open_app":
            package = await runtime.resolve_app(str(step.get("app") or ""))
            if package:
                found.append(package)
            opened = True
        elif action == "open_link":
            found.append(_link_target(str(step.get("url") or "")))
            opened = True
        elif action in _TOUCHING and not opened:
            package = await runtime.foreground()
            if package:
                found.append(package)
            opened = True
    return list(dict.fromkeys(found))


async def _label(target: str) -> str:
    if target.startswith("link:"):
        return target[5:]
    return await runtime.app_label(target)


def _costly_taps(steps: list[dict[str, Any]]) -> list[str]:
    """I nomi dei tasti che costano fra quelli che i passi toccano."""
    names: list[str] = []
    for step in steps:
        if step.get("action") not in ("tap", "long_press"):
            continue
        entry = runtime.ref_name(str(step.get("ref") or ""))
        if entry and COSTLY_RE.search(entry[1]):
            names.append(entry[1])
    return names


def _types_password(steps: list[dict[str, Any]]) -> bool:
    for step in steps:
        if step.get("action") != "type":
            continue
        entry = runtime.ref_name(str(step.get("ref") or ""))
        if entry and entry[0] == "password":
            return True
    return False


async def before_tool(name: str, params: Any) -> str | None:
    """``None`` se ``phone_do`` può procedere, altrimenti il rifiuto per il modello."""
    if name != ACT_TOOL:
        return None
    refusal = await availability()
    if refusal is not None:
        return refusal
    settings, pending = _SETTINGS, _ASKS
    if settings is None or pending is None:
        return "Using the phone is not available here."
    args = params if isinstance(params, dict) else {}
    steps = [s for s in (args.get("steps") or []) if isinstance(s, dict)]
    goal = " ".join(str(args.get("goal") or "").split())
    if _types_password(steps):
        return "That is a password field. The user enters credentials on the phone, not me."
    from jenny.agent.tools.context import current_request_context

    ctx = current_request_context()
    origin = origin_of(ctx)
    unattended = origin == "cron"
    approved = _approved(ctx)
    free = settings.mode() == "free"
    trusted = settings.trusted()
    last_app = ""
    for target in await _targets(steps):
        app = last_app = await _label(target)
        if free or target in approved or target in trusted:
            continue
        if f"no:{target}" in approved:
            return _said_no(app)
        if unattended:
            return (
                f"No one is there to confirm, and {app} is not an app the user marked "
                "«Always». A reminder can only use those. Say so in your answer."
            )
        choice = await pending.ask(
            kind="app", target=target, app=app, goal=goal, what="", origin=origin,
            options=("ok", "always", "no"), ctx=ctx,
        )
        if choice == "always":
            await settings.trust(target)
        elif choice != "ok":
            approved.add(f"no:{target}")
            return _said_no(app) if choice == "no" else _no_answer(app)
        approved.add(target)
    for what in _costly_taps(steps):
        app = last_app or await _label(await runtime.foreground() or "")
        if unattended:
            return (
                f"«{what}» costs money or deletes something, and no one is there to "
                "confirm. A reminder never taps it. Say so in your answer."
            )
        choice = await pending.ask(
            kind="costly", target="", app=app, goal=goal, what=what, origin=origin,
            options=("ok", "no"), ctx=ctx,
        )
        if choice != "ok":
            return (
                f"The user did not agree to «{what}». Do not tap it, and do not try "
                "another way to do the same thing."
            )
    return None


def _said_no(app: str) -> str:
    return f"The user said no to using {app}. Do not retry, and do not try another way."


def _no_answer(app: str) -> str:
    return (
        f"The user did not answer the question about using {app}, so it counts as a no. "
        "Say you are waiting for their go-ahead."
    )

