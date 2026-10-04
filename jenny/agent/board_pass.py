"""La passata della bacheca: la seconda metà del giardiniere.

Gira **dopo** la passata della wiki, nella stessa esecuzione del job, con un
prompt suo (``agent/board_pass.md``) e una cassetta sua
(``jenny/agent/board_tools.py`` più i tool di file del giardiniere, per applicare
le correzioni alla wiki).

La divisione fra codice e modello è quella del giardiniere — **al modello il
giudizio, non l'esplorazione**:

- il codice sa quali pagine sono nuove e fuori bacheca, quali cassetti hanno
  pagine cambiate (le impronte), quali correzioni aspettano, quali schede sono
  ferme da abbastanza da poterle mettere via, e scrive le righe di «dall'ultima
  volta»;
- il modello decide a quale scheda appartiene una pagina nuova, scrive i
  riassunti, estrae le attese dal diario e scrive le proposte.

Il rispetto della forma dell'utente non è affidato al prompt: gli strumenti non
hanno porte per spostare o togliere, e l'imbuto rifiuta qualunque scrittura di
Jenny che tocchi quel che è dell'utente.

**Le passate a vuoto non partono.** :meth:`BoardPass.reason` dice se c'è lavoro
— una bacheca da proporre, cassetti con pagine cambiate, pagine nuove fuori
bacheca, correzioni, pagine chieste in bacheca, righe di diario nuove — e la
selezione del cron (``gardener_schedule.pick_project``) fa la stessa domanda.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.board_tools import BoardPassState, build_board_tools
from jenny.board import model
from jenny.board.corrections import open_corrections
from jenny.board.store import mutate_board, read_board, read_card_summary, read_reminders
from jenny.utils.prompt_templates import render_template
from jenny.utils.wiki_paths import iter_wiki_pages

# Quanto delle pagine di un cassetto entra nel prompt, e quanto in tutto. Un
# cassetto è un indice: due righe per sezione si scrivono dalle prime righe di
# ogni pagina, e una pagina oltre il tetto entra tagliata e lo dice.
_PAGE_CHARS = 2500
_CARD_CHARS = 9000
_TOTAL_CHARS = 30000
# Nella prima bacheca ogni pagina entra con la sua testa, per raggrupparle.
_MIGRATION_PAGE_CHARS = 400
_MIGRATION_TOTAL = 24000
# Il freno fra due passate della bacheca senza righe di diario nuove (v.
# ``BoardPass.reason``): un'ora per quel che ha chiesto l'utente, sei per il resto.
USER_RETRY_MINUTES = 60
RETRY_MINUTES = 360

# Le righe ``- [ ]`` con dentro una data: le attese sepolte nelle pagine.
_TODO_RE = re.compile(r"^\s*[-*]\s+\[ \]\s+(.+)$", re.M)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(4, longest + 1)


@dataclass
class BoardOutcome:
    status: str
    assigned: int = 0
    summaries: int = 0
    reminders: int = 0
    suggestions: int = 0
    resolved: int = 0
    elapsed: float = 0.0


class BoardPass:
    """L'inventario, il prompt e la cassetta della passata della bacheca, per un quaderno."""

    def __init__(self, root: Path, workspace: Path, *, now: datetime | None = None) -> None:
        self.root = root
        self.workspace = workspace
        self.now = now or datetime.now().astimezone()
        self.board = read_board(root)
        self.reminders = read_reminders(root)
        self.titles: dict[str, str] = dict(iter_wiki_pages(root / "wiki"))
        self._texts: dict[str, str] = {}

    @property
    def rel_root(self) -> str:
        try:
            return self.root.resolve().relative_to(self.workspace.resolve()).as_posix()
        except ValueError:
            return self.root.name

    def text(self, page: str) -> str:
        if page not in self._texts:
            self._texts[page] = _read(self.root / "wiki" / page)
        return self._texts[page]

    # -- inventario -------------------------------------------------------

    def is_research_wiki(self) -> bool:
        """Le wiki di ricerca (``concepts/``/``entities/``) restano senza bacheca.

        È una decisione e non un'omissione: hanno un'altra forma, si aprono con
        la ricerca, e la loro bacheca si decide dopo aver visto questa al lavoro.
        """
        pages = self.root / "wiki"
        return (pages / "concepts").is_dir() or (pages / "entities").is_dir()

    def owners(self) -> dict[str, str]:
        return model.page_owners(self.board) if self.board else {}

    def orphans(self) -> list[str]:
        owners = self.owners()
        return sorted(p for p in self.titles if p not in owners)

    def new_orphans(self) -> list[str]:
        seen = set(self.board["orphansSeen"]) if self.board else set()
        return [p for p in self.orphans() if p not in seen]

    def card_fingerprint(self, card: dict[str, Any]) -> str:
        return model.fingerprint(
            (p, self.text(p)) for p in card.get("pages") or [] if p in self.titles
        )

    def stale_cards(self) -> list[dict[str, Any]]:
        """I cassetti da riscrivere: mai scritti, o con le pagine cambiate dall'ultima volta."""
        if not self.board:
            return []
        out = []
        for card in self.board["cards"]:
            if not any(p in self.titles for p in card.get("pages") or []):
                continue
            summary = read_card_summary(self.root, card["id"])
            if summary is None or summary.get("fingerprint") != self.card_fingerprint(card):
                out.append(card)
        return out

    def remove_candidates(self) -> list[str]:
        """Le schede ferme da :data:`model.REMOVE_AFTER_DAYS` giorni e senza attese aperte."""
        if not self.board:
            return []
        limit = self.now - timedelta(days=model.REMOVE_AFTER_DAYS)
        busy = {r.get("card") for r in model.open_reminders(self.reminders)}
        out = []
        for card in self.board["cards"]:
            if card["id"] in busy or card.get("pinned"):
                continue
            moments = [model.parse_iso(card.get("created"))]
            summary = read_card_summary(self.root, card["id"]) or {}
            moments += [model.parse_iso(s.get("changed")) for s in summary.get("sections") or []]
            for page in card.get("pages") or []:
                try:
                    moments.append(datetime.fromtimestamp(
                        (self.root / "wiki" / page).stat().st_mtime).astimezone())
                except OSError:
                    pass
            known = [m for m in moments if m is not None]
            if known and max(known) < limit:
                out.append(card["id"])
        return out

    def apps_with_section(self) -> dict[str, dict[str, Any]]:
        try:
            from jenny.apps.manifest import scan_apps
        except Exception:  # noqa: BLE001 — senza app si propone senza sezioni
            return {}
        out = {}
        for app in scan_apps(self.workspace):
            if app.manifest is not None and app.manifest.section is not None:
                out[app.slug] = {"name": app.manifest.name, "params": app.manifest.section.params}
        return out

    def is_migrating(self) -> bool:
        """Se il quaderno aspetta ancora la sua prima bacheca.

        Nessuna bacheca, o una bacheca senza schede su cui non c'e' gia' una
        proposta in attesa, un «no» o un «piu' avanti» ancora valido: una prima
        bacheca rifiutata non si ripropone da sola.
        """
        board = self.board
        if board is None:
            return True
        if board["cards"]:
            return False
        if any(s["kind"] == "initial_board" for s in board["suggestions"]):
            return False
        if any(d.get("kind") == "initial_board" for d in board["declined"]):
            return False
        return not any(
            x.get("kind") == "initial_board" and int(x.get("untilPass") or 0) > board["passes"]
            for x in board["later"]
        )

    def _rested(self, minutes: int) -> bool:
        last = model.parse_iso((self.board or {}).get("lastPassAt"))
        return last is None or self.now - last >= timedelta(minutes=minutes)

    def reason(self, journal_lines: int = 0) -> str | None:
        """Perché questa passata deve girare, o ``None`` se non c'è niente da fare.

        **Il freno.** Una ragione che resta vera dopo la passata (una correzione
        che il modello non ha saputo applicare, un cassetto che non ha scritto)
        si ripresenterebbe a ogni battito del cron. Le ragioni dell'utente —
        una correzione, una pagina chiesta in bacheca — aspettano
        :data:`USER_RETRY_MINUTES`; le altre :data:`RETRY_MINUTES`. Le righe di
        diario nuove no: quelle le porta una passata della wiki, che ha già il
        suo freno.
        """
        if self.is_research_wiki() or not self.titles:
            return None
        migrating = self.is_migrating()
        if journal_lines:
            return "migrate" if migrating else "journal"
        if not self._rested(USER_RETRY_MINUTES):
            return None
        if migrating:
            return "migrate" if self._rested(RETRY_MINUTES) else None
        assert self.board is not None
        if open_corrections(self.root):
            return "corrections"
        if self.board["offboardWants"]:
            return "wants"
        if not self._rested(RETRY_MINUTES):
            return None
        if self.stale_cards():
            return "drawers"
        if self.new_orphans():
            return "pages"
        return None

    # -- prompt -----------------------------------------------------------

    def _section(self, title: str, body: str) -> str:
        return f"## {title}\n\n{body}"

    def build_state(self) -> BoardPassState:
        stale = self.stale_cards()
        return BoardPassState(
            root=self.root,
            stale={c["id"] for c in stale},
            orphans=set(self.orphans()),
            remove_candidates=set(self.remove_candidates()),
            apps_with_section=self.apps_with_section(),
            fingerprints={c["id"]: self.card_fingerprint(c) for c in stale},
            migration=self.is_migrating(),
        )

    def build_prompt(self, state: BoardPassState, journal: str = "") -> str:
        board = self.board or model.empty_board()
        corrections = open_corrections(self.root)
        new = set(self.new_orphans())
        full = model.is_full(board, self.reminders)
        parts = [render_template(
            "agent/board_pass.md",
            strip=True,
            project_path=self.rel_root,
            migration=state.migration,
            corrections=bool(corrections),
            new_orphans=bool(new),
            stale=bool(state.stale) or state.migration,
            journal=bool(journal.strip()),
            max_suggestions=model.MAX_SUGGESTIONS_PER_PASS,
            max_initial=model.MAX_INITIAL_CARDS,
            min_pages=model.NEW_CARD_MIN_PAGES,
            full=full,
            glyphs=", ".join(model.GLYPHS),
        )]
        hint = str(board.get("remindHint") or "")
        if hint:
            parts.append(self._section(
                "What the user wants to be reminded of",
                f"When they created this notebook they said: «{hint}». Extract reminders for "
                "that kind of thing first.",
            ))
        if state.migration:
            if board["skeleton"]:
                parts.append(self._section(
                    "The skeleton the user chose",
                    ", ".join(board["skeleton"]) + "\n\nUse it as it is: it is theirs.",
                ))
            parts.append(self._migration_pages())
            return "\n\n---\n\n".join(parts)

        cards = []
        for card in board["cards"]:
            pages = ", ".join(f"`{p}`" for p in card["pages"]) or "_(no pages)_"
            summary = read_card_summary(self.root, card["id"]) or {}
            brief = f" — {summary['brief']}" if summary.get("brief") else ""
            cards.append(f"- `{card['id']}` **{card['title']}**{brief}\n  pages: {pages}")
        parts.append(self._section(
            "The board as it stands",
            f"Skeleton of every drawer: {', '.join(board['skeleton']) or '_(none)_'}\n\n"
            + ("\n".join(cards) or "_(no cards)_")
            + f"\n\nThreads: {len(board['connections'])} of at most {model.MAX_CONNECTIONS}; "
            f"items on the board: {model.items_on_board(board, self.reminders)} of at most "
            f"{model.MAX_ITEMS}.",
        ))
        if corrections:
            blocks = []
            for c in corrections:
                page = c["target"]
                body = self.text(page)
                fence = _fence(body)
                also = f" (also: {', '.join(c['sources'][1:])})" if len(c["sources"]) > 1 else ""
                blocks.append(
                    f"### Correction `{c['id']}` — card `{c['card']}`, section «{c['section']}»\n\n"
                    f"The user says: {c['text']}\n\nIt goes on `{self.rel_root}/wiki/{page}`{also}:"
                    f"\n\n{fence}markdown\n{body[:_PAGE_CHARS]}\n{fence}"
                )
            parts.append(self._section("Corrections waiting", "\n\n".join(blocks)))
        orphans = self.orphans()
        if orphans:
            rows = [f"- `{p}` — {self.titles[p]}{' **(new)**' if p in new else ''}"
                    for p in orphans[:200]]
            parts.append(self._section("Pages off the board", "\n".join(rows)))
        if state.stale:
            parts.append(self._stale_section(board, state))
        wants = board["offboardWants"]
        if wants:
            rows = [f"- `{w['page']}`" + (f" (opened from `{w['from']}`)" if w.get("from") else "")
                    for w in wants]
            parts.append(self._section("Pages the user wants on the board", "\n".join(rows)))
        if journal.strip():
            parts.append(self._section("New journal lines", f"````markdown\n{journal}\n````"))
        rems = [f"- `{r['id']}` on `{r['card']}`: {r['text']} ({r['when'].get('date', '')})"
                for r in model.open_reminders(self.reminders)]
        parts.append(self._section("Reminders already on the list", "\n".join(rems) or "_(none)_"))
        if state.remove_candidates:
            parts.append(self._section(
                "Cards still for a long time",
                ", ".join(f"`{c}`" for c in sorted(state.remove_candidates)),
            ))
        if board["declined"]:
            rows = [f"- {d.get('kind')}: {d.get('subject')}" for d in board["declined"][-20:]]
            parts.append(self._section("Proposals the user declined", "\n".join(rows)))
        if state.apps_with_section:
            rows = [f"- `{slug}` — {info['name']}; asks for: {', '.join(info['params']) or 'nothing'}"
                    for slug, info in state.apps_with_section.items()]
            parts.append(self._section("Apps that can show live data in a drawer", "\n".join(rows)))
        return "\n\n---\n\n".join(parts)

    def _stale_section(self, board: dict[str, Any], state: BoardPassState) -> str:
        blocks = []
        total = 0
        for card in board["cards"]:
            if card["id"] not in state.stale:
                continue
            old = read_card_summary(self.root, card["id"]) or {}
            current = "\n".join(f"- **{s['title']}**: {s['text']} (from {', '.join(s['sources'])})"
                                for s in old.get("sections") or []) or "_(never written)_"
            pages = []
            used = 0
            for page in card["pages"]:
                if page not in self.titles:
                    continue
                body = self.text(page)
                room = min(_PAGE_CHARS, _CARD_CHARS - used, _TOTAL_CHARS - total)
                if room <= 200:
                    pages.append(f"`{page}` — _(not shown: over the budget of this prompt; "
                                 "read it with read_file if you need it)_")
                    continue
                cut = body[:room]
                note = "\n_(cut here)_" if len(body) > room else ""
                fence = _fence(cut)
                pages.append(f"`{page}`\n\n{fence}markdown\n{cut}\n{fence}{note}")
                used += len(cut)
                total += len(cut)
            blocks.append(
                f"### `{card['id']}` — {card['title']}\n\nThe drawer now:\n{current}\n\n"
                + "\n\n".join(pages)
            )
        return self._section("Drawers to rewrite", "\n\n".join(blocks))

    def _migration_pages(self) -> str:
        rows = []
        total = 0
        todos = []
        for page, title in self.titles.items():
            body = self.text(page)
            head = " ".join(body.split())[:_MIGRATION_PAGE_CHARS]
            if total + len(head) > _MIGRATION_TOTAL:
                rows.append(f"- `{page}` — {title}")
            else:
                rows.append(f"- `{page}` — {title}: {head}")
                total += len(head)
            todos += [f"- `{page}`: {m.strip()}" for m in _TODO_RE.findall(body)]
        out = self._section("The pages of this notebook", "\n".join(rows))
        if todos:
            out += "\n\n" + self._section("Unchecked tasks found in the pages", "\n".join(todos[:60]))
        return out

    # -- dopo la passata ---------------------------------------------------

    def finish(self, state: BoardPassState) -> None:
        """Il codice racconta la passata, e mette via quel che ha visto.

        Le righe di «dall'ultima volta» le scrive il codice da quel che gli
        strumenti hanno fatto davvero: un modello che scrive il proprio registro
        può raccontare una passata che non ha fatto.
        """
        titles = {}
        if self.board:
            titles = {c["id"]: c["title"] for c in self.board["cards"]}
        orphans_now = [p for p in self.titles if p in state.orphans]

        def apply(board: dict[str, Any]) -> None:
            board["passes"] = int(board.get("passes") or 0) + 1
            board["orphansSeen"] = sorted(set(orphans_now))
            board["lastPassAt"] = model.now_iso()
            # Le pagine chieste in bacheca sono state lette: sono entrate in un
            # cassetto, sono diventate una proposta, o la passata ha giudicato
            # che non stanno da nessuna parte. Un segnale vale una passata.
            board["offboardWants"] = []
            board["later"] = [
                x for x in board["later"] if int(x.get("untilPass") or 0) > board["passes"]
            ]
            for card, count in state.summaries.items():
                if count and card in titles:
                    model.add_since(board, "sections_regenerated", card=card,
                                    title=titles[card], count=count)
            if state.assigned:
                model.add_since(board, "pages_assigned", count=len(state.assigned))
            if state.reminders:
                model.add_since(board, "reminders_added", count=len(state.reminders))
            if state.suggestions and not state.migration:
                model.add_since(board, "suggestions_added", count=len(state.suggestions))
            if state.resolved:
                model.add_since(board, "corrections_applied", count=len(state.resolved))

        mutate_board(self.root, apply, actor=model.JENNY)


