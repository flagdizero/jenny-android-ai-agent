"""Il tema di ogni frase di ``USER.md``: come la stanza le mette in ordine.

**Il problema.** «Cosa ricorda di te» mostrava quarantaquattro frasi in fila,
senza un ordine: si leggeva come una pila, non come un ritratto (visto sul
Titan il 26/11/2026). ``USER.md`` le sezioni le ha avute, e una passata di
revisione di Dream le ha tolte: ha il mandato di farlo, e i titoli pesano sul
tetto di caratteri del file.

**La scelta.** Il tema non sta nel file. Sta qui, in una riga della tabella
``memory_themes`` di ``state.sqlite``, per impronta della frase
(``memory_entries.entry_id``): lo stesso schema di ``memory_locks``. Dream
continua a scrivere ``USER.md`` come prima, il prompt di ogni turno non cambia
di un carattere, e niente di quel che riordina puo' cancellare un tema.

**Chi lo assegna.** Una chiamata piccola al modello, solo per le frasi che un
tema non l'hanno: una lista numerata dentro, un oggetto JSON fuori
(``templates/agent/memory_themes.md``). Parte quando la stanza si apre e trova
frasi senza tema (:func:`sort_in_background`), mai dentro un turno. Una frase
corretta dalla stanza eredita il tema di quella che sostituisce
(:meth:`MemoryThemes.inherit`); una riscritta da Dream lo eredita dal legame
dell'archivio (``superseded_by``), e solo se non c'e' si chiede al modello.

**Quando non riesce.** Il modello che non risponde, o risponde male, lascia le
frasi senza tema: la stanza le mostra lo stesso, in fila. Si riprova dopo
:data:`RETRY_AFTER_S`; una frase rimasta fuori da due risposte va in
:data:`OTHER`, per non richiederla all'infinito.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.memory_archive import list_archived
from jenny.agent.tools.memory_entries import ENTRIES_LOCK, Entry, parse_entries
from jenny.db.schema import Schema
from jenny.utils.prompt_templates import render_template

# Solo le frasi su di te hanno un tema: gli appunti di ``memory/MEMORY.md``
# sono contesto di lavoro, e la stanza li tiene chiusi in una riga.
THEMED_SOURCE = "USER.md"

# Le chiavi, nell'ordine in cui la stanza mostra i temi. I nomi che si leggono
# sono nell'i18n (``home.memory.theme.<chiave>``); le definizioni che legge il
# modello sono nel template.
THEMES = ("identity", "people", "health", "home", "tastes", "character", "plans", "answers")
OTHER = "other"

SCHEMA = Schema(
    "memory_themes",
    (
        "CREATE TABLE memory_themes ("
        " source TEXT NOT NULL,"
        " entry_id TEXT NOT NULL,"
        " theme TEXT NOT NULL,"
        " PRIMARY KEY (source, entry_id));",
    ),
)

# Quanto aspettare il modello: e' un lavoro di sottofondo, ma la stanza sta
# rileggendo e non deve farlo per sempre.
SORT_TIMEOUT_S = 45.0
# Dopo una chiamata fallita, quanto prima di riprovare.
RETRY_AFTER_S = 600.0
# Frasi per chiamata, e quanto di ognuna arriva al modello: per scegliere un
# tema bastano le prime parole.
BATCH = 60
MAX_FACT_CHARS = 300
# Dopo quante risposte che la saltano una frase finisce in ``OTHER``.
MAX_MISSES = 2

_IN_FLIGHT: set[str] = set()
_TASKS: set[asyncio.Task[Any]] = set()
_RETRY_AT: dict[str, float] = {}
_MISSES: dict[tuple[str, str], int] = {}


class MemoryThemes:
    """La tabella ``memory_themes`` di un'installazione."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    def _db(self):
        from jenny import db

        return db.store_database(self._workspace, db.STATE, SCHEMA)

    def all(self, source: str = THEMED_SOURCE) -> dict[str, str]:
        rows = self._db().read(
            "SELECT entry_id, theme FROM memory_themes WHERE source = ?", (source,),
        )
        return {row["entry_id"]: row["theme"] for row in rows}

    def set_many(self, themes: dict[str, str], source: str = THEMED_SOURCE) -> None:
        if not themes:
            return
        with self._db().write() as conn:
            conn.executemany(
                "INSERT INTO memory_themes(source, entry_id, theme) VALUES (?, ?, ?) "
                "ON CONFLICT(source, entry_id) DO UPDATE SET theme = excluded.theme",
                [(source, entry_id, theme) for entry_id, theme in themes.items()],
            )

    def inherit(self, old_id: str, new_id: str, source: str = THEMED_SOURCE) -> bool:
        """La frase *new_id* prende il tema di *old_id*. ``False`` se non ne aveva."""
        theme = self.all(source).get(old_id)
        if not theme:
            return False
        self.set_many({new_id: theme}, source)
        return True


