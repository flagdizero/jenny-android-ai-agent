"""Il modello di un piano a lista: dati, stati, transizioni, validazione.

Puro, senza I/O: lo leggono lo store (che lo salva), il driver (che lo muove) e
i tool del modello (che lo propongono). Lo stato di un piano vive **solo** in
``plan.json``; questo modulo ne è la forma, e le regole che nessun chiamante
deve poter aggirare — quali stati seguono quali, che aspetto ha una proposta —
stanno qui una volta sola invece che ripetute in ogni chiamante.

Le chiavi sconosciute di ``plan.json`` si conservano (``extra``) e si
riscrivono: una versione più vecchia dell'app che rilegge un piano scritto da
una più nuova non deve cancellare quello che non capisce. È la stessa regola di
``config/store.py::mutate``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

PLAN_FORMAT = 1

# ── Stati ────────────────────────────────────────────────────────────────────

PLAN_STATUSES: tuple[str, ...] = (
    "proposed",
    "superseded",
    "discarded",
    "running",
    "pausing",
    "paused",
    "blocked",
    "done",
    "stopped",
    "rolled_back",
)

# Aperto = il piano tiene il quaderno: c'è al più uno di questi per quaderno, e
# finché c'è il quaderno è in sola lettura per l'utente. ``proposed`` è aperto
# anche se non ha ancora toccato niente: una seconda proposta la sostituisce
# (``superseded``), non le si affianca.
OPEN_STATUSES: frozenset[str] = frozenset({"proposed", "running", "pausing", "paused", "blocked"})
CLOSED_STATUSES: frozenset[str] = frozenset(PLAN_STATUSES) - OPEN_STATUSES

# Partito e non ancora chiuso: il piano *tiene* il quaderno. Finché è in uno di
# questi stati l'utente non scrive nel quaderno (le RPC rispondono
# ``plan_running``) e il giardiniere non ci passa: tutto quel che cambia fra la
# foto A e la foto B dev'essere del piano, così il ritorno non indovina di chi è
# un file. ``proposed`` ne resta fuori: una proposta non ha ancora fotografato
# niente, e proporre non deve chiudere il quaderno.
HELD_STATUSES: frozenset[str] = frozenset({"running", "pausing", "paused", "blocked"})

# Chiusi dopo aver girato: hanno le foto, e sono quelli da cui si torna indietro.
RAN_STATUSES: frozenset[str] = frozenset({"done", "stopped", "rolled_back"})

STEP_STATUSES: tuple[str, ...] = (
    "pending",
    "now",
    "done",
    "blocked",
    "skipped",
    "cut",
    "interrupted",
)

STEP_KINDS: tuple[str, ...] = ("research", "write", "delete", "check", "other")

# Come la proposta pensa di coprire ogni voce del lavoro (``coverage``):
# ``notebook`` basta il quaderno, ``web`` si cerca in un passo ``research``,
# ``ask`` è una scelta dell'utente (in una proposta non ne deve restare: si
# chiede in chat *prima*), ``leave`` resta scoperta, con il perché.
COVERAGE_STRATEGIES: tuple[str, ...] = ("notebook", "web", "ask", "leave")

# I due motivi di un blocco: una domanda del modello, o le bozze di un passo che
# aspettano l'utente (``staged_writes``). Un blocco scritto prima di ``kind`` è
# una domanda.
BLOCK_KINDS: tuple[str, ...] = ("question", "drafts")

# Tag che il codice dà ai passi di una versione nuova rispetto alla precedente.
STEP_TAGS: tuple[str, ...] = ("new", "moved", "changed")

# Le transizioni ammesse. Una tabella e non una catena di ``if`` perché la
# domanda «da qui posso andare là?» se la fanno il driver, le RPC e i test, e
# deve avere una risposta sola.
#
# Le scelte che la tela non fissava:
# - ``running → paused`` senza passare da ``pausing``: è il boot che trova un
#   piano in corsa (il riavvio a metà mette in pausa), e lì non c'è nessun passo
#   da aspettare;
# - ``pausing → running``: «Riprendi» toccato prima che il passo finisca annulla
#   la pausa invece di aspettarla e poi ripartire;
# - ``pausing → blocked | done``: il passo corrente, che la pausa lascia finire,
#   può bloccarsi o essere l'ultimo;
# - ``rolled_back → done | stopped``: «Annulla il ritorno». Lo stato di prima
#   lo sa la storia; il driver sceglie;
# - ``blocked → paused``: il boot che trova un piano fermo su una domanda. Il
#   riavvio a metà mette in pausa *qualunque* piano partito (deciso il 23/10):
#   la domanda resta nella storia, e Riprendi rifà il passo, che è idempotente.
PLAN_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"running", "discarded", "superseded"}),
    "running": frozenset({"pausing", "paused", "blocked", "done", "stopped"}),
    "pausing": frozenset({"paused", "running", "blocked", "done", "stopped"}),
    "paused": frozenset({"running", "stopped"}),
    "blocked": frozenset({"running", "paused", "stopped"}),
    "done": frozenset({"rolled_back"}),
    "stopped": frozenset({"rolled_back"}),
    "rolled_back": frozenset({"done", "stopped"}),
    "superseded": frozenset(),
    "discarded": frozenset(),
}

# ── Limiti di una proposta ───────────────────────────────────────────────────

MIN_STEPS = 2
MAX_STEPS = 12
MAX_STEP_TITLE = 60
MAX_PLAN_TITLE = 80
# Il ``detail`` è il testo che Jenny rilegge quando apre il passo: lungo quanto
# serve, ma con un tetto, perché la somma dei dettagli entra nel turno di ogni
# passo (la lista intera, ripetuta) e un piano da 12 × 20 kB lo sfonderebbe.
MAX_STEP_DETAIL = 4000
# Il criterio di «fatto» di un passo: una soglia, non un secondo dettaglio.
MAX_STEP_DONE_WHEN = 1000
# La copertura conta le voci del lavoro (10 piante × 6 temi sono 60 voci): il
# tetto è largo, ma c'è, perché la lista intera torna nel piano completo.
MAX_COVERAGE = 80
MAX_COVERAGE_FIELD = 500
MAX_ASSUMPTIONS = 20
MAX_ASSUMPTION = 300
# La forma attesa che un passo ``check`` dichiara (``expect``): pochi pattern e
# un elenco di file per cartella, non un inventario del quaderno.
MAX_EXPECT_FILES = 40
MAX_EXPECT_PATTERN = 200

# Quanto dura una pianificazione senza turni (``drafting``): come
# ``goal_inactivity_ttl_h``, mezza giornata. Oltre, il prossimo messaggio è una
# chat normale: una domanda di ieri sera non tiene il quaderno in sola lettura
# per sempre.
DRAFTING_TTL_HOURS = 12


class PlanError(Exception):
    """Base degli errori del piano."""


class PlanTransitionError(PlanError):
    """Un cambio di stato che la tabella non ammette."""

    def __init__(self, plan_id: str, current: str, wanted: str) -> None:
        super().__init__(f"plan {plan_id}: cannot go from {current!r} to {wanted!r}")
        self.plan_id = plan_id
        self.current = current
        self.wanted = wanted


class PlanValidationError(PlanError, ValueError):
    """Una proposta malformata. Il messaggio è per il modello, che lo legge e riprova."""


class PlanFormatError(PlanError, ValueError):
    """Un ``plan.json`` che non si può leggere come piano."""


def now_iso() -> str:
    """Adesso, in UTC, al secondo: il formato di tutti i timestamp del piano."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_open_status(status: str) -> bool:
    return status in OPEN_STATUSES


