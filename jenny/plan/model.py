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
    extra: dict[str, Any] = field(default_factory=dict)

    _KEYS = (
        "id",
        "title",
        "detail",
        "done_when",
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
            extra={k: v for k, v in data.items() if k not in cls._KEYS},
        )


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


_COVERAGE_KEYS = ("item", "have", "missing", "strategy", "why")


def _read_coverage(raw: Any) -> list[dict[str, str]]:
    """La copertura letta da ``plan.json``: voci con le cinque chiavi, tutte testo.

    Tollerante come il resto della lettura: un piano di prima della copertura
    ne ha una vuota, e una voce malformata si salta invece di rendere
    illeggibile il piano.
    """
    if not isinstance(raw, list):
        return []
    return [
        {key: str(entry.get(key) or "") for key in _COVERAGE_KEYS}
        for entry in raw
        if isinstance(entry, dict)
    ]


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
    voce ``web`` vuole almeno un passo ``research``, perché chi esegue il piano
    non improvvisa ricerche che il piano non prevede. Ogni passo porta il
    suo criterio di «fatto» (``done_when``), che la verifica finale controlla.
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
        if kind is None or kind == "":
            kind = "other"
        if kind not in STEP_KINDS:
            problems.append(f"{where}: `kind` must be one of {', '.join(STEP_KINDS)}.")
        same_as = raw.get("same_as")
        if same_as is not None and not isinstance(same_as, str):
            problems.append(f"{where}: `same_as` must be the id of a step of the previous version.")
            same_as = None
        clean.append(
            Step(
                id=f"s{index}",
                title=step_title,
                detail=detail,
                kind=str(kind),
                same_as=(same_as.strip() or None) if isinstance(same_as, str) else None,
                done_when=done_when,
            )
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
    # Una voce ``web`` vuole un passo che cerchi: senza, chi esegue il piano
    # improvviserebbe ricerche che il piano non prevede, o scriverebbe «non
    # documentato». Basta un passo ``research`` qualsiasi, e non uno che nomini
    # ogni voce parola per parola: il modello parafrasa, e un confronto di
    # stringhe lo farebbe riprovare all'infinito. Cosa cerca ogni passo lo dice
    # il suo ``detail`` (lo chiede ``plan_mode.md``).
    if not research and any(e["strategy"] == "web" for e in clean):
        problems.append(
            "Some coverage entries are to be searched on the web, but the plan has no "
            "`research` step: add one, and say in its `detail` what it looks up."
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
    - stesso posto ma titolo, ``detail`` o ``done_when`` diverso → ``changed``;
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
        ):
            step.tag = "changed"
        else:
            step.tag = None
    return [{"id": s.id, "title": s.title} for i, s in enumerate(old) if i not in used]
