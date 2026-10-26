"""Il controllo a macchina di fine piano: cosa c'è da riparare nel quaderno.

Nelle prove del 24-25/10/2026 il passo di controllo lo faceva il modello:
rileggeva, contava, cercava. Nella riprova A ne ha scritti due, 76 minuti in
tutto, e ha lasciato nelle pagine dieci «query da fare» che nessuno ha cercato,
e dieci sezioni Fonti su sedici senza un link. Il codice queste cose le vede da
solo, in un attimo e sempre allo stesso modo: qui le vede, e al modello resta
solo la lista da riparare (``jenny/plan/driver.py``).

Riusa :func:`jenny.board.notebook_ops.check_notebook` (link, fonti, bacheca,
tetti, ``expect``) e ci aggiunge tre controlli tarati sulle wiki vere delle
prove:

- **buchi non cercati**: una riga che dice di cercare dopo («query da fare»,
  «restano da cercare», «queries to run»). «da cercare» da solo no: nella
  riprova A compare anche in «il parassita da cercare per primo». «TODO» no:
  nelle pagine delle piante «todo» è una parola del testo;
- **Fonti senza link**: una sezione ``## Fonti``/``## Sources`` senza un URL;
- **pagine vuote**: meno di :data:`EMPTY_BODY_CHARS` caratteri di testo, titoli e
  frontmatter esclusi. Nelle wiki delle prove la pagina più corta ne ha 683.

**Solo i difetti nuovi.** All'Approva si prende la *base*: le chiavi dei difetti
che il quaderno aveva già (:func:`baseline_keys`). Quelli non sono lavoro del
piano: si contano in una nota, non si chiedono. In C1 e C2 erano tutti di prima
(sei pagine senza fonti, la mappa oltre il tetto).

Modulo che legge e basta, tranne :func:`remove_empty_folders`, che il driver
chiama alla fine: una cartella vuota sotto ``wiki/`` si toglie senza chiedere a
nessuno.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jenny.board.notebook_ops import CATEGORIES, check_notebook, parse_frontmatter
from jenny.security.workspace_policy import is_path_within

#: Sotto questi caratteri di testo una pagina è vuota.
EMPTY_BODY_CHARS = 200
#: Le chiavi della base al massimo: oltre, un quaderno guasto in partenza.
MAX_BASELINE = 3000
#: Le voci al massimo nella lista per il modello; il totale si dice a parte.
MAX_LISTED = 60

#: Le categorie di ``check_notebook`` che non si chiedono di riparare: un
#: riassunto mancante di una fonte non è lavoro che il piano ha chiesto.
_NOT_REPAIRED = frozenset({"no_summary"})

_GAP = re.compile(
    r"\bquer(?:y|ies)\s+(?:da\s+fare|to\s+run|to\s+try|still\s+to)"
    r"|\b(?:resta|restano)\s+da\s+cercare"
    r"|\bda\s+cercare\s*[:(]"
    r"|\bto\s+be\s+searched\b"
    r"|\bstill\s+to\s+(?:be\s+)?search",
    re.IGNORECASE,
)
_SOURCES_HEAD = re.compile(r"^(#{1,4})\s*(?:fonti|sources|riferimenti|references)\b",
                           re.IGNORECASE | re.MULTILINE)
_ANY_HEAD = re.compile(r"^(#{1,6})\s", re.MULTILINE)
_WIKILINK = re.compile(r"\[\[[^\]]+\]\]")
_URL = re.compile(r"https?://", re.IGNORECASE)
_HEADING_LINE = re.compile(r"^#.*$", re.MULTILINE)
_DIGITS = re.compile(r"\d+")

#: Le categorie nuove, coi nomi per i conteggi.
AUDIT_CATEGORIES: dict[str, str] = {
    "unsearched_gap": "lines that say to search later",
    "sources_no_link": "sources sections without links",
    "empty_page": "empty pages",
}

#: Cosa fare, per categoria: lo legge il modello sotto la lista.
REPAIR_HINTS: dict[str, str] = {
    "dead_link": "point the link to the page that exists, or remove it",
    "missing_source": "fix `sources:` to name a file that is in raw/, or remove the entry",
    "dangling_source": "fix `source:` to a file that exists",
    "no_sources": "add `sources:` with the raw/ files the page comes from",
    "no_frontmatter": "add the frontmatter",
    "no_state": "set a valid `state:`",
    "over_ceiling": "split the page",
    "thin": "fill the page",
    "map_over": "shorten the map",
    "card_missing_page": "update the card's pages (board tool)",
    "drawer_missing_source": "update the drawer section's sources (board tool)",
    "reminder_dead_why": "point the reminder's why to the page that exists now (board tool)",
    "expect_missing": "write the missing page",
    "expect_extra": "move it into the expected pages, or remove it if its content is "
                    "already there",
    "unsearched_gap": "write \"not documented\" with the queries tried (the plan's research "
                      "is spent: this step starts none) — never leave \"to search\" in a page",
    "sources_no_link": "add the URL of each source, from the header of the raw/ file it cites",
    "empty_page": "write the page, or remove it if it has no reason to exist",
}


@dataclass(frozen=True)
class Finding:
    """Un difetto da riparare: categoria, pagina, riga (0 = nessuna), dettaglio."""

    kind: str
    path: str
    line: int
    detail: str

    @property
    def key(self) -> str:
        """La chiave per la base: senza la riga, che si sposta a ogni modifica sopra, e
        senza i numeri del dettaglio: una mappa già oltre il tetto che cresce di dieci
        caratteri è lo stesso difetto di prima."""
        raw = f"{self.kind}|{self.path}|{_DIGITS.sub('#', self.detail)}"
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]

    def render(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return f"{where}{self.detail}"


@dataclass
class Audit:
    """L'esito: i difetti da riparare, quelli di prima, i conteggi per la nota."""

    findings: list[Finding] = field(default_factory=list)
    preexisting: int = 0
    pages: int = 0
    cards: int = 0
    open_reminders: int = 0
    not_documented: int = 0
    no_summary: int = 0
    removed_dirs: int = 0
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out