def _live_entries(workspace: Path) -> list[Entry]:
    try:
        with ENTRIES_LOCK:
            text = (Path(workspace) / THEMED_SOURCE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_entries(text)


def unsorted_entries(workspace: Path) -> list[Entry]:
    """Le frasi vive di ``USER.md`` che non hanno ancora un tema."""
    themes = MemoryThemes(workspace).all()
    return [entry for entry in _live_entries(workspace) if entry.id not in themes]


def _inherit_from_archive(workspace: Path, missing: list[Entry]) -> list[Entry]:
    """Da' a ogni frase il tema di quella che ha sostituito, se l'archivio lo sa.

    Una frase riscritta resta nel suo tema invece di essere richiesta al
    modello, che potrebbe metterla altrove. Torna quelle rimaste senza.
    """
    store = MemoryThemes(workspace)
    themes = store.all()
    wanted = {entry.id for entry in missing}
    found: dict[str, str] = {}
    for old in list_archived(Path(workspace) / "memory"):
        target = old.superseded_by
        if target in wanted and target not in found and themes.get(old.id):
            found[target] = themes[old.id]
    store.set_many(found)
    return [entry for entry in missing if entry.id not in found]


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
# Il trattino del bullet e' del file, non della frase.
_BULLET = re.compile(r"^[-*+][ \t]+")


def parse_answer(text: str | None, count: int) -> dict[int, str]:
    """La risposta del modello: ``{numero: tema}``, solo per i numeri validi.

    Tollera il testo intorno all'oggetto e i recinti di codice; un tema che non
    e' nella lista diventa :data:`OTHER`, un numero fuori dalla lista si scarta.
    """
    match = _JSON_OBJECT.search(text or "")
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in data.items():
        try:
            number = int(str(key).strip())
        except ValueError:
            continue
        if not 1 <= number <= count:
            continue
        theme = str(value).strip().lower()
        out[number] = theme if theme in THEMES else OTHER
    return out


async def _ask(llm: tuple[Any, str], entries: list[Entry]) -> dict[str, str]:
    provider, model = llm
    facts = "\n".join(
        f"{n}. {' '.join(_BULLET.sub('', entry.text.strip()).split())[:MAX_FACT_CHARS]}"
        for n, entry in enumerate(entries, 1)
    )
    response = await asyncio.wait_for(
        provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": render_template("agent/memory_themes.md", strip=True)},
                {"role": "user", "content": facts},
            ],
            tools=None,
            tool_choice=None,
        ),
        timeout=SORT_TIMEOUT_S,
    )
    if getattr(response, "finish_reason", None) == "error":
        raise RuntimeError("provider error")
    answer = parse_answer(getattr(response, "content", None), len(entries))
    return {entries[n - 1].id: theme for n, theme in answer.items()}


async def sort_unsorted(workspace: Path, llm: tuple[Any, str] | None) -> int:
    """Da' un tema alle frasi che non l'hanno. Torna quante ne ha sistemate.

    Prima l'eredita' dall'archivio (gratis), poi il modello per quel che resta.
    Solleva se il modello non risponde: decide chi chiama cosa farne.
    """
    workspace = Path(workspace)
    missing = await asyncio.to_thread(unsorted_entries, workspace)
    if not missing:
        return 0
    left = await asyncio.to_thread(_inherit_from_archive, workspace, missing)
    done = len(missing) - len(left)
    if not left or llm is None:
        return done
    store = MemoryThemes(workspace)
    key = str(workspace)
    for start in range(0, len(left), BATCH):
        batch = left[start:start + BATCH]
        themes = await _ask(llm, batch)
        for entry in batch:
            if entry.id in themes:
                _MISSES.pop((key, entry.id), None)
                continue
            misses = _MISSES.get((key, entry.id), 0) + 1
            _MISSES[(key, entry.id)] = misses
            if misses >= MAX_MISSES:
                themes[entry.id] = OTHER
                _MISSES.pop((key, entry.id), None)
        await asyncio.to_thread(store.set_many, themes)
        done += len(themes)
    return done


def sorting(workspace: Path) -> bool:
    """Se le frasi di questo workspace si stanno ordinando adesso."""
    return str(Path(workspace)) in _IN_FLIGHT


def sort_in_background(workspace: Path, llm: tuple[Any, str] | None) -> bool:
    """Fa partire l'ordinamento, se non e' gia' in corso e non e' appena fallito.

    Si chiama da dentro il loop (un comando della stanza). ``True`` se e'
    partito adesso.
    """
    key = str(Path(workspace))
    if key in _IN_FLIGHT:
        return False
    retry_at = _RETRY_AT.get(key)
    if retry_at is not None and time.monotonic() < retry_at:
        return False
    _IN_FLIGHT.add(key)

    async def run() -> None:
        try:
            done = await sort_unsorted(Path(workspace), llm)
            _RETRY_AT.pop(key, None)
            if done:
                logger.info("memory themes: sorted {} entries", done)
        except Exception as exc:  # noqa: BLE001 — sottofondo: si riprova piu' tardi
            _RETRY_AT[key] = time.monotonic() + RETRY_AFTER_S
            logger.warning("memory themes: sorting failed, retry later: {}", exc)
        finally:
            _IN_FLIGHT.discard(key)

    task = asyncio.get_running_loop().create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return True
