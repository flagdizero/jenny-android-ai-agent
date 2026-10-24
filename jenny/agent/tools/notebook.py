"""Il tool ``notebook``: un quaderno letto, controllato e sfoltito in blocco.

Nato dalla prova del Piano a lista del 23/10/2026 sul telefono: per verificare
un quaderno l'editore ha fatto 39 ``read_file`` in 39 iterazioni, e un analyst
ha riscritto in ``python_exec`` i controlli che ``lint_wiki.py`` fa già. Qui le
stesse cose sono una chiamata: ``check`` (i difetti, contati, con
percorso:riga), ``map`` (quali file nominano quale soggetto × argomento),
``read_many`` (fino a 30 file insieme). La logica sta in
:mod:`jenny.board.notebook_ops`; qui restano la radice, i tetti e le scritture.

**Due classi con lo stesso nome**, come ``app`` (``app_tool.py``):

- :class:`NotebookReadTool`, scope ``orchestrator`` e ``core``: Jenny, solo le
  tre letture (``read_only``, quindi in parallelo con le altre letture);
- :class:`NotebookEditTool`, scope ``notebook``: l'editore dei quaderni, anche
  quello in sola lettura della pianificazione, con in più ``remove_pages``.

Gli scope **devono restare disgiunti**: il loader rifiuta due tool con lo stesso
nome nello stesso registro, e l'editore carica ``subagent`` + ``notebook``. Lo
tiene vero ``tests/agent/tools/test_notebook_tool.py``.

La radice è quella di ``BoardTool._root()``: la conversazione di un quaderno, poi
la ``write_root()`` del turno; nessun percorso come argomento.

**Le uscite stanno sotto lo scarico** in ``.jenny/tool-results/`` (16.000
caratteri, ``max_tool_result_chars``): 12.000 per Jenny, che si porta dietro
ogni risultato per tutta la conversazione, 14.000 per l'editore. Un risultato
scaricato si rilegge con ``read_file``, e dentro un aiutante quella lettura
contamina il lavoro (``subagent_policy``): l'uscita deve bastare da sola.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.file_state import FileStates, current_file_states
from jenny.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.board import notebook_ops as ops
from jenny.security.subagent_policy import check_write as check_subagent_write
from jenny.security.subagent_policy import must_stage as must_stage_subagent_write
from jenny.security.subagent_policy import note_write as note_subagent_write
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)
from jenny.security.workspace_policy import (
    is_path_within,
    refuse_board_write,
    resolve_allowed_path,
)
from jenny.utils.wiki_paths import WIKI_INDEX_FILENAME

_READS = ("check", "map", "read_many")
_EDIT_ACTIONS = (*_READS, "remove_pages")
_MAX_SUBJECTS = 40
_MAX_TOPICS = 20
_PER_CELL = 3
_MAX_REMOVE = 50
_DEFAULT_LIMIT = 40
# Dove resta la traccia di una pagina tolta: ``raw/removed/<giorno>.md``. Non in
# ``raw/notes/`` (il passo 11 di ``lint_wiki`` vuole un riassunto per ogni nota)
# e non in ``audit/`` (il passo 6 vuole voci di audit, con il loro frontmatter):
# ``raw/removed/`` non lo guarda nessun passo del lint.
_TRACE_DIR = "raw/removed"
# Le cartelle del quaderno accanto a ``wiki/``: niente lì è una pagina.
_NOT_PAGES = frozenset({"raw", "audit", "log", "board", ".jenny"})


def _schema(edit: bool) -> dict[str, Any]:
    actions = _EDIT_ACTIONS if edit else _READS
    remove = (" remove_pages (pages, reason): delete wiki pages — only when the request or the "
              "plan step says so; the board, drawers and reminders follow." if edit else "")
    extra: dict[str, Any] = {}
    if edit:
        extra = {
            "pages": ArraySchema(
                StringSchema("A page under wiki/, e.g. wiki/entities/Acero.md (never "
                             "wiki/index.md)."),
                description="remove_pages: the pages to delete.", max_items=_MAX_REMOVE),
            "reason": StringSchema("remove_pages: why, in one sentence (it goes in the trace)."),
        }
    return tool_parameters_schema(
        action=StringSchema(
            "check (expect?, limit?, min_chars?): the notebook's defects, counted, with "
            "path:line — dead [[links]], sources:/source: that do not exist, pages without "
            "frontmatter or state, pages over the ceiling, the map over its ceiling, board "
            "cards, drawers and reminders pointing at missing pages. "
            "map (subjects, topics, paths?): which files mention which subject together with "
            "which topic, with file:line. "
            "read_many (paths, mode?, lines?, max_chars?): up to 30 files in one call." + remove,
            enum=list(actions),
        ),
        expect=ObjectSchema(
            folders=StringSchema("A glob of folders, e.g. wiki/concepts/*: each must hold "
                                 "exactly `files`."),
            files=ArraySchema(StringSchema("A file name; .md is added when missing."),
                              description="Without folders: paths from the notebook root."),
            description="check: the structure you expect, to list what is missing or extra.",
        ),
        limit=IntegerSchema(description="check: how many defects to list (default 40).",
                            minimum=1, maximum=200),
        min_chars=IntegerSchema(description="check: also list pages shorter than this.",
                                minimum=0, maximum=6000),
        subjects=ArraySchema(
            ObjectSchema(name=StringSchema("The subject, e.g. a plant."),
                         aliases=ArraySchema(StringSchema("Another name for it.")),
                         required=["name"]),
            description="map: the rows.", max_items=_MAX_SUBJECTS),
        topics=ArraySchema(
            ObjectSchema(name=StringSchema("The topic, e.g. soil."),
                         keywords=ArraySchema(StringSchema("A word that means it.")),
                         required=["name"]),
            description="map: the columns.", max_items=_MAX_TOPICS),
        paths=ArraySchema(
            StringSchema("A path inside the notebook."),
            description="map: the folders to search (default wiki/ and raw/). read_many: the "
                        "files, relative to the notebook (or to wiki/).",
            max_items=ops.MAX_READ_PATHS),
        mode=StringSchema("read_many: frontmatter, head (the first `lines`) or full "
                          "(default).", enum=["frontmatter", "head", "full"]),
        lines=IntegerSchema(description="read_many head: lines per file (default 20).",
                            minimum=1, maximum=400),
        max_chars=IntegerSchema(description="read_many: at most this many characters per file.",
                                minimum=200, maximum=14000),
        **extra,
        required=["action"],
    )


class _NotebookTool(Tool):
    """Quel che le due facce hanno in comune: radice, tetti, letture."""

    _actions: tuple[str, ...] = _READS
    _max_chars = 12000

    def __init__(self, origin_session_key: str | None = None,
                 file_states: FileStates | None = None, timezone: str | None = None) -> None:
        # Dentro il giro dell'editore la sessione di richiesta è la sua, non
        # quella del quaderno: come ``BoardTool``.
        self._origin = origin_session_key
        self._explicit_file_states = file_states
        self._fallback_file_states = FileStates()
        self._timezone = timezone

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        origin = getattr(ctx, "origin_session_key", None)
        states = getattr(ctx, "file_states", None)
        tz = getattr(ctx, "timezone", None)
        return cls(origin_session_key=origin if isinstance(origin, str) else None,
                   file_states=states if isinstance(states, FileStates) else None,
                   timezone=tz if isinstance(tz, str) else None)

    @property
    def name(self) -> str:
        return "notebook"

    @property
    def _file_states(self) -> FileStates:
        if self._explicit_file_states is not None:
            return self._explicit_file_states
        return current_file_states(self._fallback_file_states)

    def _root(self) -> Path | None:
        from jenny.session.keys import is_project_session_key
        from jenny.utils.wiki_paths import is_wiki_root

        key = self._origin or current_request_session_key() or ""
        if not is_project_session_key(key):
            return None
        scope = current_workspace_scope()
        root = scope.write_root() if scope is not None else None
        return root if root is not None and is_wiki_root(root) else None

    async def execute(self, action: str = "", **kw: Any) -> str:
        if action not in self._actions:
            return f"Error: unknown action {action!r}: use {', '.join(self._actions)}."
        root = self._root()
        if root is None:
            return ("Error: not from here — the notebook tool works only in a notebook's chat. "
                    "Nothing was read.")
        if action == "check":
            return await asyncio.to_thread(self._check, root, kw)
        if action == "map":
            return await asyncio.to_thread(self._map, root, kw)
        if action == "read_many":
            return await self._read_many(root, kw)
        return await self._remove_pages(root, kw)

    def _check(self, root: Path, kw: dict[str, Any]) -> str:
        expect = kw.get("expect")
        report = ops.check_notebook(root, expect=expect if isinstance(expect, dict) else None,
                                    min_chars=_int(kw.get("min_chars"), 0, 0, 6000))
        return ops.render_check(report, limit=_int(kw.get("limit"), _DEFAULT_LIMIT, 1, 200),
                                max_chars=self._max_chars)

    def _map(self, root: Path, kw: dict[str, Any]) -> str:
        subjects = ops.parse_terms(kw.get("subjects"), "aliases")[:_MAX_SUBJECTS]
        topics = ops.parse_terms(kw.get("topics"), "keywords")[:_MAX_TOPICS]
        if not subjects or not topics:
            return ("Error: map needs subjects ([{name, aliases}]) and topics "
                    "([{name, keywords}]), at least one each.")
        paths = _strings(kw.get("paths")) or ["wiki", "raw"]
        return ops.map_notebook(root, subjects, topics, paths[:ops.MAX_READ_PATHS],
                                per_cell=_PER_CELL, max_chars=self._max_chars)

    async def _read_many(self, root: Path, kw: dict[str, Any]) -> str:
        paths = _strings(kw.get("paths"))
        if not paths:
            return "Error: read_many needs paths: the files, relative to the notebook."
        if len(paths) > ops.MAX_READ_PATHS:
            return (f"Error: at most {ops.MAX_READ_PATHS} files per call; you gave "
                    f"{len(paths)}. Split them.")
        mode = kw.get("mode") if kw.get("mode") in ("frontmatter", "head", "full") else "full"
        per_file = kw.get("max_chars")
        result = await asyncio.to_thread(
            ops.read_many, root, paths, mode=str(mode), lines=_int(kw.get("lines"), 20, 1, 400),
            max_chars=_int(per_file, 0, 200, 14000) if per_file is not None else None,
            total_chars=self._max_chars,
        )
        # Come ``read_file``: chi ha letto può correggere senza l'avviso «non
        # l'hai letto». Mai come lettura da deduplicare: quel che si è visto qui
        # può essere solo la testa del file, e un ``read_file`` dopo deve dare il
        # file, non «invariato dall'ultima lettura».
        for path, count in result.read:
            if count == 0:
                continue
            self._file_states.record_read(path, offset=1, limit=count)
            entry = self._file_states.get(path)
            if entry is not None:
                entry.can_dedup = False
        return result.text

    async def _remove_pages(self, root: Path, kw: dict[str, Any]) -> str:
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        pages = _strings(kw.get("pages"))
        reason = " ".join(str(kw.get("reason") or "").split())
        if not pages or not reason:
            return "Error: remove_pages needs pages (under wiki/) and a reason."
        if len(pages) > _MAX_REMOVE:
            return f"Error: at most {_MAX_REMOVE} pages per call."
        try:
            targets, missing = await asyncio.to_thread(_resolve_pages, root, pages)
        except (OSError, ValueError) as exc:  # i recinti sono PermissionError
            return f"Error: nothing was removed — {exc}"
        if not targets:
            return f"None of those pages exists: nothing was removed ({', '.join(missing)})."
        out = await asyncio.to_thread(self._remove, root, targets, reason, missing)
        if out.startswith("Error"):
            return out
        from jenny.board.notify import board_changed

        await board_changed(root.name)
        return out

    def _remove(self, root: Path, targets: list[tuple[str, Path]], reason: str,
                missing: list[str]) -> str:
        """Traccia, cancellazione, bacheca, promemoria: in quest'ordine.

        La bacheca **dopo** i file: ``edit.remove_pages`` segna come «già viste»
        le pagine vere tolte da ogni scheda (``orphansSeen``), e una pagina già
        cancellata non è più vera, quindi non sporca quell'elenco.
        """
        from jenny.agent.tools.board import _redraw
        from jenny.board import edit

        paths = {p for _, p in targets}
        linked = ops.links_into(root, paths, skip=paths)
        try:
            trace, trace_path = self._write_trace(root, targets, reason)
        except OSError as exc:  # anche un recinto (PermissionError) sulla traccia
            return f"Error: nothing was removed — the trace could not be written: {exc}"
        removed: list[str] = []
        failed: list[str] = []
        for rel, path in targets:
            try:
                path.unlink()
            except OSError as exc:
                failed.append(f"wiki/{rel} ({exc.strerror or exc})")
                continue
            _forget_taint(path)
            removed.append(rel)
        if failed:
            # La traccia è scritta prima: chi la rilegge deve sapere quali sono
            # ancora al loro posto.
            _append(trace_path, f"\nNot removed (still in place): {'; '.join(failed)}\n")
        if not removed:
            return "Nothing was removed: " + "; ".join(failed)
        # Le pagine sono già sparite: un guasto della bacheca non deve far credere
        # il contrario, né lasciare il resto della risposta per strada.
        taken: dict[str, list[str]] = {}
        redraw: list[str] = []
        cleared: list[dict[str, str]] = []
        board_error = reminders_error = ""
        try:
            taken, redraw = edit.remove_pages(root, removed)
        except Exception as exc:  # noqa: BLE001 — la risposta lo dice, con il rimedio
            board_error = str(exc) or type(exc).__name__
        try:
            cleared = edit.clear_reminder_whys(root, removed)
        except Exception as exc:  # noqa: BLE001
            reminders_error = str(exc) or type(exc).__name__

        lines = [f"Removed {len(removed)} page(s): {', '.join(f'wiki/{r}' for r in removed)}. "
                 f"Trace (path, first lines, reason): {trace}."]
        if board_error:
            lines.append(f"Board not updated: {board_error}; take the pages off with board "
                         f"action=remove_pages ({', '.join(removed)}).")
        elif taken:
            lines.append(f"Cards touched: {len(taken)} — " + "; ".join(
                f"`{cid}`: {', '.join(gone)}" for cid, gone in taken.items()))
        else:
            lines.append("Cards touched: 0 (no card held them).")
        lines.append(f"Drawers to rewrite: {len(redraw)}." if redraw else "Drawers to rewrite: 0.")
        redraw_note = _redraw(root, redraw)
        if redraw_note:
            lines.append(redraw_note.strip())
        if reminders_error:
            lines.append(f"Reminders not updated: {reminders_error}; run notebook action=check "
                         "and fix their why with board update_reminder.")
        elif cleared:
            lines.append(f"Reminders cleared: {len(cleared)} — their why pointed to a removed "
                         "page; give them another with board update_reminder if one explains "
                         "them: " + "; ".join(f"`{c['id']}` «{c['text']}» (was {c['why']})"
                                              for c in cleared))
        else:
            lines.append("Reminders cleared: 0.")
        if linked:
            shown = linked[:20]
            lines.append(f"Links that now point nowhere ({len(linked)}; fix them with edit_file):")
            lines.extend(f"- {ref}" for ref in shown)
            if len(linked) > len(shown):
                lines.append(f"- … {len(linked) - len(shown)} more (notebook action=check)")
        if missing:
            lines.append(f"Not found, nothing to remove: {', '.join(missing)}.")
        if failed:
            lines.append(f"Not removed: {'; '.join(failed)}.")
        out = "\n".join(lines)
        return out if len(out) <= self._max_chars else out[: self._max_chars - 40] + "\n(cut)"

    def _write_trace(self, root: Path, targets: list[tuple[str, Path]],
                     reason: str) -> tuple[str, Path]:
        """Appende a ``raw/removed/<giorno>.md`` percorso, prime righe e
        motivo di ogni pagina, **prima** di cancellarle. Stessi recinti di una
        scrittura dei tool file.

        In append e con una scrittura sola: due rimozioni nello stesso giorno non
        si leggono e riscrivono a vicenda, quindi nessuna perde la sezione
        dell'altra.
        """
        from jenny.utils.helpers import safe_zoneinfo

        now = datetime.now(safe_zoneinfo(self._timezone or "UTC"))
        rel = f"{_TRACE_DIR}/{now:%Y-%m-%d}.md"
        path = _fenced(root, root / rel)
        section = [f"\n## {now:%H:%M} — {len(targets)} page(s)\n", f"Reason: {reason}\n"]
        section.extend(ops.page_trace(root, p) for _, p in targets)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = _append(path, "\n".join(section),
                          header=f"# Pages removed — {now:%Y-%m-%d}\n")
        # Un file nuovo è tutto di questo lavoro; un'aggiunta no (v. ``note_write``).
        note_subagent_write(path, full=created)
        return rel, path


@tool_parameters(_schema(edit=False))
class NotebookReadTool(_NotebookTool):
    """La faccia di Jenny: le tre letture, mai una scrittura."""

    _scopes = {"orchestrator", "core"}
    _actions = _READS
    _max_chars = 12000

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Read this notebook in bulk, from its chat: check lists its defects (dead links, "
            "missing sources, pages without state or over the ceiling, board pointing at missing "
            "pages) — the structural checks, not the full lint; map shows which files mention "
            "which subject with which topic; read_many reads up to 30 files at once. Read-only."
        )


@tool_parameters(_schema(edit=True))
class NotebookEditTool(_NotebookTool):
    """La faccia dell'editore dei quaderni: le letture, e ``remove_pages``."""

    _scopes = {"notebook"}
    _actions = _EDIT_ACTIONS
    _max_chars = 14000

    @property
    def description(self) -> str:
        return (
            "This notebook in bulk: check (its defects, with path:line: the structural checks, "
            "not the full lint — use it to verify instead of rereading), map (which files "
            "mention which subject with which topic), read_many (up to 30 files in one call, "
            "instead of one read_file each), and remove_pages (delete wiki pages, with a trace "
            "in raw/removed/; the board, drawers and reminders follow). Delete only when the "
            "request or the plan step says so."
        )