# ── Dati ─────────────────────────────────────────────────────────────────────


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass
class Step:
    """Un passo del piano.

    ``title`` è per l'utente (la riga della scheda), ``detail`` per Jenny (cosa
    fare davvero, riletto all'apertura del passo). ``same_as`` è l'id del passo
    della versione precedente da cui questo viene, se il modello l'ha detto: si
    conserva perché è la ragione del ``tag``.
    """

    id: str
    title: str
    detail: str
    kind: str = "other"
    status: str = "pending"
    tag: str | None = None
    same_as: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    note: str | None = None
    photo_before: str | None = None
    photo_after: str | None = None
    done_when: str = ""
    covers: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    _KEYS = (
        "id",
        "title",
        "detail",
        "done_when",
        "covers",
        "kind",
        "status",
        "tag",
        "same_as",
        "started_at",
        "ended_at",
        "note",
        "photo_before",
        "photo_after",
    )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.extra)
        for key in self._KEYS:
            data[key] = getattr(self, key)
        data["covers"] = list(self.covers)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Step:
        if not isinstance(data, dict):
            raise PlanFormatError(f"a step is not an object: {type(data).__name__}")
        status = str(data.get("status", "pending"))
        if status not in STEP_STATUSES:
            raise PlanFormatError(f"step {data.get('id')!r}: unknown status {status!r}")
        kind = str(data.get("kind") or "other")
        return cls(
            id=str(data["id"]),
            title=str(data.get("title", "")),
            detail=str(data.get("detail", "")),
            # Un tipo che questa versione non conosce non rende illeggibile il
            # piano: diventa «altro». Serve solo all'icona della scheda, quindi
            # perderlo costa un'icona, mentre rifiutare costerebbe il piano.
            kind=kind if kind in STEP_KINDS else "other",
            status=status,
            tag=_opt_str(data.get("tag")),
            same_as=_opt_str(data.get("same_as")),
            started_at=_opt_str(data.get("started_at")),
            ended_at=_opt_str(data.get("ended_at")),
            note=_opt_str(data.get("note")),
            photo_before=_opt_str(data.get("photo_before")),
            photo_after=_opt_str(data.get("photo_after")),
            # Un passo di prima dei criteri di «fatto» non ne ha uno: vuoto.
            done_when=str(data.get("done_when") or ""),
            # Un passo di prima del legame con la copertura non copre niente.
            covers=_read_covers(data.get("covers")),
            extra={k: v for k, v in data.items() if k not in cls._KEYS},
        )


def _read_covers(raw: Any) -> list[str]:
    """I ``covers`` letti da ``plan.json``: solo testo, una lista malformata è vuota."""
    if not isinstance(raw, list):
        return []
    return [str(c) for c in raw if isinstance(c, str) and c]


def _empty_photos() -> dict[str, str | None]:
    return {"start": None, "end": None, "before_rollback": None, "after_rollback": None}


