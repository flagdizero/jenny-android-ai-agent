"""Le letture in blocco di un quaderno: controllo, mappa, letture a gruppi.

La logica dello strumento ``notebook`` (``jenny/agent/tools/notebook.py``), che
qui resta sottile. Nato dalla prova del 23/10/2026 sul telefono: l'editore dei
quaderni ha fatto 39 ``read_file`` in 39 iterazioni per verificare un quaderno,
e un analyst ha riscritto da zero, in ``python_exec``, i controlli che
``lint_wiki.py`` fa già. Qui gli stessi controlli girano in una chiamata sola e
rispondono in poche righe.

**Non si importa** ``jenny/skills/llm-wiki/scripts/lint_wiki.py``: stampa su
stdout, scrive ``.jenny/lint_journal.json``, su Android si carica dagli asset e
non deve importare ``jenny``. Le due implementazioni degli stessi controlli le
tiene insieme un test di parità su una wiki di prova
(``tests/board/test_notebook_ops.py``), come già succede per ``MAP_MAX_CHARS``;
le differenze volute sono elencate lì. ``check`` non è il lint intero: quel che
lascia fuori lo dice in ogni risposta (:data:`NOT_CHECKED`).

Tutto qui legge e basta: le scritture di ``remove_pages`` stanno nel tool, dove
le vede l'inventario delle scritture
(``tests/security/test_readonly_write_surfaces.py``).

Dentro un subagent, ogni file il cui **testo** entra nella risposta vale come
una lettura (:func:`jenny.security.subagent_policy.check_read`): marca il lavoro
se il file è contaminato, come fa ``grep`` sulle righe che mostra. I file
contati ma non mostrati no.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jenny.security.subagent_policy import SubagentSecretError
from jenny.security.subagent_policy import check_read as check_subagent_read
from jenny.security.workspace_policy import is_path_within
from jenny.utils.wiki_paths import WIKI_INDEX_FILENAME, iter_wiki_pages, page_chars

# ── Le regole condivise con ``lint_wiki.py`` (copie: v. la docstring) ────────

# Il vocabolario di ``state:``. Copia di ``lint_wiki.PAGE_STATES``.
PAGE_STATES = frozenset({"open", "hypothesis", "decided", "done"})
# Le cartelle che dichiarano il layout di ricerca. Copia di ``RESEARCH_SUBDIRS``.
RESEARCH_SUBDIRS = ("concepts", "entities", "summaries")
# Il frontmatter tollerante del lint (BOM, riga vuota prima, ``---`` finale senza
# a capo): un 🔴 su un file corretto costa la credibilità dei 🔴 veri.
_FRONTMATTER_RE = re.compile(r"^﻿?\s*---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_CODE_FENCE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*(?:\n.*?)?(?:^[ \t]*\1[ \t]*$|\Z)",
    re.DOTALL | re.MULTILINE,
)
_INLINE_CODE_RE = re.compile(r"(`{1,2})(?:(?!\1)[\s\S])*?\1")

# I file di testo che ``map`` legge: un PDF in ``raw/papers`` non si cerca a righe.
_TEXT_SUFFIXES = frozenset({
    ".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml",
})
# Oltre, un file non si legge per una mappa o un controllo: il telefono non deve
# caricare un dump intero per contarne le parole.
_MAX_FILE_BYTES = 2_000_000

MAX_READ_PATHS = 30
# Quanto testo ``map`` legge in tutto, al massimo.
_MAP_BYTE_BUDGET = 8_000_000
_SNIPPET = 140


# ── Utilità ──────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    """Il testo di *path*, senza BOM e con i byte guasti sostituiti (come il lint)."""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _shown(path: Path) -> bool:
    """Il testo di *path* entra nella risposta: dentro un subagent è una lettura.

    ``False`` se il file è un segreto dell'installazione, che allora non si mostra.
    """
    try:
        check_subagent_read(path)
    except SubagentSecretError:
        return False
    return True


def _blank_code(text: str) -> str:
    """*text* con codice recintato e codice in linea sbiancati, **righe intatte**.

    Il lint toglie il codice prima di cercare i link (un esempio di sintassi non
    è un link); qui serve anche il numero di riga, quindi al posto del codice
    restano spazi e a capo.
    """
    def blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return _INLINE_CODE_RE.sub(blank, _CODE_FENCE_RE.sub(blank, text))


def _strip_inline_comment(value: str) -> str:
    """Il commento in coda a uno scalare (`` # …``), fuori dalle virgolette."""
    if value[:1] in ("'", '"'):
        return value
    cut = value.find(" #")
    return value[:cut].rstrip() if cut >= 0 else value


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, int]:
    """``(campi, riga d'inizio)`` del frontmatter di *text*, o ``(None, 0)``.

    Un parser minimo e deterministico come quello del lint (chiave: valore,
    liste in linea e a blocco), non YAML: le pagine ne usano solo questa parte,
    e due parser diversi darebbero due risposte a «questa pagina ha uno stato?».
    La riga d'inizio (1 = la riga del primo ``---``) serve ai numeri di riga.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, 0
    start = text[: match.start(1)].count("\n")  # righe prima del corpo
    lines = match.group(1).split("\n")
    out: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        val = _strip_inline_comment(rest.strip())
        key = key.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key] = [p.strip().strip("\"'") for p in inner.split(",")] if inner else []
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            out[key] = val[1:-1]
        elif val == "":
            block, j = [], i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    j += 1
                    continue
                if nxt[0] in (" ", "\t") and nxt.strip().startswith("- "):
                    block.append(_strip_inline_comment(nxt.strip()[2:].strip()).strip("\"'"))
                    j += 1
                else:
                    break
            if block:
                out[key] = block
                i = j
                continue
            out[key] = ""
        else:
            out[key] = val
        i += 1
    return out, start


def _values(value: Any) -> list[str]:
    """Un campo che può essere una stringa o una lista: le voci non vuote."""
    items = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in items if str(v or "").strip()]


def _key_line(text: str, start: int, key: str, needle: str = "") -> int:
    """La riga (da 1) in cui il frontmatter nomina *needle*, o la riga di *key*."""
    lines = text.split("\n")
    key_line = 0
    for idx in range(start, min(len(lines), start + 200)):
        line = lines[idx]
        if line.strip() == "---" and idx > start:
            break
        if line.lstrip().startswith(f"{key}:") and not key_line:
            key_line = idx + 1
            if needle and needle in line:
                return idx + 1
        elif key_line and needle and needle in line:
            return idx + 1
    return key_line or start + 1


def is_research_layout(wiki_dir: Path) -> bool:
    """Una biblioteca di ricerca, letta dalle **pagine** (la regola del lint)."""
    return any(
        (wiki_dir / name).is_dir() and any(p.is_file() for p in (wiki_dir / name).rglob("*.md"))
        for name in RESEARCH_SUBDIRS
    )


def _wiki_md_files(wiki_dir: Path) -> list[Path]:
    """Ogni ``.md`` sotto ``wiki/``, nascosti esclusi: quelli dove un link può stare."""
    if not wiki_dir.is_dir():
        return []
    return sorted(
        p for p in wiki_dir.rglob("*.md")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(wiki_dir).parts)
    )


class _Links:
    """La risoluzione dei wikilink dell'app, con una cache per bersaglio.

    ``resolve_wikilink`` fa una camminata di ``wiki/`` per ogni chiamata: su un
    quaderno da cento pagine e trecento link sarebbero trecento camminate.
    Ogni bersaglio distinto se ne prende una sola.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, Path | None] = {}

    def resolve(self, target: str) -> Path | None:
        target = target.strip()
        if target not in self._cache:
            from jenny.webui.wiki import resolve_wikilink

            self._cache[target] = resolve_wikilink(self._root, target) if target else None
        return self._cache[target]