def _append(path: Path, text: str, *, header: str = "") -> bool:
    """Appende *text* a *path* in una scrittura sola; *header* solo se il file
    nasce adesso. Restituisce se l'ha creato."""
    with open(path, "a", encoding="utf-8") as fh:
        created = fh.tell() == 0
        fh.write((header if created else "") + text)
    return created


def _forget_taint(path: Path) -> None:
    """Un file cancellato non porta più contaminazione: via la sua voce dal
    registro (``security.taint_ledger``). Fuori da un subagent non c'è registro."""
    from jenny.security.subagent_policy import current_subagent_policy
    from jenny.security.taint_ledger import ledger_for

    policy = current_subagent_policy()
    parts = policy.relative(path) if policy is not None else None
    if policy is not None and parts:
        ledger_for(policy.install_root).clear("/".join(parts))


def _fenced(root: Path, target: Path) -> Path:
    """I recinti di una scrittura dei tool file (``_FsTool._resolve_write``), per
    un percorso che deve restare dentro il quaderno."""
    resolved = resolve_allowed_path(target, allowed_root=root)
    refuse_board_write(resolved)
    check_subagent_write(resolved)
    if must_stage_subagent_write(resolved):
        # Una cancellazione non si mette in bozza: un lavoro non fidato non tocca
        # i file che Jenny carica da sola.
        raise PermissionError(f"{target.name} is loaded by Jenny on her own: this job "
                              "cannot change it")
    return resolved