@dataclass
class Plan:
    """Un piano: la sua lista, il suo stato, le sue foto, la sua storia.

    ``dropped`` sono i passi della versione precedente che questa non ha più,
    come ``{"id", "title"}``: con il titolo e non solo l'id, così la scheda li
    mostra («tolto: …») senza dover rileggere il piano sostituito, che la
    pulizia può già aver cancellato. ``replaces`` è l'id di quel piano.
    """

    id: str
    notebook: str
    session_key: str
    title: str
    steps: list[Step]
    version: int = 1
    status: str = "proposed"
    notes: str | None = None
    replaces: str | None = None
    dropped: list[dict[str, str]] = field(default_factory=list)
    photos: dict[str, str | None] = field(default_factory=_empty_photos)
    blocked: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    coverage: list[dict[str, str]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    _KEYS = (
        "format",
        "id",
        "notebook",
        "session_key",
        "version",
        "status",
        "title",
        "notes",
        "replaces",
        "coverage",
        "assumptions",
        "steps",
        "dropped",
        "photos",
        "blocked",
        "history",
        "created_at",
        "updated_at",
    )

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.extra)
        data.update(
            {
                "format": PLAN_FORMAT,
                "id": self.id,
                "notebook": self.notebook,
                "session_key": self.session_key,
                "version": self.version,
                "status": self.status,
                "title": self.title,
                "notes": self.notes,
                "replaces": self.replaces,
                "coverage": [dict(c) for c in self.coverage],
                "assumptions": list(self.assumptions),
                "steps": [s.to_dict() for s in self.steps],
                "dropped": [dict(d) for d in self.dropped],
                "photos": dict(self.photos),
                "blocked": dict(self.blocked) if self.blocked is not None else None,
                "history": [dict(h) for h in self.history],
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        if not isinstance(data, dict):
            raise PlanFormatError(f"a plan is not an object: {type(data).__name__}")
        fmt = data.get("format", PLAN_FORMAT)
        # Un formato più nuovo l'ha scritto un'app più nuova (un downgrade): non
        # si indovina cosa vuol dire, si rifiuta. I lettori di elenchi saltano
        # il piano con un avviso invece di cadere.
        if not isinstance(fmt, int) or fmt > PLAN_FORMAT:
            raise PlanFormatError(f"plan format {fmt!r} is newer than {PLAN_FORMAT}")
        status = str(data.get("status", ""))
        if status not in PLAN_STATUSES:
            raise PlanFormatError(f"plan {data.get('id')!r}: unknown status {status!r}")
        try:
            raw_steps = data.get("steps") or []
            if not isinstance(raw_steps, list):
                raise PlanFormatError("steps is not a list")
            photos = _empty_photos()
            raw_photos = data.get("photos")
            if isinstance(raw_photos, dict):
                for key in photos:
                    photos[key] = _opt_str(raw_photos.get(key))
            blocked = data.get("blocked")
            history = data.get("history") or []
            dropped = data.get("dropped") or []
            return cls(
                id=str(data["id"]),
                notebook=str(data["notebook"]),
                session_key=str(data.get("session_key", "")),
                title=str(data.get("title", "")),
                steps=[Step.from_dict(s) for s in raw_steps],
                version=int(data.get("version", 1)),
                status=status,
                notes=_opt_str(data.get("notes")),
                replaces=_opt_str(data.get("replaces")),
                dropped=[
                    {"id": str(d.get("id", "")), "title": str(d.get("title", ""))}
                    for d in dropped
                    if isinstance(d, dict)
                ],
                photos=photos,
                blocked=_read_blocked(blocked),
                history=[dict(h) for h in history if isinstance(h, dict)],
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")),
                coverage=_read_coverage(data.get("coverage")),
                assumptions=[
                    str(a) for a in (data.get("assumptions") or [])
                    if isinstance(a, (str, int, float)) and not isinstance(a, bool)
                ] if isinstance(data.get("assumptions"), list) else [],
                extra={k: v for k, v in data.items() if k not in cls._KEYS},
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, PlanFormatError):
                raise
            raise PlanFormatError(f"malformed plan: {exc}") from exc


def _read_blocked(raw: Any) -> dict[str, Any] | None:
    """Il blocco letto da ``plan.json``, con ``kind`` e ``drafts`` sempre presenti.

    Un blocco scritto prima delle bozze di un passo non ha né l'uno né l'altro:
    è una domanda, senza bozze. Normalizzarlo qui, alla lettura, vuol dire che
    la scheda e il driver vedono sempre la stessa forma, vecchio o nuovo.
    """
    if not isinstance(raw, dict):
        return None
    blocked = dict(raw)
    kind = blocked.get("kind")
    blocked["kind"] = kind if kind in BLOCK_KINDS else "question"
    drafts = blocked.get("drafts")
    blocked["drafts"] = [
        {"id": str(d.get("id", "")), "target": str(d.get("target", ""))}
        for d in drafts
        if isinstance(d, dict)
    ] if isinstance(drafts, list) else []
    return blocked


_COVERAGE_KEYS = ("id", "item", "have", "missing", "strategy", "why")


def coverage_id(position: int) -> str:
    """L'id di una voce di copertura: ``c1``…``cN`` per posizione, come i passi."""
    return f"c{position}"


def _read_coverage(raw: Any) -> list[dict[str, str]]:
    """La copertura letta da ``plan.json``: voci con le sei chiavi, tutte testo.

    Tollerante come il resto della lettura: un piano di prima della copertura
    ne ha una vuota, e una voce malformata si salta invece di rendere
    illeggibile il piano. Una voce di prima degli id prende quello della sua
    posizione: è lo stesso che le avrebbe dato la validazione, perché una
    proposta con una voce malformata non passa, e quindi le posizioni scritte
    sono tutte quelle validate.
    """
    if not isinstance(raw, list):
        return []
    clean: list[dict[str, str]] = []
    for position, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        item = {key: str(entry.get(key) or "") for key in _COVERAGE_KEYS}
        item["id"] = item["id"] or coverage_id(position)
        clean.append(item)
    return clean


# ── Transizioni ──────────────────────────────────────────────────────────────


def can_transition(current: str, wanted: str) -> bool:
    return wanted in PLAN_TRANSITIONS.get(current, frozenset())


def apply_transition(
    plan: Plan,
    wanted: str,
    *,
    at: str | None = None,
    event: str | None = None,
    **details: Any,
) -> Plan:
    """Porta *plan* a *wanted*, o solleva :class:`PlanTransitionError`.

    Muta il piano e lo ritorna. Ogni transizione lascia una riga in ``history``
    (``event`` è il nome dello stato nuovo, se il chiamante non ne dà uno più
    parlante come ``"approved"``). Uscire da ``blocked`` toglie la domanda:
    una domanda rimasta sulla scheda di un piano che è ripartito sarebbe una
    seconda verità.
    """
    if wanted not in PLAN_STATUSES:
        raise PlanTransitionError(plan.id, plan.status, wanted)
    if not can_transition(plan.status, wanted):
        raise PlanTransitionError(plan.id, plan.status, wanted)
    stamp = at or now_iso()
    previous = plan.status
    plan.status = wanted
    if previous == "blocked" and wanted != "blocked":
        plan.blocked = None
    plan.history.append({"at": stamp, "event": event or wanted, "from": previous, **details})
    plan.updated_at = stamp
    return plan


# ── Lettura della lista ──────────────────────────────────────────────────────


def current_step(plan: Plan) -> Step | None:
    """Il passo in corso: quello ``now``, o quello fermo su una domanda."""
    for step in plan.steps:
        if step.status in ("now", "blocked"):
            return step
    return None


def next_pending_step(plan: Plan) -> Step | None:
    """Il primo passo ancora da fare, nell'ordine della lista."""
    for step in plan.steps:
        if step.status == "pending":
            return step
    return None


def step_counts(plan: Plan) -> dict[str, int]:
    """Quanti passi per stato, più ``total`` (senza i tagliati) e ``finished``.

    ``finished`` conta i fatti e i saltati: il bollino «Piano 2/8» dice quanti
    passi sono alle spalle, e un passo saltato con un motivo è alle spalle.
    """
    counts = {status: 0 for status in STEP_STATUSES}
    for step in plan.steps:
        counts[step.status] = counts.get(step.status, 0) + 1
    counts["total"] = len(plan.steps) - counts["cut"]
    counts["finished"] = counts["done"] + counts["skipped"]
    return counts


# ── Proposta ─────────────────────────────────────────────────────────────────


@dataclass
class Proposal:
    """Una proposta validata: titolo pulito e passi pronti, con gli id assegnati."""

    title: str
    steps: list[Step]
    coverage: list[dict[str, str]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate_proposal(
    title: Any, steps: Any, coverage: Any = None, assumptions: Any = None
) -> Proposal:
    """Controlla una proposta del modello e la ripulisce, o solleva con un messaggio per lui.

    I messaggi sono in inglese e dicono cosa correggere, perché li legge il
    modello come risultato del tool e deve poter riprovare senza indovinare.
    Gli id dei passi li assegna il codice (``s1``…``sN`` nell'ordine), mai il
    modello: un id scelto da lui potrebbe ripetersi o finire in un percorso.

    Due passi con lo stesso titolo si rifiutano: sulla scheda sarebbero due
    righe indistinguibili, e il confronto fra versioni abbina per titolo.

    **La proposta è un contratto per l'autonomia**: dopo l'Approva Jenny lavora
    da sola, quindi quel che si può decidere si decide qui. Per questo la
    copertura (``coverage``) è obbligatoria — ogni voce del lavoro dice cosa il
    quaderno ha già e come si colma il resto — e non può restarci una voce
    ``ask``: una scelta dell'utente si chiede in chat prima di proporre. Una
    voce ``web`` deve stare nei ``covers`` di un passo ``research``, perché chi
    esegue il piano non improvvisa ricerche che il piano non prevede, e non ne
    dimentica una che il piano prevede. Ogni passo porta il suo criterio di
    «fatto» (``done_when``), che la verifica finale controlla.
    """
    problems: list[str] = []
    clean_title = _clean_text(title)
    if not clean_title:
        problems.append("The plan needs a non-empty title.")
    elif "\n" in clean_title or "\r" in clean_title:
        problems.append("The plan title must be a single line.")
    elif len(clean_title) > MAX_PLAN_TITLE:
        problems.append(
            f"The plan title is {len(clean_title)} characters; keep it within {MAX_PLAN_TITLE}."
        )

    if not isinstance(steps, list):
        problems.append("`steps` must be a list of objects with `title` and `detail`.")
        raise PlanValidationError(" ".join(problems))
    if not MIN_STEPS <= len(steps) <= MAX_STEPS:
        problems.append(
            f"A plan has between {MIN_STEPS} and {MAX_STEPS} steps; this one has {len(steps)}. "
            + (
                "Merge related steps."
                if len(steps) > MAX_STEPS
                else "A one-step task does not need a plan: just do it."
            )
        )

    clean: list[Step] = []
    seen_titles: dict[str, int] = {}
    remap = _given_coverage_ids(coverage)
    for index, raw in enumerate(steps, start=1):
        where = f"Step {index}"
        if not isinstance(raw, dict):
            problems.append(f"{where} must be an object with `title` and `detail`.")
            continue
        raw_title = raw.get("title")
        step_title = _clean_text(raw_title)
        if not step_title:
            problems.append(f"{where} needs a non-empty title.")
        elif "\n" in step_title or "\r" in step_title:
            problems.append(f"{where}: the title must be a single line; move the rest to `detail`.")
        elif len(step_title) > MAX_STEP_TITLE:
            problems.append(
                f"{where}: the title is {len(step_title)} characters; keep it within "
                f"{MAX_STEP_TITLE} and move the rest to `detail`."
            )
        elif step_title in seen_titles:
            problems.append(
                f"{where} has the same title as step {seen_titles[step_title]}; "
                "give every step a distinct title."
            )
        else:
            seen_titles[step_title] = index
        detail = _clean_text(raw.get("detail"))
        if not detail:
            problems.append(f"{where} needs a non-empty `detail`: what you will actually do.")
        elif len(detail) > MAX_STEP_DETAIL:
            problems.append(
                f"{where}: `detail` is {len(detail)} characters; keep it within "
                f"{MAX_STEP_DETAIL} or split the step."
            )
        done_when = _clean_text(raw.get("done_when"))
        if not done_when:
            problems.append(
                f"{where} needs `done_when`: how you will know it is done, with the "
                "quality bar (e.g. \"every page has at least 3 concrete facts with a source\")."
            )
        elif len(done_when) > MAX_STEP_DONE_WHEN:
            problems.append(
                f"{where}: `done_when` is {len(done_when)} characters; keep it within "
                f"{MAX_STEP_DONE_WHEN}."
            )
        kind = raw.get("kind")
        # «Research» o « research »: lo stesso tipo, non un errore da far
        # riprovare al modello.
        if isinstance(kind, str):
            kind = kind.strip().lower()
        if kind is None or kind == "":
            kind = "other"
        if kind not in STEP_KINDS:
            problems.append(f"{where}: `kind` must be one of {', '.join(STEP_KINDS)}.")
        same_as = raw.get("same_as")
        if same_as is not None and not isinstance(same_as, str):
            problems.append(f"{where}: `same_as` must be the id of a step of the previous version.")
            same_as = None
        covers = _validate_covers(raw.get("covers"), kind, where, problems, remap)
        expect = _validate_expect(raw.get("expect"), kind, where, problems)
        clean.append(
            Step(
                id=f"s{index}",
                title=step_title,
                detail=detail,
                kind=str(kind),
                same_as=(same_as.strip() or None) if isinstance(same_as, str) else None,
                done_when=done_when,
                covers=covers,
                extra={"expect": expect} if expect else {},
            )
        )

    if clean and clean[-1].kind != "check":
        # Il controllo a macchina di fine piano vive nel passo ``check``: dead
        # link, fonti, bacheca, righe «da cercare», fatti persi, i buchi messi in
        # lista. Era un avviso in coda al risultato (``check_warnings``), e il
        # piano del Titan (26/10) è partito senza: nessun controllo, e i fatti
        # persi finiti sull'ultimo passo qualunque fosse.
        problems.append(
            "The last step must be a `check`: the code checks the notebook there (dead "
            "links, sources, the board, lines left to search, lost facts) and gives you only "
            "what to repair. Add a final `check` step."
        )
    clean_coverage = _validate_coverage(coverage, clean, problems)
    clean_assumptions = _validate_assumptions(assumptions, problems)
    if problems:
        raise PlanValidationError(" ".join(problems))
    return Proposal(
        title=clean_title,
        steps=clean,
        coverage=clean_coverage,
        assumptions=clean_assumptions,
    )


def _given_coverage_ids(coverage: Any) -> dict[str, str]:
    """Gli id che il modello ha dato alle voci → quelli per posizione.

    Il codice riassegna gli id per posizione, ma un modello che scrive le sue
    voci con ``"id": "c3"`` e poi ``covers: ["c3"]`` parla dei suoi id: si
    traducono, purché ci siano su ogni voce e senza doppioni. Altrimenti niente
    traduzione, e i ``covers`` si leggono come posizioni.
    """
    if not isinstance(coverage, list) or not coverage:
        return {}
    given: list[str] = []
    for raw in coverage:
        cid = raw.get("id") if isinstance(raw, dict) else None
        if not isinstance(cid, str) or not cid.strip():
            return {}
        given.append(cid.strip())
    if len(set(given)) != len(given):
        return {}
    return {cid: coverage_id(position) for position, cid in enumerate(given, start=1)}


def _validate_covers(
    raw: Any, kind: Any, where: str, problems: list[str], remap: dict[str, str] | None = None
) -> list[str]:
    """I ``covers`` di un passo, ripuliti; se esistano le voci lo dice la copertura.

    Solo un passo ``research`` copre voci: è lui che cerca sul web. Su un altro
    passo i ``covers`` sarebbero una promessa che nessuno mantiene. Con un
    ``kind`` sconosciuto l'errore è già quello del tipo: dirgli anche «solo un
    research» lo manderebbe fuori strada. *remap* traduce gli id dati dal
    modello in quelli per posizione (:func:`_given_coverage_ids`).
    """
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        problems.append(
            f'{where}: `covers` must be a list of coverage ids, like ["c2", "c5"].'
        )
        return []
    if kind not in STEP_KINDS:
        return []
    if kind != "research":
        problems.append(
            f"{where}: only a `research` step has `covers`; remove it here, and list "
            "the entries in the research step that searches them."
        )
        return []
    clean: list[str] = []
    for value in raw:
        cid = value.strip()
        cid = (remap or {}).get(cid, cid)
        if cid and cid not in clean:
            clean.append(cid)
    return clean


def _expect_pattern(value: Any) -> str | None:
    """Un percorso o un glob dentro il quaderno, o ``None`` se esce o non è testo."""
    if not isinstance(value, str):
        return None
    text = value.strip().strip("/")
    if not text or len(text) > MAX_EXPECT_PATTERN or text.startswith("~") or "\\" in text:
        return None
    if any(part == ".." for part in text.split("/")):
        return None
    return text


def _validate_expect(
    raw: Any, kind: Any, where: str, problems: list[str]
) -> dict[str, Any] | None:
    """La forma attesa di un passo ``check``, nel formato di ``notebook check``.

    ``{"folders": "wiki/piante/*", "files": ["index", "acqua"]}``: ogni cartella
    del glob deve avere quei file. Senza ``folders``, ``files`` sono percorsi dalla
    radice del quaderno. Il controllo a macchina di fine piano
    (``jenny/plan/audit.py``) dice così «mancano pagine» senza rileggere niente.
    Solo sul passo ``check``: è lui che la verifica.
    """
    if raw is None or raw == {}:
        return None
    shape = (
        'like {"folders": "wiki/plants/*", "files": ["index", "water"]} '
        '(folders: a glob inside the notebook; files: names each folder must have)'
    )
    if kind != "check":
        if kind in STEP_KINDS:
            problems.append(f"{where}: only the `check` step has `expect`; remove it here.")
        return None
    if not isinstance(raw, dict) or not set(raw) <= {"folders", "files"}:
        problems.append(f"{where}: `expect` must be an object {shape}.")
        return None
    folders_raw = raw.get("folders")
    folders_list = [folders_raw] if isinstance(folders_raw, str) else folders_raw or []
    files_raw = raw.get("files") or []
    if not isinstance(folders_list, list) or not isinstance(files_raw, list):
        problems.append(f"{where}: `expect` must be an object {shape}.")
        return None
    folders = [_expect_pattern(f) for f in folders_list]
    files = [_expect_pattern(f) for f in files_raw]
    if None in folders or None in files:
        problems.append(f"{where}: every path in `expect` must stay inside the notebook, {shape}.")
        return None
    if not files or len(files) > MAX_EXPECT_FILES or len(folders) > 5:
        problems.append(
            f"{where}: `expect` needs 1 to {MAX_EXPECT_FILES} `files` and at most 5 `folders`."
        )
        return None
    out: dict[str, Any] = {"files": files}
    if folders:
        out["folders"] = folders
    return out


def _validate_coverage(
    coverage: Any, steps: list[Step], problems: list[str]
) -> list[dict[str, str]]:
    """La copertura ripulita; i problemi si aggiungono a *problems*."""
    if not isinstance(coverage, list) or not coverage:
        problems.append(
            "`coverage` is required: one entry per item the job needs (for example each "
            "page to write), saying what the notebook already has (`have`), what is "
            "missing (`missing`), and how the plan fills it (`strategy`: notebook, web or "
            "leave, with `why`). Look at the notebook first if you do not know."
        )
        return []
    if len(coverage) > MAX_COVERAGE:
        problems.append(
            f"`coverage` has {len(coverage)} entries; keep it within {MAX_COVERAGE} by "
            "grouping items that share the same answer."
        )
    research = [s for s in steps if s.kind == "research"]
    clean: list[dict[str, str]] = []
    for index, raw in enumerate(coverage, start=1):
        where = f"Coverage entry {index}"
        if not isinstance(raw, dict):
            problems.append(f"{where} must be an object with `item` and `strategy`.")
            continue
        entry = {key: _clean_text(raw.get(key)) for key in _COVERAGE_KEYS}
        # L'id lo assegna il codice, per posizione, come agli step: quello che
        # il modello eventualmente manda si ignora (potrebbe ripetersi).
        entry["id"] = coverage_id(index)
        item = entry["item"]
        if not item:
            problems.append(f"{where} needs a non-empty `item`.")
            continue
        where = f"Coverage entry {index} ({item[:40]})"
        long = [k for k, v in entry.items() if len(v) > MAX_COVERAGE_FIELD]
        if long:
            problems.append(
                f"{where}: keep `{long[0]}` within {MAX_COVERAGE_FIELD} characters."
            )
        strategy = entry["strategy"]
        if strategy not in COVERAGE_STRATEGIES:
            problems.append(
                f"{where}: `strategy` must be one of notebook, web or leave."
            )
        elif strategy == "ask":
            problems.append(
                f"{where} is still `ask`: a choice that is the user's goes to them in "
                "chat before you propose. Ask, wait for the answer, then propose with "
                "their answer (notebook, web or leave)."
            )
        elif strategy == "leave" and not entry["why"]:
            problems.append(f"{where} is left uncovered: say `why`.")
        clean.append(entry)
    # Ogni voce ``web`` sta nei ``covers`` di un passo ``research``: senza, chi
    # esegue il piano improvviserebbe ricerche che il piano non prevede, o ne
    # salterebbe una e scriverebbe «non documentato» (prova 2: cinque voci web
    # senza passo che le cercasse). Il legame è per id e non per parole: il
    # modello parafrasa, e un confronto di stringhe lo farebbe riprovare
    # all'infinito; un id invece si controlla esatto.
    known = {e["id"] for e in clean}
    for number, step in enumerate(steps, start=1):
        unknown = [c for c in step.covers if c not in known]
        if unknown:
            problems.append(
                f"Step {number}: `covers` names {', '.join(unknown)}, which "
                f"{'is not a coverage id' if len(unknown) == 1 else 'are not coverage ids'}; "
                "coverage ids are c1, c2, ... in the order of `coverage`."
            )
    covered = {c for s in research for c in s.covers}
    uncovered = [e for e in clean if e["strategy"] == "web" and e["id"] not in covered]
    if uncovered:
        names = ", ".join(f"{e['id']} ({e['item'][:40]})" for e in uncovered)
        problems.append(
            f"Coverage entries to search on the web need a `research` step that lists "
            f"them in `covers`: {names} "
            f"{'is' if len(uncovered) == 1 else 'are'} in none. "
            + (
                "Add the ids to the `covers` of the research step that searches them "
                "(coverage ids are c1, c2, ... in the order of `coverage`)."
                if research
                else "Add a `research` step with `covers` listing them (coverage ids "
                "are c1, c2, ... in the order of `coverage`)."
            )
        )
    return clean


def _validate_assumptions(assumptions: Any, problems: list[str]) -> list[str]:
    """Le assunzioni ripulite: frasi brevi, una riga ciascuna; facoltative."""
    if assumptions is None:
        return []
    if not isinstance(assumptions, list):
        problems.append("`assumptions` must be a list of short sentences.")
        return []
    if len(assumptions) > MAX_ASSUMPTIONS:
        problems.append(
            f"`assumptions` has {len(assumptions)} entries; keep the {MAX_ASSUMPTIONS} "
            "that matter."
        )
    clean: list[str] = []
    for index, raw in enumerate(assumptions, start=1):
        text = _clean_text(raw)
        if not text:
            problems.append(f"Assumption {index} must be a non-empty sentence.")
        elif "\n" in text or len(text) > MAX_ASSUMPTION:
            problems.append(
                f"Assumption {index} must be one line within {MAX_ASSUMPTION} characters."
            )
        else:
            clean.append(text)
    return clean


def order_warnings(steps: Iterable[Step]) -> list[str]:
    """Gli avvisi d'ordine di una proposta valida: un ``delete`` prima di un ``write``/``research``.

    Un avviso e non un rifiuto: togliere pagine che nessuno legge prima di
    scriverne altre è legittimo, e un rifiuto bloccherebbe quei piani. Ma se il
    passo dopo attinge a quel che il primo cancella, lavora sul vuoto — e lo
    sa solo il modello, che rilegge la sua lista. Un avviso per ``delete``, sul
    primo passo che scrive dopo di lui. I numeri sono le posizioni (da 1).
    """
    warnings: list[str] = []
    ordered = list(steps)
    for i, step in enumerate(ordered):
        if step.kind != "delete":
            continue
        later = next(
            (j for j in range(i + 1, len(ordered)) if ordered[j].kind in ("write", "research")),
            None,
        )
        if later is None:
            continue
        verb = "writes" if ordered[later].kind == "write" else "researches"
        warnings.append(
            f"Step {i + 1} deletes before step {later + 1} {verb}: if step {later + 1} draws "
            f"on what step {i + 1} deletes, propose again with the deletion after it."
        )
    return warnings


# ── Pianificazione in corso (``drafting``) ───────────────────────────────────


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def drafting_expired(entry: dict[str, Any], *, now: datetime | None = None) -> bool:
    """La pianificazione è scaduta: nessun turno da :data:`DRAFTING_TTL_HOURS` ore.

    Si conta dall'ultimo turno (``touched``), non dall'inizio: un giro di
    domande e risposte lungo è ancora una pianificazione viva. Una voce senza
    date leggibili è scaduta: non si sa da quando tiene il quaderno.
    """
    moment = now or datetime.now(timezone.utc)
    last = _parse_stamp(entry.get("touched")) or _parse_stamp(entry.get("since"))
    if last is None:
        return True
    return (moment - last).total_seconds() > DRAFTING_TTL_HOURS * 3600


def drafting_view(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """La forma del drafting per la WebUI: ``{since, revise, reads}``.

    ``touched`` resta dentro: serve alla scadenza, e la scheda non ne fa niente.
    """
    if not isinstance(entry, dict):
        return None
    reads = entry.get("reads")
    revise = entry.get("revise")
    return {
        "since": str(entry.get("since") or ""),
        "revise": revise if isinstance(revise, str) and revise else None,
        "reads": reads if isinstance(reads, int) and not isinstance(reads, bool) else 0,
    }


# ── Versione nuova ───────────────────────────────────────────────────────────


def _longest_increasing(values: list[int]) -> set[int]:
    """Gli indici (in *values*) di una sottosequenza crescente più lunga.

    Quadratico, ed è voluto: la lista ha al più 12 voci.
    """
    if not values:
        return set()
    best_len = [1] * len(values)
    prev = [-1] * len(values)
    for i in range(len(values)):
        for j in range(i):
            if values[j] < values[i] and best_len[j] + 1 > best_len[i]:
                best_len[i] = best_len[j] + 1
                prev[i] = j
    end = max(range(len(values)), key=lambda i: best_len[i])
    chain: set[int] = set()
    while end != -1:
        chain.add(end)
        end = prev[end]
    return chain


def tag_new_version(old_steps: Iterable[Step], new_steps: list[Step]) -> list[dict[str, str]]:
    """Mette il ``tag`` sui passi di una versione nuova; ritorna i passi tolti.

    I tag li calcola il codice, non il modello. Un passo nuovo corrisponde a
    uno vecchio se ne porta l'id in ``same_as``; altrimenti (o se quell'id non
    esiste) per titolo identico. Ogni passo vecchio si abbina al più una volta.

    - nessuna corrispondenza → ``new``;
    - fuori dall'ordine relativo dei passi abbinati → ``moved``;
    - stesso posto ma titolo, ``detail``, ``done_when`` o ``covers`` diverso →
      ``changed``;
    - altrimenti nessun tag.

    «Posizione» vuol dire **ordine relativo**, non indice: con un passo nuovo in
    testa tutti gli altri scalano di uno, e chiamarli tutti «spostati» sarebbe
    falso agli occhi di chi legge. Si tengono fermi i passi della sottosequenza
    crescente più lunga degli indici vecchi (come fa un diff), e solo gli altri
    sono ``moved``.
    """
    old = list(old_steps)
    old_index = {step.id: i for i, step in enumerate(old)}
    old_by_title = {step.title: i for i, step in enumerate(old)}
    used: set[int] = set()
    matches: list[int | None] = []
    for step in new_steps:
        match: int | None = None
        if step.same_as is not None and step.same_as in old_index:
            match = old_index[step.same_as]
        if match is None or match in used:
            match = old_by_title.get(step.title)
        if match is not None and match in used:
            match = None
        if match is not None:
            used.add(match)
        matches.append(match)

    matched_positions = [i for i, m in enumerate(matches) if m is not None]
    in_order = {
        matched_positions[k] for k in _longest_increasing([m for m in matches if m is not None])
    }
    for position, (step, match) in enumerate(zip(new_steps, matches)):
        if match is None:
            step.tag = "new"
        elif position not in in_order:
            step.tag = "moved"
        elif (
            old[match].detail != step.detail
            or old[match].title != step.title
            or old[match].done_when != step.done_when
            # Cosa cerca il passo fa parte di cosa fa: anche se gli id si
            # riassegnano per posizione, un ``covers`` diverso va riguardato.
            or old[match].covers != step.covers
        ):
            step.tag = "changed"
        else:
            step.tag = None
    return [{"id": s.id, "title": s.title} for i, s in enumerate(old) if i not in used]


# ── Resoconti degli aiutanti ─────────────────────────────────────────────────

#: Le voci al massimo prese da una lista **To research**: oltre, è un resoconto
#: che elenca tutto, e il cancello del passo le mostrerebbe tutte al modello.
MAX_RESEARCH_ITEMS = 20
_MAX_RESEARCH_ITEM = 600

# L'etichetta: a inizio riga («## To research», «To research (3):», «- **To
# research**: none»), oppure in grassetto in mezzo a una riga e seguita dai due
# punti («… nuovi pages. **To research:** (i) … (ii) …»). Nuda a metà frase no
# («To research these I would need the web»), e nemmeno in grassetto senza i
# due punti («ends with a **To research** list»): è nominata, non è una lista.
# Fino al 26/10 valeva solo la prima forma, e l'annuncio dell'aiutante la
# cercava come sottostringa: la stessa riga valeva 0 voci per il driver e «vai
# a cercare» per Jenny (Titan).
_LABEL = r"(?:\*\*|__)?\s*to\s+research\s*(?:\([^)]*\))?\s*(?:\*\*|__)?"
_TO_RESEARCH = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[-*+]\s+)?" + _LABEL + r"\s*(?::\s*(?P<rest>.*)|(?P<rest_end>)$)",
    re.IGNORECASE,
)
_TO_RESEARCH_INLINE = re.compile(
    r"(?:^|\s)(?:\*\*|__)\s*to\s+research\s*(?:\([^)]*\))?\s*(?::\s*(?:\*\*|__)|(?:\*\*|__)\s*:)"
    r"\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_BULLET = re.compile(
    r"^(?P<indent>\s*)(?:[-*+•]|\d{1,2}[.)]|\(?(?:[ivx]{1,5}|[a-z]|\d{1,2})\))\s+(?P<text>\S.*)$"
)
# Una riga «(i) … (ii) … (iii) …»: i marcatori che la spezzano in voci.
_INLINE_MARKS = re.compile(r"(?:(?<=^)|(?<=[\s;.]))\(?(?:[ivx]{1,5}|[a-z]|\d{1,2})\)\s+")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*]+\*\*\s*:?\s*$)")
_NOTHING_WORDS = frozenset({"none", "nothing", "nessuno", "nessuna", "niente", "nulla"})