def wikilinks(text: str) -> list[tuple[int, str]]:
    """``(riga, bersaglio)`` dei wikilink di *text*, codice escluso."""
    out = []
    for lineno, line in enumerate(_blank_code(text.replace("\\|", "|")).split("\n"), 1):
        out.extend((lineno, m.group(1).strip()) for m in _WIKILINK_RE.finditer(line))
    return out


# ── check ────────────────────────────────────────────────────────────────────

# Le categorie, nell'ordine in cui si elencano, con il nome per i conteggi.
CATEGORIES: dict[str, str] = {
    "dead_link": "dead wikilinks",
    "missing_source": "sources: not in raw/",
    "no_sources": "concept/entity pages without sources:",
    "no_summary": "raw sources without a summary",
    "dangling_source": "source: not found",
    "no_frontmatter": "pages without frontmatter",
    "no_state": "pages without a valid state",
    "over_ceiling": "pages over the ceiling",
    "thin": "pages under min_chars",
    "map_over": "map over its ceiling",
    "card_missing_page": "card pages that do not exist",
    "drawer_missing_source": "drawer sections citing missing pages",
    "reminder_dead_why": "open reminders whose why is missing",
    "expect_missing": "expected files missing",
    "expect_extra": "files not in expect",
}


@dataclass(frozen=True)
class Defect:
    """Un difetto: categoria, percorso relativo al quaderno, riga (0 = nessuna)."""

    kind: str
    path: str
    line: int
    detail: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"- {where} — {self.detail}"