def _labels() -> dict[str, str]:
    return {**CATEGORIES, **AUDIT_CATEGORIES}


def _wiki_pages(root: Path) -> list[tuple[str, str]]:
    """``(percorso dal quaderno, testo)`` di ogni ``.md`` sotto ``wiki/``, nascosti esclusi."""
    wiki = root / "wiki"
    if not wiki.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(wiki.rglob("*.md")):
        rel = path.relative_to(wiki)
        if not path.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        try:
            out.append((f"wiki/{rel.as_posix()}", path.read_text(encoding="utf-8",
                                                                  errors="replace")))
        except OSError:
            continue
    return out


def wiki_texts(root: Path) -> dict[str, str]:
    """``{percorso dal quaderno: testo}`` delle pagine di adesso, come ``PlanPhotos.texts``."""
    return dict(_wiki_pages(root))


def _body_chars(text: str) -> int:
    fm, start = parse_frontmatter(text)
    body = text[start:] if fm is not None else text
    return len(_HEADING_LINE.sub("", body).strip())


def _short(line: str, limit: int = 160) -> str:
    line = " ".join(line.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _page_findings(rel: str, text: str) -> list[Finding]:
    found: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _GAP.search(line)
        if match:
            start = max(0, match.start() - 80)
            found.append(Finding("unsearched_gap", rel, lineno,
                                 f"left to search: «{_short(line[start:], 200)}»"))
    head = _SOURCES_HEAD.search(text)
    if head is not None:
        # La sezione finisce al titolo dello stesso livello o più alto: i suoi
        # sotto-titoli («### Istituzionali») ne fanno parte.
        rest = text[head.end():]
        level = len(head.group(1))
        nxt = next((m for m in _ANY_HEAD.finditer(rest) if len(m.group(1)) <= level), None)
        section = rest[: nxt.start()] if nxt else rest
        if not _URL.search(section):
            found.append(Finding("sources_no_link", rel, text.count("\n", 0, head.start()) + 1,
                                 "the sources section has no links"))
    # Una pagina corta fatta di link è un indice, anche se non si chiama index.md.
    if (not rel.endswith("/index.md") and _body_chars(text) < EMPTY_BODY_CHARS
            and not _WIKILINK.search(text)):
        found.append(Finding("empty_page", rel, 0,
                             f"almost empty ({_body_chars(text)} characters of text)"))
    return found


def _all_findings(root: Path, expect: Mapping[str, Any] | None) -> tuple[list[Finding], Audit]:
    report = check_notebook(root, expect=expect)
    audit = Audit(pages=report.pages, cards=report.cards, open_reminders=report.open_reminders)
    audit.notes.extend(report.notes)
    found: list[Finding] = []
    for d in report.defects:
        if d.kind in _NOT_REPAIRED:
            audit.no_summary += 1
            continue
        found.append(Finding(d.kind, d.path, d.line, d.detail))
    nd = re.compile(r"non documentat|not documented", re.IGNORECASE)
    for rel, text in _wiki_pages(root):
        audit.not_documented += len(nd.findall(text))
        found.extend(_page_findings(rel, text))
    return found, audit


def baseline_keys(root: Path, expect: Mapping[str, Any] | None = None) -> list[str]:
    """Le chiavi dei difetti che il quaderno ha già: si prende all'Approva."""
    found, _ = _all_findings(root, expect)
    return sorted({f.key for f in found})[:MAX_BASELINE]


def audit_notebook(root: Path, *, expect: Mapping[str, Any] | None = None,
                   baseline: Iterable[str] = ()) -> Audit:
    """I difetti nuovi del quaderno rispetto a *baseline*. Deterministico, non scrive."""
    found, audit = _all_findings(root, expect)
    base = set(baseline)
    for f in found:
        if f.key in base:
            audit.preexisting += 1
        else:
            audit.findings.append(f)
    return audit


def empty_folders(root: Path) -> list[str]:
    """Le cartelle vuote sotto ``wiki/`` adesso, come ``wiki/concepts/Acero/``.

    All'Approva: quelle che c'erano prima del piano sono dell'utente (un posto
    tenuto per dopo), e :func:`remove_empty_folders` le lascia stare.
    """
    wiki = root / "wiki"
    if not wiki.is_dir():
        return []
    return sorted(
        f"wiki/{p.relative_to(wiki).as_posix()}/"
        for p in wiki.rglob("*")
        if p.is_dir() and not p.is_symlink() and not any(p.iterdir())
    )


def remove_empty_folders(root: Path, keep: Iterable[str] = ()) -> list[str]:
    """Toglie le cartelle vuote sotto ``wiki/`` (mai ``wiki/`` stessa), dalle più profonde.

    ``rmdir`` toglie solo una cartella vuota: un file arrivato nel frattempo la
    tiene. *keep* sono quelle vuote già all'Approva (:func:`empty_folders`): non
    sono lavoro del piano, e le foto del piano tengono i file, non le cartelle,
    quindi un ritorno indietro non le rimetterebbe. Restituisce i percorsi tolti,
    come ``wiki/concepts/Acero/``.
    """
    wiki = root / "wiki"
    if not wiki.is_dir():
        return []
    kept = set(keep)
    gone: list[str] = []
    folders = sorted((p for p in wiki.rglob("*") if p.is_dir() and not p.is_symlink()),
                     key=lambda p: len(p.parts), reverse=True)
    for folder in folders:
        if not is_path_within(folder, wiki) or folder == wiki:
            continue
        if f"wiki/{folder.relative_to(wiki).as_posix()}/" in kept:
            continue
        try:
            folder.rmdir()
        except OSError:
            continue
        gone.append(f"wiki/{folder.relative_to(wiki).as_posix()}/")
    return sorted(gone)


def render_repair(audit: Audit, *, limit: int = MAX_LISTED) -> list[dict[str, Any]]:
    """La lista per il modello, raggruppata per pagina: ``[{"path", "items": [...]}]``.

    Ogni voce ha il testo e cosa fare; al massimo *limit* voci in tutto.
    """
    groups: dict[str, list[str]] = {}
    shown = 0
    for f in audit.findings:
        if shown >= limit:
            break
        hint = REPAIR_HINTS.get(f.kind, "repair it")
        groups.setdefault(f.path, []).append(f"{f.render()} → {hint}")
        shown += 1
    return [{"path": path, "items": items} for path, items in groups.items()]


def summary_line(audit: Audit) -> str:
    """I conteggi in una riga, per la nota del passo (in inglese: la legge il modello)."""
    labels = _labels()
    counts = audit.counts()
    head = (f"Machine check: {audit.pages} pages, {audit.cards} cards, "
            f"{audit.open_reminders} open reminders")
    if counts:
        head += "; to repair: " + ", ".join(f"{labels.get(k, k)} {n}"
                                           for k, n in sorted(counts.items()))
    else:
        head += "; nothing to repair"
    extra = []
    if audit.preexisting:
        extra.append(f"{audit.preexisting} defect(s) were there before the plan")
    if audit.not_documented:
        extra.append(f"{audit.not_documented} \"not documented\"")
    if audit.no_summary:
        extra.append(f"{audit.no_summary} raw source(s) without a summary")
    return head + (" (" + "; ".join(extra) + ")" if extra else "") + "."