def _resolve_pages(root: Path, pages: list[str]) -> tuple[list[tuple[str, Path]], list[str]]:
    """Le pagine da togliere, tutte controllate **prima** di toglierne una.

    Un recinto che rifiuta una pagina ferma tutto: una cancellazione a metà è
    più difficile da capire di un rifiuto. Le pagine che non ci sono si
    elencano e basta (forse le ha già tolte qualcun altro).
    """
    wiki = (root / "wiki").resolve()
    out: list[tuple[str, Path]] = []
    missing: list[str] = []
    for raw in pages:
        # Normalizzato prima di ogni confronto: ``./index.md`` o ``sub/../index.md``
        # sono la mappa quanto ``index.md`` (revisione del 24/10/2026).
        rel = os.path.normpath(raw.strip().replace("\\", "/").lstrip("/")).replace(os.sep, "/")
        # Un percorso che comincia da un'altra cartella del quaderno la nomina:
        # non è una pagina scritta relativa a ``wiki/``, come le elenca la bacheca.
        if rel.split("/", 1)[0] in _NOT_PAGES:
            raise ValueError(f"{raw}: only wiki pages (.md under wiki/) can be removed")
        rel = rel.removeprefix("wiki/")
        if rel == ".." or rel.startswith("../"):
            raise ValueError(f"{raw}: not under wiki/")
        if not rel.endswith(".md"):
            raise ValueError(f"{raw}: only wiki pages (.md under wiki/) can be removed")
        candidate = root / "wiki" / rel
        # Un link non si segue per cancellare: ``unlink`` sul percorso risolto
        # toglierebbe il file a cui punta (anche la mappa) e lascerebbe il link
        # morto. Vale per il file e per ogni cartella fra lui e ``wiki/``.
        link = next((p for p in (candidate, *candidate.parents)
                     if p != root / "wiki" and p.is_relative_to(root / "wiki")
                     and p.is_symlink()), None)
        if link is not None:
            raise ValueError(f"{raw}: {link.name} is a link — only real pages are removed")
        resolved = _fenced(root, candidate)
        if not is_path_within(resolved, wiki):
            raise ValueError(f"{raw}: not under wiki/")
        if resolved == wiki / WIKI_INDEX_FILENAME:
            raise ValueError("wiki/index.md is the map: it is never removed")
        if not resolved.is_file():
            missing.append(raw)
            continue
        canon = resolved.relative_to(wiki).as_posix()
        if all(canon != done for done, _ in out):
            out.append((canon, resolved))
    return out, missing


def _strings(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    return [s.strip() for s in items if isinstance(s, str) and s.strip()]


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


TOOLS = [NotebookReadTool, NotebookEditTool]