@dataclass
class CheckReport:
    pages: int = 0
    research: bool = False
    cards: int = 0
    open_reminders: int = 0
    defects: list[Defect] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.defects:
            out[d.kind] = out.get(d.kind, 0) + 1
        return out


def check_notebook(root: Path, *, expect: Mapping[str, Any] | None = None,
                   min_chars: int = 0) -> CheckReport:
    """I difetti di un quaderno, senza scrivere niente. Deterministico."""
    from jenny.agent.gardener import MAP_TARGET_CHARS, page_ceiling

    wiki_dir = root / "wiki"
    report = CheckReport()
    found = report.defects
    md_files = _wiki_md_files(wiki_dir)
    injected = set(iter_wiki_pages(wiki_dir, titles=False))
    report.pages = len(injected)
    report.research = is_research_layout(wiki_dir)
    links = _Links(root)
    raw_dir = root / "raw"
    raw_stems = {f.stem for f in raw_dir.rglob("*") if f.is_file()} if raw_dir.is_dir() else set()
    ceiling = page_ceiling()

    for path in md_files:
        rel_wiki = path.relative_to(wiki_dir).as_posix()
        rel = _rel(root, path)
        try:
            size_bytes = path.stat().st_size
            # Un «.md» enorme non si carica per controllarlo: basta dire che
            # sfonda il tetto (un byte vale almeno un carattere).
            if size_bytes > _MAX_FILE_BYTES:
                found.append(Defect("over_ceiling", rel, 0,
                                    f"{size_bytes} bytes, far over the page ceiling of "
                                    f"{ceiling}: too large to check — split it"))
                continue
            text = _read(path)
        except OSError:
            continue
        shown = False
        for lineno, target in wikilinks(text):
            if links.resolve(target) is None:
                found.append(Defect("dead_link", rel, lineno, f"dead wikilink [[{target}]]"))
                shown = True
        fm, start = parse_frontmatter(text)
        if fm is not None:
            if isinstance(fm.get("sources"), (list, str)):
                for src in _values(fm.get("sources")):
                    if "<" in src and ">" in src:
                        continue
                    if Path(src).stem not in raw_stems:
                        found.append(Defect("missing_source", rel,
                                            _key_line(text, start, "sources", src),
                                            f"sources: {src} — no such file in raw/"))
                        shown = True
            for src in _values(fm.get("source")):
                if "://" in src or ("<" in src and ">" in src):
                    continue
                file_part = src.partition("#")[0].strip()
                if file_part and not ((root / file_part).exists()
                                      or (wiki_dir / file_part).exists()):
                    found.append(Defect("dangling_source", rel,
                                        _key_line(text, start, "source", src),
                                        f"source: {src} — no such file"))
                    shown = True
        # Il passo 9a del lint: una pagina di concetto o di entità senza fonti.
        first = Path(rel_wiki).parts[0] if Path(rel_wiki).parts else ""
        if first in ("concepts", "entities") and path.stem != "index" \
                and not _values((fm or {}).get("sources")):
            found.append(Defect("no_sources", rel, 1, "no sources: — a concept or entity page "
                                "names the raw/ files it comes from"))
        if rel_wiki not in injected:
            if shown:
                _shown(path)
            continue
        # Ogni ``index.md`` è una mappa, non una pagina: niente stato da chiedere
        # (la regola del lint). La misura invece vale anche per l'hub di una
        # cartella, che entra nel prompt come le altre pagine.
        if fm is None and path.stem != "index":
            found.append(Defect("no_frontmatter", rel, 1, "no frontmatter"
                                + ("" if report.research else " (so no state:)")))
        elif fm is not None and not report.research and path.stem != "index":
            state = str(fm.get("state", "")).strip()
            if state.lower() not in PAGE_STATES:
                found.append(Defect("no_state", rel, _key_line(text, start, "state"),
                                    f"state: {state or '(missing)'} — one of "
                                    f"{', '.join(sorted(PAGE_STATES))}"))
                shown = True  # il valore citato è testo della pagina
        size = page_chars(text)
        if size > ceiling:
            found.append(Defect("over_ceiling", rel, 0,
                                f"{size} characters, over the page ceiling of {ceiling}: "
                                "it is not injected at all — split it"))
        elif min_chars and size < min_chars:
            found.append(Defect("thin", rel, 0, f"only {size} characters (min_chars {min_chars})"))
        if shown:
            _shown(path)

    if report.research and raw_dir.is_dir():
        _check_summaries(root, report)

    index = wiki_dir / WIKI_INDEX_FILENAME
    if index.is_file():
        try:
            size = len(index.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            size = 0
        if size > MAP_TARGET_CHARS:
            found.append(Defect("map_over", _rel(root, index), 0,
                                f"the map is {size} characters, over {MAP_TARGET_CHARS}: "
                                "past that it is injected cut"))

    _check_board(root, report, injected)
    if expect:
        _check_expect(root, expect, report)
    found.sort(key=lambda d: (list(CATEGORIES).index(d.kind), d.path, d.line, d.detail))
    return report


def _check_summaries(root: Path, report: CheckReport) -> None:
    """Il passo 11 del lint, solo nel layout di ricerca: ogni fonte testuale in
    ``raw/articles``, ``raw/papers``, ``raw/notes`` ha il suo ``wiki/summaries/``.

    In un quaderno piatto ``raw/notes`` sono appunti dell'utente, non documenti
    da riassumere: lì il lint grida al lupo, e qui non si ripete.
    """
    summaries = root / "wiki" / "summaries"
    stems = {p.stem for p in summaries.rglob("*.md")} if summaries.is_dir() else set()
    for folder in ("articles", "papers", "notes"):
        base = root / "raw" / folder
        if not base.is_dir():
            continue
        for src in sorted(base.rglob("*.md")):
            if src.is_file() and src.stem not in stems:
                report.defects.append(Defect("no_summary", _rel(root, src), 0,
                                             f"no wiki/summaries/{src.stem}.md"))


def _check_board(root: Path, report: CheckReport, pages: set[str]) -> None:
    """Schede con pagine che non ci sono, cassetti che citano pagine che non ci
    sono, promemoria aperti il cui ``why`` non c'è più.

    **Solo gli aperti**, come :func:`jenny.board.edit.clear_reminder_whys` e
    ``update_reminder``: un promemoria chiuso (``done``/``dropped``) è storia,
    nessuno lo legge più per agire, e nessuno strumento lo cambia. Nelle prove
    del 24-25/10/2026 i ``why`` morti dei chiusi chiudevano ogni piano con «6
    why che lo strumento non lascia toccare», e un aiutante ha scritto un file in
    ``outputs/`` per aggirarlo: qui si contano in una nota, come non-difetto.
    """
    from jenny.board import model
    from jenny.board.store import read_board, read_card_summary, read_reminders
    from jenny.board.view import missing_pages

    board = read_board(root)
    reminders = read_reminders(root)
    if board is not None:
        report.cards = len(board["cards"])
        titles = dict.fromkeys(pages, "")
        gone_pages = missing_pages(board, titles)
        if gone_pages:
            _shown(root / "board" / "board.json")
        for cid, gone in gone_pages.items():
            for page in gone:
                report.defects.append(Defect("card_missing_page", "board/board.json", 0,
                                             f"card `{cid}` lists {page}, which does not exist"))
        for card in board["cards"]:
            summary = read_card_summary(root, card["id"])
            for section in (summary or {}).get("sections") or []:
                gone = [s for s in section.get("sources") or []
                        if isinstance(s, str) and s not in pages]
                if gone:
                    _shown(root / "board" / "cards" / f"{card['id']}.json")
                    report.defects.append(Defect(
                        "drawer_missing_source", f"board/cards/{card['id']}.json", 0,
                        f"drawer `{card['id']}`, section «{section.get('title')}» cites "
                        f"missing {', '.join(gone)}"))
    open_rems = model.open_reminders(reminders)
    report.open_reminders = len(open_rems)
    closed_dead = 0
    for rem in reminders["reminders"]:
        # Il ``why`` si legge da ``wiki/`` (``board/clock.py``): conta il file.
        why = str(rem.get("why") or "").strip()
        if not why or (root / "wiki" / why.removeprefix("wiki/")).is_file():
            continue
        if rem["state"] != "open":
            closed_dead += 1
            continue
        _shown(root / "board" / "reminders.json")
        report.defects.append(Defect("reminder_dead_why", "board/reminders.json", 0,
                                     f"open reminder `{rem['id']}`: why {why} does not exist"))
    if closed_dead:
        report.notes.append(f"{closed_dead} closed reminder(s) have a why that no longer exists: "
                            "closed reminders are history, not a defect — leave them as they are.")


def _safe_pattern(pattern: str) -> str | None:
    pattern = pattern.strip().strip("/")
    if not pattern or pattern.startswith(("/", "~")) or ".." in Path(pattern).parts:
        return None
    return pattern


def _expected_name(name: str) -> str:
    name = name.strip().strip("/")
    return name if Path(name).suffix else f"{name}.md"


def _check_expect(root: Path, expect: Mapping[str, Any], report: CheckReport) -> None:
    """La struttura attesa: ``{"folders": "wiki/concepts/*", "files": ["index", …]}``.

    Con ``folders`` ogni cartella che il glob trova deve avere esattamente quei
    file (i ``.md`` in più si elencano); senza, ``files`` sono percorsi dalla
    radice del quaderno e si cercano i mancanti.
    """
    files = [_expected_name(f) for f in _values(expect.get("files")) if _expected_name(f)]
    patterns = [p for p in _values(expect.get("folders")) if p]
    if not patterns:
        for name in files:
            if _safe_pattern(name) is None or not (root / name).is_file():
                report.defects.append(Defect("expect_missing", name, 0, "missing (in expect)"))
        return
    for raw in patterns:
        pattern = _safe_pattern(raw)
        if pattern is None:
            report.notes.append(f"expect: «{raw}» is not a pattern inside the notebook")
            continue
        folders = sorted(p for p in root.glob(pattern)
                         if p.is_dir() and is_path_within(p, root)
                         and not any(part.startswith(".") for part in p.relative_to(root).parts))
        if not folders:
            report.notes.append(f"expect: no folder matches {pattern}")
            continue
        report.notes.append(f"expect: {len(folders)} folder(s) match {pattern}")
        wanted = set(files)
        for folder in folders:
            for name in files:
                if not (folder / name).is_file():
                    report.defects.append(Defect("expect_missing", _rel(root, folder / name), 0,
                                                 "missing (in expect)"))
            if not files:
                continue
            for extra in sorted(folder.glob("*.md")):
                if extra.name not in wanted:
                    report.defects.append(Defect("expect_extra", _rel(root, extra), 0,
                                                 "not in expect.files"))


# Quel che il lint della skill controlla e ``check`` no: detto in ogni risposta,
# perché un «No defects found» non passi per una wiki sana in tutto.
NOT_CHECKED = ("Not checked here (the llm-wiki lint does): orphans and pages missing from the "
               "map, duplicates, cross-links, log/ and audit/ shape, the journal, provenance of "
               "decided pages, stale drawers.")


def render_check(report: CheckReport, *, limit: int, max_chars: int) -> str:
    layout = "research" if report.research else "notebook"
    head = [f"Notebook check: {report.pages} pages (layout: {layout}), "
            f"{report.cards} cards, {report.open_reminders} open reminders."]
    counts = report.counts()
    if counts:
        head.append(f"Defects: {len(report.defects)} — " + ", ".join(
            f"{CATEGORIES[k]} {counts[k]}" for k in CATEGORIES if k in counts))
    else:
        head.append("No defects found.")
    head.extend(report.notes)
    out = "\n".join(head)
    shown = 0
    for defect in report.defects[:limit]:
        line = "\n" + defect.render()
        if len(out) + len(line) > max_chars - 120 - len(NOT_CHECKED):
            break
        out += line
        shown += 1
    rest = len(report.defects) - shown
    if rest:
        out += f"\n(+{rest} more: raise limit, or fix these and check again)"
    return out + "\n" + NOT_CHECKED


# ── map ──────────────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    """Minuscolo e senza accenti: «Città» e «citta» sono la stessa parola qui."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def _terms_regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    folded = sorted({_fold(t).strip() for t in terms if _fold(str(t)).strip()}, key=len,
                    reverse=True)
    if not folded:
        return None
    alt = "|".join(re.escape(t) for t in folded)
    return re.compile(rf"(?<![a-z0-9])(?:{alt})(?![a-z0-9])")