def _research_item(text: str) -> str | None:
    """Una voce ripulita, o ``None`` se dice «niente» o è solo un'intestazione."""
    clean = " ".join(text.replace("**", "").replace("__", "").split()).strip()
    if not clean or clean.strip(" .-–—") == "" or clean.lower() in ("n/a", "na"):
        return None
    first = re.match(r"[^\W\d_]+", clean)
    if first is not None and first.group(0).lower() in _NOTHING_WORDS:
        return None
    if clean.endswith(":"):
        # «(lacune emerse leggendo, nessuna query ancora fatta):» introduce le
        # voci che seguono, non ne è una.
        return None
    if len(clean) > _MAX_RESEARCH_ITEM:
        # Al limite si taglia a una parola: le query in fondo alla voce («Query:
        # `acer palma») si perdevano a metà (Titan).
        cut = clean[:_MAX_RESEARCH_ITEM]
        clean = cut[: cut.rfind(" ")] if " " in cut else cut
    return clean


def _inline_items(rest: str) -> list[str | None]:
    """Il resto della riga dell'etichetta: una voce, o più se numerate in linea."""
    marks = list(_INLINE_MARKS.finditer(rest))
    if len(marks) < 2:
        return [_research_item(rest)]
    parts: list[str | None] = []
    for n, mark in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(rest)
        parts.append(_research_item(rest[mark.end():end]))
    return parts