async def run_board_pass(
    agent: Any,
    store: Any,
    *,
    journal: str = "",
    journal_lines: int = 0,
    session_key: str,
    write_guard: Any = None,
) -> BoardOutcome:
    """Una passata della bacheca sul quaderno di *store* (un ``GardenerStore``)."""
    from jenny.agent.internal_run import internal_run_completed
    from jenny.session.turn_visibility import silent_progress

    t0 = time.monotonic()
    bp = BoardPass(store.root, store.workspace)
    reason = bp.reason(journal_lines)
    if reason is None:
        return BoardOutcome(status="skipped")
    state = bp.build_state()
    tools = store.build_tools(write_guard=write_guard)
    for tool in build_board_tools(state):
        tools.register(tool)
    try:
        resp = await agent.process_direct(
            bp.build_prompt(state, journal),
            session_key=session_key,
            ephemeral=True,
            tools=tools,
            on_progress=silent_progress,
        )
    except Exception as exc:  # noqa: BLE001 — l'esito viaggia nell'outcome
        logger.exception("board pass over {} failed: {}", store.name, exc)
        # Il timbro anche qui: un provider giu' non deve far ripartire la
        # passata a ogni battito (v. il freno in ``BoardPass.reason``).
        try:
            BoardPass(store.root, store.workspace).finish(state)
        except Exception:  # noqa: BLE001
            pass
        return BoardOutcome(status="failed", elapsed=time.monotonic() - t0)
    status = "done" if internal_run_completed(resp) else "incomplete"
    try:
        # Anche su una passata incompleta: quel che gli strumenti hanno scritto
        # e' su disco, e va raccontato e contato.
        BoardPass(store.root, store.workspace).finish(state)
    except Exception as exc:  # noqa: BLE001 — il racconto non deve far cadere la passata
        logger.warning("board pass over {}: could not record the pass: {}", store.name, exc)
    outcome = BoardOutcome(
        status=status,
        assigned=len(state.assigned),
        summaries=len(state.summaries),
        reminders=len(state.reminders),
        suggestions=len(state.suggestions),
        resolved=len(state.resolved),
        elapsed=time.monotonic() - t0,
    )
    logger.info(
        "board pass over {} ({}): {} — {} pages assigned, {} drawers, {} reminders, "
        "{} proposals, {} corrections in {:.1f}s",
        store.name, reason, status, outcome.assigned, outcome.summaries, outcome.reminders,
        outcome.suggestions, outcome.resolved, outcome.elapsed,
    )
    return outcome


def board_needs_pass(root: Path, workspace: Path, journal_lines: int = 0) -> bool:
    """La stessa domanda della passata, per la selezione del cron."""
    try:
        return BoardPass(root, workspace).reason(journal_lines) is not None
    except Exception as exc:  # noqa: BLE001 — un quaderno illeggibile non ferma il tick
        logger.warning("board: could not tell whether {} needs a pass: {}", root.name, exc)
        return False