@dataclass(frozen=True)
class Term:
    name: str
    regex: re.Pattern[str]


def parse_terms(items: Any, words_key: str) -> list[Term]:
    """``[{name, aliases|keywords}]`` → termini cercabili; le voci vuote cadono."""
    out: list[Term] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        words = [name, *_values(item.get(words_key))]
        regex = _terms_regex(words)
        if name and regex is not None:
            out.append(Term(name, regex))
    return out


def _text_files(root: Path, paths: Sequence[str]) -> tuple[list[Path], list[str]]:
    """I file di testo sotto *paths* (relativi al quaderno), nascosti esclusi.

    Nell'ordine di *paths* (``wiki`` prima di ``raw`` di default): se il tetto di
    ``map`` taglia, taglia le fonti, non le pagine.
    """
    files: dict[Path, None] = {}
    notes: list[str] = []
    for raw in paths:
        pattern = _safe_pattern(raw)
        base = (root / pattern).resolve() if pattern else None
        if base is None or not is_path_within(base, root.resolve()):
            notes.append(f"{raw}: not a folder inside the notebook")
            continue
        if base.is_file():
            candidates: Iterable[Path] = [base]
        elif base.is_dir():
            candidates = sorted(base.rglob("*"))
        else:
            notes.append(f"{raw}: not found")
            continue
        for path in candidates:
            try:
                rel_parts = path.resolve().relative_to(root.resolve()).parts
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel_parts):
                continue
            if path.suffix.lower() in _TEXT_SUFFIXES and path.is_file():
                try:
                    if path.stat().st_size <= _MAX_FILE_BYTES:
                        files.setdefault(path)
                except OSError:
                    continue
    return list(files), notes