def _label(line: str) -> str | None:
    """Il resto della riga dopo l'etichetta **To research**, o ``None`` se non c'è."""
    head = _TO_RESEARCH.match(line)
    if head is not None:
        return head.group("rest") or ""
    inline = _TO_RESEARCH_INLINE.search(line)
    return inline.group("rest") if inline is not None else None


def to_research_items(text: str) -> list[str]:
    """Le voci della lista **To research** del resoconto di un aiutante.

    Un ``writer`` o un ``notebook-editor`` che non ha trovato un dato lo
    scrive lì (il suo template lo chiede): cosa manca, per quale pagina. Qui si
    trova l'etichetta (:data:`_TO_RESEARCH`, :data:`_TO_RESEARCH_INLINE`) e si
    prendono le voci sulla sua riga («(i) … (ii) …») e i punti elenco che la
    seguono, numerati o no; i sottopunti più rientrati sono dettagli della voce
    sopra e non contano a parte, salvo quando la voce sopra è solo
    un'intestazione («Fico — malattie:»): allora contano loro. La lista finisce
    alla prima riga che non è un punto elenco. «None», «nothing», «—» non sono
    buchi. Al massimo :data:`MAX_RESEARCH_ITEMS`, senza doppioni. È l'unica
    lettura: la usano il driver e l'annuncio dell'aiutante.
    """
    items: list[str] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines) and len(items) < MAX_RESEARCH_ITEMS:
        rest = _label(lines[i])
        i += 1
        if rest is None:
            continue
        found: list[str | None] = _inline_items(rest)
        base: int | None = None
        heading_indent: int | None = None
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if _label(line) is not None and _TO_RESEARCH.match(line):
                break
            bullet = _BULLET.match(line)
            if bullet is None:
                if base is not None and line[: len(line) - len(line.lstrip())] and not (
                    _HEADING.match(line)
                ):
                    # La continuazione rientrata di una voce: è sua, non una voce.
                    i += 1
                    continue
                if base is None and line.rstrip().endswith(":") and not _HEADING.match(line):
                    # Una riga d'introduzione sotto l'etichetta («(lacune emerse
                    # leggendo, nessuna query ancora fatta):»): le voci seguono.
                    i += 1
                    continue
                break
            indent = len(bullet.group("indent").expandtabs(4))
            if base is None:
                base = indent
            raw = bullet.group("text").strip()
            if indent <= base:
                heading_indent = indent if raw.rstrip("* ").endswith(":") else None
                found.append(_research_item(raw))
            elif heading_indent is not None and indent > heading_indent:
                # I sottopunti di un'intestazione sono le voci vere.
                found.append(_research_item(raw))
            i += 1
        for item in found:
            if item is not None and item not in items and len(items) < MAX_RESEARCH_ITEMS:
                items.append(item)
    return items