def _snippet(line: str, folded: str, regex: re.Pattern[str]) -> str:
    line = " ".join(line.split())
    if len(line) <= _SNIPPET:
        return line
    match = regex.search(folded)
    centre = match.start() if match else 0
    start = max(0, min(centre - _SNIPPET // 3, len(line) - _SNIPPET))
    cut = line[start:start + _SNIPPET]
    return ("…" if start else "") + cut + ("…" if start + _SNIPPET < len(line) else "")


def map_notebook(root: Path, subjects: list[Term], topics: list[Term],
                 paths: Sequence[str], *, per_cell: int, max_chars: int) -> str:
    """La matrice soggetto × argomento: quanti file nominano entrambi, e dove."""
    root = root.resolve()
    files, notes = _text_files(root, paths)
    cells: dict[tuple[int, int], list[tuple[str, int, str, Path]]] = {}
    subj_files = [0] * len(subjects)
    topic_files = [0] * len(topics)
    searched, spent, skipped_files = 0, 0, 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        # Un tetto in byte per tutta la mappa: sul telefono 50 MB di fonti sono
        # minuti di CPU, e la risposta arriverebbe dopo che il turno è scaduto.
        if spent + size > _MAP_BYTE_BUDGET:
            skipped_files += 1
            continue
        try:
            text = _read(path)
        except OSError:
            continue
        spent += size
        searched += 1
        folded_text = _fold(text)
        stem = _fold(path.stem.replace("-", " ").replace("_", " "))
        # Prima il file intero, una ricerca per termine: le righe si scorrono
        # solo per i soggetti e gli argomenti che il file nomina davvero.
        topic_in = [t.regex.search(folded_text) is not None for t in topics]
        subj_name = [s.regex.search(stem) is not None for s in subjects]
        subj_in = [subj_name[i] or s.regex.search(folded_text) is not None
                   for i, s in enumerate(subjects)]
        for ti, present in enumerate(topic_in):
            topic_files[ti] += present
        if not any(topic_in) and not any(subj_in):
            continue
        lines = text.split("\n")
        folded = folded_text.split("\n")
        if len(folded) != len(lines):  # una piegatura che cambia le righe: riga per riga
            folded = [_fold(line) for line in lines]
        rel = _rel(root, path)
        topic_hits = [[i for i, f in enumerate(folded) if t.regex.search(f)] if topic_in[ti]
                      else [] for ti, t in enumerate(topics)]
        for si, subj in enumerate(subjects):
            if not subj_in[si]:
                continue
            in_name = subj_name[si]
            subj_hits = ({i for i, f in enumerate(folded) if subj.regex.search(f)}
                         if any(topic_in) else set())
            subj_files[si] += 1
            for ti, hits in enumerate(topic_hits):
                if not hits:
                    continue
                # La riga che lo mostra meglio: un argomento vicino al soggetto
                # (due righe), altrimenti il primo dove compare.
                near = [i for i in hits
                        if in_name or any(j in subj_hits for j in range(i - 2, i + 3))]
                line = (near or hits)[0]
                cells.setdefault((si, ti), []).append(
                    (rel, line + 1, _snippet(lines[line], folded[line], topics[ti].regex), path))

    where = ", ".join(paths)
    if not searched and not skipped_files:
        return f"Error: no text file under {where} in this notebook."
    if skipped_files:
        notes.append(f"Not searched: {skipped_files} files, past {_MAP_BYTE_BUDGET // 1_000_000} MB "
                     "in all — map fewer folders (paths) to reach them.")
    out = [f"Map of {searched} text files under {where}: files that mention both the subject "
           "(name, alias, or file name) and the topic.", *notes, ""]
    header = "| subject (files) | " + " | ".join(
        f"{t.name} ({topic_files[i]})" for i, t in enumerate(topics)) + " |"
    out.append(header)
    out.append("|---" * (len(topics) + 1) + "|")
    empty: list[str] = []
    for si, subj in enumerate(subjects):
        row = [str(len(cells.get((si, ti), []))) for ti in range(len(topics))]
        out.append(f"| {subj.name} ({subj_files[si]}) | " + " | ".join(row) + " |")
        empty.extend(f"{subj.name} × {topics[ti].name}" for ti in range(len(topics))
                     if (si, ti) not in cells)
    if empty:
        out.append("")
        out.append(f"Empty cells ({len(empty)}): " + "; ".join(empty))
    text = "\n".join(out)
    if len(text) > max_chars:
        return text[: max_chars - 80] + "\n(cut: ask fewer subjects or topics at once)"
    skipped = 0
    for (si, ti), hits in sorted(cells.items()):
        block = [f"\n{subjects[si].name} × {topics[ti].name} ({len(hits)}):"]
        for rel, lineno, snip, path in hits[:per_cell]:
            if not _shown(path):
                continue
            block.append(f"- {rel}:{lineno} — {snip}")
        if len(hits) > per_cell:
            block.append(f"- … {len(hits) - per_cell} more")
        piece = "\n".join(block)
        if len(text) + len(piece) > max_chars - 100:
            skipped += 1
            continue
        text += piece
    if skipped:
        text += f"\n({skipped} cells not detailed for space: map them on their own)"
    return text


# ── read_many ────────────────────────────────────────────────────────────────


@dataclass
class ReadResult:
    text: str
    read: list[tuple[Path, int | None]]  # (file, righe lette: None = tutto)


def resolve_inside(root: Path, raw: str) -> Path | None:
    """*raw* come file dentro il quaderno — anche relativo a ``wiki/``, come lo
    scrive la bacheca — o ``None`` se esce dal quaderno o non c'è."""
    rel = _safe_pattern(raw)
    if rel is None:
        return None
    base = root.resolve()
    for candidate in (root / rel, root / "wiki" / rel):
        try:
            resolved = candidate.resolve()
            if is_path_within(resolved, base) and resolved.is_file():
                return resolved
        except OSError:  # un nome troppo lungo, un ciclo di link: non è un file
            continue
    return None


def _extract(text: str, mode: str, lines: int) -> tuple[str, int | None]:
    if mode == "frontmatter":
        match = _FRONTMATTER_RE.match(text)
        if match:
            return text[: match.end()].rstrip("\n"), text[: match.end()].count("\n") + 1
        heading = next((ln for ln in text.split("\n") if ln.startswith("# ")), "")
        return "(no frontmatter)" + (f"\n{heading}" if heading else ""), 0
    if mode == "head":
        part = text.split("\n")[:lines]
        return "\n".join(part), len(part)
    return text, None


def _short(raw: str, limit: int = 80) -> str:
    """Un percorso ripetuto nella risposta, accorciato: è testo del modello, non del file."""
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


def _read_capped(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Al più *max_bytes* di *path*, decodificati; ``True`` se il file continua.

    Un PDF da 30 MB letto per intero ha portato il processo a 257 MB (revisione
    del 24/10/2026): qui si legge solo quel che potrebbe entrare nella risposta.
    """
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    more = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace").removeprefix("\ufeff")
    return text, more


def read_many(root: Path, paths: Sequence[str], *, mode: str, lines: int,
              max_chars: int | None, total_chars: int) -> ReadResult:
    """Più file in una volta: il tetto totale si divide fra i file.

    Un file più corto della sua parte lascia il resto agli altri; chi resta
    tagliato lo dice, con la riga da cui riprendere con ``read_file``. La
    risposta non supera mai *total_chars*: oltre, finirebbe scaricata in un file,
    e rileggerla contaminerebbe l'aiutante.
    """
    root = root.resolve()
    seen: list[tuple[str, Path]] = []
    notes: list[str] = []
    for raw in paths:
        resolved = resolve_inside(root, raw)
        if resolved is None:
            notes.append(f"{_short(raw)}: not a file inside this notebook")
        elif all(resolved != p for _, p in seen):
            seen.append((_rel(root, resolved), resolved))
    # Un carattere pesa al più 4 byte: oltre questo il file non entrerebbe comunque.
    max_bytes = max(total_chars, max_chars or 0) * 4 + 4096
    bodies: list[tuple[str, Path, str, int | None, str]] = []
    for rel, path in seen:
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            notes.append(f"{rel}: not a text file")
            continue
        try:
            text, more = _read_capped(path, max_bytes)
        except OSError as exc:
            notes.append(f"{rel}: unreadable ({exc.strerror or exc})")
            continue
        if not _shown(path):
            notes.append(f"{rel}: not readable from here")
            continue
        body, count = _extract(text, mode, lines)
        size = f"{text.count(chr(10)) + 1}+ lines, read in part" if more \
            else f"{text.count(chr(10)) + 1} lines"
        if more and count is None:  # «full» di un file letto in parte: non è tutto
            count = text.count("\n") + 1
        bodies.append((rel, path, body, count, size))
    # Quel che non è corpo: intestazioni, note, la riga dei tagli (una voce per
    # file al più), i separatori.
    overhead = (sum(len(rel) * 2 + 80 for rel, *_ in bodies)
                + sum(len(n) + 1 for n in notes) + 120)
    budget = max(0, total_chars - overhead)
    caps = {rel: min(len(body), max_chars or len(body)) for rel, _p, body, _c, _s in bodies}
    remaining, pending = budget, sorted(caps, key=lambda r: caps[r])
    share: dict[str, int] = {}
    for i, rel in enumerate(pending):
        fair = remaining // max(1, len(pending) - i)
        share[rel] = min(caps[rel], fair)
        remaining -= share[rel]
    out: list[str] = []
    read: list[tuple[Path, int | None]] = []
    cut: list[str] = []
    for rel, path, body, count, size in bodies:
        allowed = share.get(rel, 0)
        shown_body = body
        if len(body) > allowed:
            shown_body = body[:allowed]
            last_line = shown_body.count("\n") + 1
            cut.append(f"{rel} (from line {last_line}: read_file offset={last_line})")
        out.append(f"=== {rel} ({size}) ===\n{shown_body}")
        if shown_body:
            read.append((path, count if len(body) <= allowed else shown_body.count("\n") + 1))
    text = "\n\n".join(out)
    tail = notes + ([f"Truncated ({len(cut)}): " + "; ".join(cut)] if cut else [])
    if tail:
        text += "\n\n" + "\n".join(tail)
    if not read:
        text = "Error: nothing was read.\n" + text
    if len(text) > total_chars:
        text = text[: total_chars - 40] + "\n(cut at the size limit)"
    return ReadResult(text=text, read=read)


# ── remove_pages: quel che serve a dirlo (le scritture stanno nel tool) ──────


def page_trace(root: Path, path: Path, *, head_lines: int = 8) -> str:
    """La traccia di una pagina che sta per sparire: percorso e prime righe."""
    try:
        head = "\n".join(_read(path).split("\n")[:head_lines]).rstrip()
    except OSError:
        head = "(unreadable)"
    return f"### {_rel(root, path)}\n\n````markdown\n{head}\n````\n"


def links_into(root: Path, targets: set[Path], *, skip: set[Path]) -> list[str]:
    """``percorso:riga`` dei wikilink che oggi aprono una di *targets*.

    Va chiamata **prima** di cancellare: dopo, gli stessi link non risolvono più
    e non si sa più dove puntavano.
    """
    links = _Links(root)
    wanted = {p.resolve() for p in targets}
    out = []
    for path in _wiki_md_files(root / "wiki"):
        if path.resolve() in skip:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = _read(path)
        except OSError:
            continue
        for lineno, target in wikilinks(text):
            hit = links.resolve(target)
            if hit is not None and hit.resolve() in wanted:
                out.append(f"{_rel(root, path)}:{lineno} [[{target}]]")
    return out
