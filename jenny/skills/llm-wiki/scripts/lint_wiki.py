#!/usr/bin/env python3
"""
lint_wiki.py — Health check for an LLM Wiki.

Usage:
    python3 lint_wiki.py <wiki-root>
    python3 lint_wiki.py --workspace <wikis-dir> [--fix]

Examples:
    python3 lint_wiki.py workspace/wikis/ai-research
    python3 lint_wiki.py --workspace workspace/wikis
    python3 lint_wiki.py --workspace workspace/wikis --fix   # repair registry drift

Per-wiki checks:
  0. Encoding — every page under wiki/ must be UTF-8. Printed first, because the
     checks below read a page that is not with replacement characters instead of
     dying on it, and because the injector reads pages as strict UTF-8: a page
     that is not UTF-8 is skipped whole, every turn, and no other check can see
     that (the page exists, is linked, and declares its state).
  1. Dead wikilinks — [[Target]] where Target.md doesn't exist. Wikis are
     isolated, so a link to another wiki's page is (correctly) a dead link.
  2. Orphan pages — wiki pages with no inbound links
  3. Missing index entries — wiki pages not listed in wiki/index.md
  4. Unlinked concepts — terms mentioned 3+ times but lacking their own page
  5. log/ shape — every file matches YYYYMMDD.md and has the right H1
  6. audit/ shape — every audit/*.md parses as a valid AuditEntry (incl. unique
     id and filename-timestamp match)
  7. Audit targets — every open audit's `target` file must exist
  8. Duplicate pages — pages whose titles normalize to the same key (case,
     punctuation, word order, stop-words) are flagged as likely duplicates
  9. Source integrity — every concept/entity page must have a non-empty
     `sources:` frontmatter (precondition, not just resolution), and every
     cited slug must resolve to a file under raw/
 10. Cross-link coverage — every concept/entity page must have at least one
     outbound wikilink, or an inbound wikilink from a page other than
     index.md — being listed in index.md alone is not cross-linking
 11. Summary completeness — every ingested text source (raw/articles,
     raw/papers, raw/notes) must have a matching wiki/summaries/<slug>.md

Checks 9-11 are the research pattern's, and they only fire where its folders
exist. Two layouts live in the world and no flag tells them apart: the **pages**
on disk are the declaration — research iff a page actually lives under
concepts/, entities/ or summaries/ (see `research_pages`). An empty folder
declares nothing, and the mode is printed on the first line of every report,
because a check that disappears in silence is worse than a check that is absent.

Every wiki, whatever its layout:
 12. Journal shape and integrity — raw/journal/YYYYMMDD.md, lines as
     `- HH:MM — text`, and **append-only verified against the previous lint**:
     a file that shrank, or whose already-written head changed, is reported. The
     journal is the only record of what was said, so a line that changes leaves
     behind a page nothing supports. A file that cannot be read is reported as
     unreadable — that is a different fact from "a line changed", and it is not
     silence either.
 15. Map size — wiki/index.md is injected into every turn of every conversation
     in that project, and past a ceiling the rest is not injected at all.
 16. Page size — the pages are injected too, and past their ceiling a page is
     skipped *whole*, every turn: no page is ever injected half. Selection is
     alphabetical, so no question can call an over-long page up. It has to be
     split.
 18. A page list inside AGENTS.md — that file is injected **whole** into every
     turn of the project, with no ceiling and no curator, while the map it
     duplicates is cut at MAP_MAX_CHARS. 🟡: nothing there is false, it is paid
     for twice — and unlike the map, an entry naming a deleted page is reported
     by nothing (check 1 walks wiki/, and AGENTS.md is outside it).

Notebook layout only (flat pages, no page under concepts/entities/summaries):
 13. Page state — every page declares `state:` from a closed vocabulary, matched
     case-insensitively. A page is worth exactly what its state says. Any
     index.md is a map, not a page, and is exempt.
 14. Cross-linking — a page with no link in or out is a note in a folder. Being
     listed in the map is not a link.
 17. Page source — `source:` is the trail from a page back to the sentence that
     caused it. Missing, it is 🟡: the page is not wrong, it is unverifiable.
     Naming a file that is not there is a **separate** 🟡: the trail was written
     and now leads nowhere. The `#HH:MM` anchor is not part of the path.
 19. Whose words a decision rests on — a page at `state: decided`/`done` whose
     journal line reads `[inferred]` is 🔴: the journal itself says the assistant
     concluded it, so the page asserts something false. A line with no marker, a
     `source:` with no `#time`, or an anchor that does not resolve is 🟡:
     unverifiable, not false — pages written before the markers existed cannot be
     attributed at all, and a 🔴 each would turn healthy projects into red walls.
     The same pass reports the **mute** side as ℹ️ and does not count it: a page
     that does not claim a decision and whose `source:` could never support one,
     i.e. one the write guard would refuse the day somebody tried. That guard
     speaks only when a pass attempts a promotion, so a page nobody attempts
     stays capped without anyone saying so.
     The write-time half of this is `gardener._provenance_guard`, which only sees
     writes; this pass is what reaches what is already on disk.

Every list in this report is capped (see `LIST_MAX_ENTRIES`) and says how many
entries it did not print. The header count is always the real total.

Every check that reads wikilinks reads them through `extract_wikilinks`, which
drops fenced blocks and inline code first: a page documenting the wikilink
syntax shows examples, it does not link. And every check that reads frontmatter
reads it through `parse_frontmatter`, which tolerates a BOM, a blank line before
the opening `---`, a missing final newline, and an inline `# comment` after a
scalar. A linter that reports 🔴 on a correct file teaches the reader to skim
past 🔴.

State: check 12 keeps one digest per journal file under <wiki>/.jenny/, which is
the only way to answer "did a line change since last time". It is machinery, not
the user's material — same place and same reason as the gardener's cursor. It is
written atomically, it is only announced when the write actually happened, and
the digest of a file that just failed the check is **not** overwritten: "lint,
fix, re-run until clean" must not be a way to launder the record.

Workspace checks (--workspace): lints every wiki under <wikis-dir>, then:
  8. wikis/_index.md exists and its wiki-registry block is in sync with the
     wikis on disk (via reindex_wikis.check_index). Add --fix to repair drift.

One bad wiki costs one wiki: each wiki is linted inside a try/except, and so is
the registry pass. An exception used to abort the whole run at the offending
wiki — in alphabetical order, so the first damaged folder hid all the ones after
it.

Exit codes (all messages, errors included, go to **stdout**):
  0 — no issues found
  1 — issues found: the wiki was linted and it has problems
  2 — unusable input: nothing was linted (no wiki/ under the given root, no
      wikis under the given workspace, bad arguments). This is *not* a clean
      wiki, and it must not read like one — a path typo used to print to stderr
      and return 1, so a caller that captures stdout saw an empty report.
"""

import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reindex_wikis  # noqa: E402  (sibling script, same scripts/ dir)

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
LOG_FILENAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.md$")

# Il frontmatter si legge anche quando non è perfetto. Tre forme che una persona
# (o un editor) scrive senza pensarci e che l'ancoraggio rigido a `^---\n`
# leggeva come «nessun frontmatter» — riportando `state: (missing)` su una
# pagina che lo dichiara: un BOM davanti, una riga vuota prima del `---`, e
# l'ultimo `---` senza il newline finale (un file di solo frontmatter). Un 🔴 su
# un file corretto costa la credibilità dei 🔴 veri, quindi il tetto della
# tolleranza sta qui e non nel controllo che poi legge il campo.
FRONTMATTER_RE = re.compile(r"^\ufeff?\s*---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)

# Blocchi recintato e codice in linea: quel che ci sta dentro è **mostrato**, non
# scritto. Una pagina che documenta la sintassi dei wikilink non ha link morti,
# ha un esempio — e se l'esempio si ripete tre volte non è nemmeno «linkato
# spesso e senza pagina».
CODE_FENCE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*(?:\n.*?)?(?:^[ \t]*\1[ \t]*$|\Z)",
    re.DOTALL | re.MULTILINE,
)
INLINE_CODE_RE = re.compile(r"(`{1,2})(?:(?!\1)[\s\S])*?\1")

AUDIT_TS_RE = re.compile(r"^(\d{8}-\d{6})")  # YYYYMMDD-HHMMSS prefix

# Required audit frontmatter fields
# Nessun ``severity``: il campo e' uscito dal formato il 22/09/2026 (v. la
# docstring di ``AuditEntry``). Questo e' un insieme di chiavi **obbligatorie**,
# non chiuso — il controllo e' ``REQUIRED - set(fm.keys())`` — quindi un file
# vecchio che se la porta dietro passa lo stesso.
AUDIT_REQUIRED_FIELDS = {
    "id", "target", "target_lines", "anchor_before", "anchor_text",
    "anchor_after", "author", "source", "created", "status",
}

# Canonical op names for log/ entries (SKILL.md § log/ format).
#
# ``gardener`` non e' un'operazione della skill: e' l'unico attore che scrive qui
# senza passare da un prompt (``GardenerStore.log_pass``, ``## [HH:MM] gardener |
# ...``), e finche' non era in elenco ogni progetto con una passata alle spalle
# portava un 🟡 permanente. **D14.** Il costo di un controllo che non conosce un
# attore che esiste non e' il falso allarme in se': e' che accanto agli altri
# diciotto insegna a scorrere l'elenco senza leggerlo.
#
# ``reindex`` non c'entra col giardiniere ed e' lo stesso difetto: sta nella
# tabella «Ops allowed in the log» di ``references/log-guide.md`` — cioe' nel
# documento da cui il modello copia la forma della riga — e non era qui. Fra un
# riferimento che autorizza e un controllo che boccia vince il riferimento: e'
# quello che si legge prima di scrivere.
VALID_LOG_OPS = {
    "compile", "ingest", "query", "lint", "audit", "promote", "split", "scaffold",
    "reindex", "gardener",
}
LOG_ENTRY_RE = re.compile(r"^## \[\d{2}:\d{2}\] (\S+)")

# Stop-words dropped when comparing page titles for near-duplicates.
TITLE_STOPWORDS = {"a", "an", "the", "of", "and", "to", "for", "in", "on", "vs", "with"}

# ── Il taccuino: quel che il formato nuovo aggiunge ─────────────────────────
#
# Due layout esistono nel mondo e **nessun flag li distingue**: la struttura su
# disco è la dichiarazione. Una wiki di ricerca ha ``concepts/``/``entities/``/
# ``summaries/`` sotto ``wiki/``; un taccuino ha pagine piatte. I controlli del
# pattern di ricerca (passi 9-11) sono già ristretti a ``concepts``/``entities``
# e su un taccuino non scattano; questi sono il loro specchio.

RESEARCH_SUBDIRS = ("concepts", "entities", "summaries")

# Il vocabolario di ``state:``. Una pagina vale quanto il suo stato dice, ed è
# l'anticorpo alla deriva auto-confermante: senza stato, un'ipotesi appuntata di
# passaggio si rilegge fra un mese come un fatto stabilito.
PAGE_STATES = {"open", "hypothesis", "decided", "done"}

# Gli stati che **rivendicano una decisione dell'utente**, e non solo un appunto.
# Sono gli unici per cui il passo 19 chiede di chi siano le parole: `open` e
# `hypothesis` non rivendicano niente, quindi non c'è niente da attribuire.
_STATES_CLAIMING_A_DECISION = {"decided", "done"}

# I marcatori di attribuzione che la cattura scrive nel diario. Duplicati **a
# mano** e non importati da ``jenny.agent.tools.journal``: questo file è uno
# script della skill, gira anche con `python3 lint_wiki.py <wiki>` fuori dal
# package, e un import del runtime lo renderebbe non eseguibile da lì. Se i
# letterali divergono il passo 19 smette di riconoscere le righe e degrada in
# giallo — inverificabile, non falso — che è il verso giusto in cui rompersi.
_SAID_MARKERS = ("[said]", "[recovered]")
_INFERRED_MARKER = "[inferred]"


# Non è un marcatore e non può esserlo (nessuna riga di diario inizia così): è
# l'esito «quel minuto tiene più righe e non sono tutte dell'utente», che il
# chiamante deve poter dire con una frase sua. Riportato come «line not found»
# mandava a cercare una riga che c'è — un avviso su cui non si può agire.
_MIXED_MINUTE = "<mixed minute>"

# I tre esiti di un tetto su ``decided``, e sono tre perché si leggono in tre
# modi. ``FIXABLE`` si ripara editando ``source:``, quindi si **nomina**.
# ``HISTORY`` è una riga di diario che non regge la promozione (anteriore ai
# marcatori, oppure ``[inferred]``): si **conta**. ``DOCUMENT`` è una fonte che
# non è una riga di diario e non lo diventerà — un file copiato in ``raw/`` —
# e si conta **a parte**: la frase di ``HISTORY`` manderebbe a cercare pagine
# vecchie, e qui non c'è niente di vecchio né di sbagliato.
_CAP_FIXABLE = "fixable"
_CAP_HISTORY = "history"
_CAP_DOCUMENT = "document"

# ``HH:MM`` o ``HH:MM.N``: v. ``_journal_line_marker``. Tenuto uguale a
# ``jenny/agent/gardener.py::_ANCHOR_RE`` da un test che fa girare gli stessi casi
# nelle due implementazioni (questo script non importa ``jenny``).
_ANCHOR_RE = re.compile(r"^(\d{2}:\d{2})(?:\.(\d+))?$")


def _journal_markers(
    root_path: Path, file_part: str, cache: dict[str, dict[str, list[str]]]
) -> dict[str, list[str]]:
    """I marcatori di un giorno di diario, ``minuto → [marcatori]``, con cache.

    Estratta da ``_journal_line_marker`` perché ``_decided_cap_reason`` deve
    poter chiedere del **giorno intero** e non di un minuto: una ``source:``
    senza ora si ripara aggiungendo l'ora *solo se* quel giorno ha marcatori da
    leggere, e mandare a una riparazione che non può funzionare è peggio che
    tacere. Un file illeggibile vale giorno vuoto, come prima.
    """
    if file_part not in cache:
        lines: dict[str, list[str]] = {}
        try:
            text = (root_path / file_part).read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            match = re.match(r"^-\s+(\d{2}:\d{2})\s+—\s+(.*)$", line)
            if match:
                body = match.group(2).lstrip()
                marker = body.split(" ", 1)[0] if body.startswith("[") else ""
                lines.setdefault(match.group(1), []).append(marker)
        cache[file_part] = lines
    return cache[file_part]


def _journal_line_marker(
    root_path: Path, file_part: str, anchor: str, cache: dict[str, dict[str, list[str]]]
) -> str | None:
    """Il marcatore della riga di diario a *anchor*, o ``None`` se non si sa.

    ``None`` copre i casi che il chiamante distingue nel messaggio ma non nella
    severità: il file illeggibile, l'ora che nel file non compare, e un minuto che
    tiene più righe di cui non tutte dell'utente. La cache è per file, perché una
    wiki con venti pagine ancorate allo stesso giorno lo rileggerebbe venti volte.

    **Una lista per minuto e non un marcatore. D13.** La prima versione teneva un
    dizionario ``minuto → marcatore``, quindi su un minuto con più righe vinceva
    **l'ultima** — e la guardia gemella in ``gardener.py`` leggeva la **prima**: due
    copie dello stesso difetto che non erano nemmeno d'accordo su quale riga
    guardare. Da T4 la cattura scrive una riga per fatto, quindi un turno in cui
    l'utente dice una cosa e Jenny ne deduce la conseguenza mette ``[said]`` e
    ``[inferred]`` allo stesso ``HH:MM``: l'ancoraggio al minuto nudo lì non
    *dice* quale riga la pagina intenda, e ``#HH:MM.2`` — la seconda riga di quel
    minuto, contando da 1 — è come lo si dice. Se sono tutte dell'utente il minuto
    nudo basta: quale delle due la pagina intenda non cambia la risposta.
    """
    _journal_markers(root_path, file_part, cache)
    anchored = _ANCHOR_RE.match(anchor.strip())
    if anchored is None:
        return None
    markers = cache[file_part].get(anchored.group(1))
    if not markers:
        return None
    if (ordinal := anchored.group(2)) is not None:
        index = int(ordinal) - 1
        return markers[index] if 0 <= index < len(markers) else None
    if len(markers) == 1:
        return markers[0]
    if all(m in _SAID_MARKERS for m in markers):
        return markers[0]
    return _MIXED_MINUTE


def _decided_cap_reason(
    root_path: Path, file_part: str, anchor: str, cache: dict[str, dict[str, list[str]]]
) -> tuple[str, str] | None:
    """Perché questa pagina **non potrà** essere marcata ``decided``, o ``None``.

    Il passo 19 guarda chi *si dichiara* deciso. Questo guarda il lato muto:
    una pagina a ``open`` (o ``hypothesis``) la cui ``source:`` non regge un
    ``decided``, cioè che verrebbe **rifiutata** il giorno in cui qualcuno prova
    a promuoverla. Nessuno lo dice oggi: la guardia in scrittura parla solo
    quando una passata ci prova, e se non ci prova mai il tetto resta invisibile.

    Il caso di campo (25/08, ``viaggio-pazzo``): la pagina del progetto è nata il
    24/08 ancorata al **giorno intero**, e le righe di quel giorno sono anteriori
    ai marcatori. È a ``open`` per sempre, correttamente, e in due giorni di
    lavoro niente e nessuno l'ha detto.

    Torna ``(motivo, outcome)`` — uno di :data:`_CAP_FIXABLE`, :data:`_CAP_HISTORY`,
    :data:`_CAP_DOCUMENT`. **La seconda metà decide come si stampa**, e non è
    pedanteria: su una wiki scritta prima dei marcatori *quasi ogni* pagina a
    ``open`` è qui dentro, e un elenco che le nomina tutte per dire «non si può
    fare niente» è il muro che questo file evita per principio in tre punti
    diversi. Quel che si nomina è quel su cui si agisce; il resto è un conteggio.

    ``FIXABLE`` vuol dire che **una modifica a ``source:`` cambia l'esito**: manca
    l'ora e il giorno ha marcatori da leggere, il minuto è misto e vuole
    l'ordinale, l'ancora non risolve. ``HISTORY`` vuol dire che la riga di diario
    stessa non regge la promozione — perché è anteriore ai marcatori, oppure
    perché è `[inferred]`, che non è un difetto ma la risposta giusta.

    **Il giorno si legge prima di tutto, e questa è la correzione del 26/08.**
    Misurato sul progetto ``salute`` vero: cinque pagine su cinque hanno
    ``source: raw/research/<documento>.md`` — la forma che ``project.md`` chiede
    quando il materiale arriva da fuori — e questa funzione le mandava tutte e
    cinque ad «aggiungi un ``#HH:MM``», cioè a una riparazione che su un documento
    non esiste, marcate *riparabili*, che è la categoria peggiore in cui
    sbagliare. Un file senza righe ``- HH:MM — `` non ha nessun minuto a cui
    ancorarsi: non è un difetto della pagina, è ``_CAP_DOCUMENT``. La prova è
    l'assenza di righe e non il percorso, perché è l'assenza che rende
    irrisolvibile qualunque ancora — e copre da sé il caso dell'ancora scritta
    comunque (``raw/research/x.md#09:12``), che prima usciva come «quella riga non
    è nel diario, controlla l'ancora».
    """
    day = _journal_markers(root_path, file_part, cache)
    if not day:
        return (
            "`source:` names a file with no journal lines — a document copied into `raw/`, "
            "where no `#HH:MM` exists to point at",
            _CAP_DOCUMENT,
        )
    if not anchor:
        if not any(marker for markers in day.values() for marker in markers):
            return ("no #time, and no line of that day carries a marker", _CAP_HISTORY)
        return (
            "no #time — nothing can check which line of that day it means; add it",
            _CAP_FIXABLE,
        )
    marker = _journal_line_marker(root_path, file_part, anchor, cache)
    if marker in _SAID_MARKERS:
        return None
    if marker == _MIXED_MINUTE:
        return (
            "that minute is mixed — add the line's place within it, e.g. #HH:MM.2",
            _CAP_FIXABLE,
        )
    if marker == _INFERRED_MARKER:
        return ("the journal attributes that line to the assistant", _CAP_HISTORY)
    if marker is None:
        return ("that line is not in the journal — check the anchor", _CAP_FIXABLE)
    return ("that line carries no marker (written before they existed)", _CAP_HISTORY)


# Tetto della mappa, in caratteri. **Non è un numero scelto qui**: è la soglia
# oltre la quale il blocco di progetto smette di iniettare la mappa intera in
# ogni turno (``jenny/agent/context.py::_PROJECT_MAP_MAX_CHARS``). Oltre, il
# resto della mappa esiste ma l'agente non lo vede senza aprire il file — quindi
# è un avviso che vale per **tutti** i layout, perché la mappa la riceve ogni
# progetto.
#
# La copia è deliberata e non si può togliere: questo script gira anche fuori
# dall'app e non importa ``jenny``. Quel che tiene i due numeri uguali è
# ``tests/skills/llm_wiki/test_lint_wiki.py::test_the_ceiling_matches_the_one_the_prompt_uses``,
# che legge ``context.py`` come testo (T3.12).
MAP_MAX_CHARS = 2000

# Tetto della **singola pagina**, in caratteri, e per la stessa ragione della
# mappa: non è scelto qui, è il budget che il blocco di progetto ha per il
# contenuto delle pagine (``jenny/agent/context.py::_PROJECT_PAGES_MAX_CHARS``).
# Le due costanti sono una famiglia, e la differenza fra loro è quel che accade
# oltre la soglia. La mappa oltre il tetto entra **troncata**: il resto esiste e
# non si vede. Una pagina oltre il tetto non entra **per niente** — nessuna
# pagina entra a metà, quindi una pagina che da sola supera il budget viene
# saltata a ogni turno di ogni conversazione del progetto, per sempre, e la
# selezione è alfabetica: non c'è messaggio dell'utente che possa richiamarla.
#
# Perché il budget intero e non una frazione. Sulle otto wiki vere (188 pagine,
# misurate il 23/08: mediana 3.217, p90 6.396, massimo 16.385) questo numero
# segnala **23 pagine** su 188 — 9 in ``main``, 9 in ``allergie``, 5 in
# ``patreon-creator`` — e sono *esattamente* le 23 il cui blocco recintato sfonda
# il budget da solo, cioè quelle che il modello non vedrà mai. Una soglia più
# bassa segnalerebbe pagine che il prompt riesce ancora a portare: a 4.000 sono
# 75, a 3.000 sono 98, a 2.000 sono 131. Una lista di 131 voci su 188 non è un
# elenco di lavori, è un modo di dire «tutto», e un avviso su cui nessuno può
# agire è peggio del silenzio.
#
# Come la mappa, la copia è tenuta ferma da un test e non da un import:
# ``test_the_page_ceiling_matches_the_budget_the_prompt_has`` (T3.12).
PAGE_MAX_CHARS = 6000

# Quanto elenco di pagine si tollera **dentro `AGENTS.md`**, in caratteri.
#
# **Non è un tetto sul file** e non lo diventerà: T7.5 e T7.10 hanno rifiutato di
# limitare `AGENTS.md` per la stessa ragione due volte — è un file che scrive
# l'utente, e un taglio silenzioso butta le sue istruzioni. Questo numero
# governa **un solo avviso**, e il difetto che nomina non è la dimensione: è la
# *collocazione*. Misurato il 24/08 sul progetto reale più grande: 4.381
# caratteri su 7.161 (il 61%) erano un elenco di pagine, mentre la mappa vera di
# quel progetto — `wiki/index.md`, 7.315 caratteri — arriva al modello tagliata
# a :data:`MAP_MAX_CHARS`. Cioè ogni turno portava una mappa **troncata** più una
# seconda copia **illimitata** dello stesso genere di contenuto. Ed è anche il
# motivo per cui un taglio in coda sarebbe l'errore peggiore possibile: la coda
# di quel file era «Open research questions», la sola parte azionabile.
#
# Perché metà del tetto della mappa. Il numero non è scelto: è derivato da quel
# che l'iniettore concede all'indice **vero**. Oltre metà di quel budget, la
# copia non curata non è più un residuo — è un secondo indice a spese del primo.
# Sotto, un «parti da qui» di tre voci resta silenzioso, che è quel che serve:
# l'avviso deve parlare solo quando c'è qualcosa da spostare.
AGENTS_LIST_MAX_CHARS = MAP_MAX_CHARS // 2

# Una voce di elenco: un bullet (o un numero) e poi qualcosa.
LIST_LINE_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")
# Il riferimento a una pagina dentro una voce, nelle tre forme che si scrivono
# davvero: wikilink, link markdown a un `.md`, percorso fra backtick.
PAGE_REF_RE = re.compile(r"\[\[([^\]]+)\]\]|\]\(([^)\s]*\.md)[^)]*\)|`([^`\s]*\.md)`")

JOURNAL_FILENAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.md$")
JOURNAL_ENTRY_RE = re.compile(r"^- \d{2}:\d{2} \u2014 \S")

# Quante voci si stampano per elenco. **Non è un numero di stile.** L'output di
# questo script torna al modello attraverso ``python_exec``, che oltre il suo
# tetto (10.000 caratteri per default) tiene la testa e la coda della stringa e
# **butta via il mezzo** (``agent/tools/python_exec.py``, ``max_output_chars``).
# Un elenco senza tetto sfonda quel limite da solo — le otto wiki vere hanno 188
# pagine, e un solo passo che le nomina tutte fa più di 10 kB — e quel che il
# modello riceve è il report tagliato nel mezzo da un marcatore generico: si vede
# che *qualcosa* manca, non *cosa* né da quale passo, e i passi che stavano in
# mezzo spariscono interi. Meglio un tetto scelto qui, che dice quante voci non
# ha stampato, di un taglio cieco a metà stringa.
LIST_MAX_ENTRIES = 20

# Lo stato del lint, dentro la cartella nascosta del progetto: macchinario, non
# materiale dell'utente — come il cursore del giardiniere, e per la stessa
# ragione sta fuori da ``wiki/`` (viste e grafo non lo vedono).
LINT_STATE_REL = ".jenny/lint_journal.json"

# I tre esiti, e il terzo è quello che mancava. «Input inutilizzabile» non è
# «problemi trovati»: nel primo caso non è stato controllato niente, e chiamarlo
# 1 come l'altro rende una wiki con dei problemi indistinguibile da un percorso
# sbagliato. Il chiamante che conta davvero — ``python_exec_builtins.wiki_lint``
# — cattura **solo stdout** e il codice lo butta, quindi il messaggio deve stare
# su stdout e il codice serve solo a chi lo guarda.
EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_UNUSABLE = 2


def print_entries(lines: list[str], cap: int = LIST_MAX_ENTRIES) -> None:
    """Le prime *cap* righe di un elenco, e **quante ne restano**.

    Il totale vero sta già nell'intestazione del passo, quindi qui non si perde
    nessun numero: si perde l'elenco integrale, che è esattamente il pezzo che
    ``python_exec`` butterebbe via da sé, senza dire quale (v.
    ``LIST_MAX_ENTRIES``). Il funnel è uno per la stessa ragione di
    ``extract_wikilinks``: un passo aggiunto domani non deve poter dimenticare il
    tetto.
    """
    for line in lines[:cap]:
        print(line)
    if len(lines) > cap:
        print(f"   …and {len(lines) - cap} more (of {len(lines)} — the count above is the total)")


def read_md(path: Path) -> str:
    """Il testo di un file markdown, **senza il BOM**.

    ``utf-8-sig`` toglie il BOM se c'è e non cambia niente se non c'è. Serve
    perché un BOM davanti al ``---`` faceva leggere «nessun frontmatter» — e
    quindi ``state: (missing)`` su una pagina che lo dichiara — e davanti a un
    ``# Titolo`` faceva cadere il ripiego dell'H1 in `page_title`, che tornava
    allo stem del file. Due 🔴 su un file che un editor ha solo salvato a modo
    suo.

    I due passi sulle dimensioni (15 e 16) **non** passano da qui, di proposito:
    quelli devono contare i caratteri come li conta l'iniettore, e l'iniettore
    legge ``utf-8``, dove il BOM è un carattere che il budget paga.

    ``errors="replace"`` perché **un file illeggibile deve costare un file**. Un
    solo ``.md`` salvato in latin-1 sotto ``wiki/`` — quel che esce da un editor
    di sistema o da un file arrivato da fuori — faceva scoppiare il passo 1 sulla
    prima pagina che apriva: traceback, zero risultati stampati, e attraverso
    ``wiki_lint`` (che cattura stdout e lo restituisce solo al ritorno normale)
    anche i risultati già calcolati buttati. Il byte guasto diventa ``\\ufffd`` e
    tutto il resto della pagina si continua a leggere: i link, la frontmatter e
    lo stato di quella pagina restano controllabili. Che il file **non sia**
    UTF-8 non si perde: lo dice il passo 0, che è l'unico posto dove va detto.
    """
    return path.read_text(encoding="utf-8-sig", errors="replace")


def decode_problem(path: Path) -> str | None:
    """Perché *path* non è UTF-8, o ``None`` se lo è (o se non si apre affatto).

    Il fatto che conta non è «il linter non riesce a leggerlo»: è che
    ``ContextBuilder._read_project_pages`` legge le pagine in ``utf-8`` stretto e
    conta come «rimasta fuori» ogni pagina che non decodifica. Una pagina non
    UTF-8 quindi **non entra nel prompt**, a ogni turno, in ogni conversazione di
    quel progetto — la stessa conseguenza del passo 16, per una causa diversa e
    con un rimedio diverso: quella va spezzata, questa va ri-salvata.

    Un ``OSError`` non è questo problema e torna ``None``: un file che non si
    apre non ha un encoding. Lo dicono già i passi che lo aprono (12 per il
    diario) e non c'è ragione di riportarlo due volte con un nome sbagliato.
    """
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"byte {exc.start} is not valid UTF-8 ({exc.reason})"
    except OSError:
        return None
    return None


def research_pages(wiki_path: Path) -> list[Path]:
    """Le pagine che **dichiarano** il layout di ricerca: quelle che stanno dentro
    ``concepts/``, ``entities/`` o ``summaries/``.

    Sono le pagine e non le cartelle, perché una cartella vuota non dichiara
    niente. Finché bastava la cartella, il top-up dello scaffold di questa skill
    — che SKILL.md consiglia come sicuro da rilanciare — creava quelle tre
    directory su un taccuino e il modo passava da taccuino a ricerca: il
    controllo su ``state:``, che è l'anticorpo alla deriva auto-confermante,
    spariva dall'output senza che nessuno lo dicesse. Un controllo che si spegne
    in silenzio è peggio di un controllo che non c'è, perché il verde resta.

    Ordinate, così il modo stampato nomina sempre la stessa pagina a parità di
    contenuto: un output che cambia da un run all'altro non si può confrontare.
    """
    found: list[Path] = []
    for name in RESEARCH_SUBDIRS:
        sub = wiki_path / name
        if sub.is_dir():
            found.extend(sorted(p for p in sub.rglob("*.md") if p.is_file()))
    return found


def is_research_layout(wiki_path: Path) -> bool:
    """Se questa wiki è una biblioteca di ricerca, letto dalle **pagine**."""
    return bool(research_pages(wiki_path))


# Le sottocartelle di ``wiki/`` che l'iniettore non guarda. Una sola oggi, e la
# lista esiste per essere confrontabile con l'altro lato.
INJECT_SKIP_DIRS = ("summaries",)


def is_injected_page(rel: Path) -> bool:
    """Vero se un ``.md`` **relativo a ``wiki/``** è una pagina che entra nel prompt.

    **Copia deliberata di ``jenny/utils/wiki_paths.py::is_wiki_page_rel``**, più
    l'esclusione dell'indice che là sta nel chiamante
    (``iter_wiki_pages``/``WIKI_INDEX_FILENAME``). Deliberata perché questo
    script è un checkout della skill: gira anche fuori dall'app, l'utente lo può
    modificare, e non importa ``jenny`` — la stessa ragione per cui
    :data:`MAP_MAX_CHARS` e ``_COMMON_DIRS`` sono duplicati, e con lo stesso
    prezzo: le due copie vanno tenute allineate a mano. Il commento dall'altra
    parte punta qui.

    La regola, per esteso: niente segmento che comincia per punto (a **ogni**
    livello: prima qui si guardava solo il nome del file, quindi una
    ``wiki/.bozze/lunga.md`` veniva segnalata come troppo lunga per entrare in
    un prompt in cui non entrava comunque), niente
    :data:`INJECT_SKIP_DIRS` al primo livello, niente ``wiki/index.md`` — quello
    è la mappa e ha il passo 15 tutto suo.
    """
    parts = rel.parts
    if any(part.startswith(".") for part in parts):
        return False
    if parts and parts[0] in INJECT_SKIP_DIRS:
        return False
    # Sensibile alle maiuscole come l'iniettore, e per il motivo scritto là: su
    # un filesystem che le distingue — l'unico su cui l'app gira — un
    # ``INDEX.md`` non è la mappa, quindi è una pagina.
    return rel.as_posix() != "index.md"


def journal_files(root_path: Path) -> list[Path]:
    journal = root_path / "raw" / "journal"
    return sorted(journal.glob("*.md")) if journal.is_dir() else []


def head_digest(path: Path, size: int) -> str | None:
    """Digest dei primi *size* byte di *path*, o ``None`` se illeggibile.

    È il pezzo che rende esatto il controllo dell'append-only: la testa di oggi
    deve essere identica al file di ieri, byte per byte.
    """
    try:
        with path.open("rb") as fh:
            return hashlib.sha256(fh.read(size)).hexdigest()
    except OSError:
        return None


def read_lint_state(root_path: Path) -> dict:
    """Le impronte del run precedente, o ``{}``.

    **Ogni strato si controlla da sé.** Il file è sul disco dell'utente e può
    essere qualunque cosa: un JSON valido con ``digests`` lista (``{"digests":
    [1, 2, 3]}``) passava il controllo sull'oggetto esterno e faceva scoppiare
    un ``AttributeError`` in mezzo al passo 12 — cioè zero risultati stampati
    per un file di stato malformato, che è l'esatto contrario del suo scopo.
    """
    try:
        data = json.loads((root_path / LINT_STATE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    digests = data.get("digests")
    return digests if isinstance(digests, dict) else {}


def write_lint_state(root_path: Path, digests: dict) -> bool:
    """Salva le impronte. **Ritorna True solo se lo stato è davvero su disco.**

    Prima ingoiava ogni ``OSError`` e il chiamante annunciava «baseline
    recorded» comunque. In un turno in sola lettura ``ReadOnlyTurnError`` *è* un
    ``OSError``, quindi il lint dichiarava una base che non esiste — e al run
    dopo, senza base, l'append-only non è "verificato": è "mai stato guardato".
    Lo stesso vale per un disco pieno. Un avviso qui costa una riga; una
    promessa falsa costa il controllo.

    La scrittura è **atomica**: temp file accanto al bersaglio, ``flush`` +
    ``fsync``, poi ``os.replace``. È la stessa ragione per cui il cursore del
    giardiniere, sullo stesso telefono, passa da ``atomic_write`` — uno stato
    troncato a metà si rilegge come JSON invalido, cioè base perduta, cioè un
    controllo che riparte da zero senza dirlo. **L'originale è
    ``jenny/utils/path.py::atomic_write``** e questa è una copia a mano, ridotta
    al necessario (niente fsync della directory: qui una base persa in un crash
    del kernel si ricrea al run dopo, e un file mezzo scritto no). Non è un
    import perché questo script è un checkout della skill, dove il package
    ``jenny`` non c'è — stessa ragione, e stesso commento, di ``slugify``.
    """
    path = root_path / LINT_STATE_REL
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "digests": digests}, indent=2, sort_keys=True)
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        print(f"\n⚠️  Could not record the journal state ({exc}):")
        print(f"   {LINT_STATE_REL}")
        print("   (without it the next lint cannot tell an appended line from a")
        print("    rewritten one — it will start over from a new baseline)")
        return False


def dup_key(title: str) -> str:
    """A normalized identity for a page title: lowercased alphanumeric tokens,
    stop-words removed, sorted. Two titles with the same key differ only by case,
    punctuation, separators, word order, or stop-words — i.e. likely duplicates."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in TITLE_STOPWORDS]
    return " ".join(sorted(tokens))


def page_title(path: Path) -> str:
    """The page's title: frontmatter `title:`, else first H1, else filename stem."""
    text = read_md(path)
    fm = parse_frontmatter(text)
    if fm and str(fm.get("title", "")).strip():
        return str(fm["title"]).strip()
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def slugify(name: str) -> str:
    """La regola di normalizzazione dell'app, ricopiata: NFKD → ASCII, spazi e
    underscore in `-`, il resto della punteggiatura via.

    **L'originale vive in ``jenny/webui/wiki.py::_slugify``** e questa è una
    copia a mano, da confrontare con quella quando una delle due cambia. Non è
    un import perché questo script è un checkout della skill: gira sotto
    ``python_exec`` e viene copiato nel workspace, dove il package ``jenny`` non
    c'è — stessa ragione, e stesso commento, di
    ``reindex_wikis.read_wiki_scope``.
    """
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9.\-]", "", name)
    return name.strip("-")


def slug_path(ref: str) -> str:
    """`slugify` segmento per segmento, come ``wiki.py::_normalize_page_path``
    (che poi aggiunge `.md`: qui le chiavi sono senza estensione)."""
    return "/".join(slugify(part) for part in ref.split("/"))


def load_pages(wiki_dir: Path) -> dict[str, Path]:
    """Indice dei nomi con cui una pagina è raggiungibile.

    **Le chiavi sono minuscole, e la ragione arriva dal campo.** Chi risolve i
    link davvero — ``jenny/webui/wiki.py::resolve_wikilink`` — prova esatto, poi
    ``.md``, poi **case-insensitive**, poi lo stem. Il lint confrontava con
    `==` sensibile alle maiuscole, quindi segnalava come morto un link che l'app
    apre senza problemi.

    Non è un caso limite: il giardiniere scrive `[[Rondine]]` per una pagina
    `rondine.md` — i nomi propri li scrive maiuscoli, come farebbe una persona —
    e misurato sul telefono il 23/08 la prima mappa che ha prodotto conteneva
    esattamente questo. Un lint che grida al lupo su un link sano è un lint che
    si impara a ignorare, cioè peggio di nessun lint.

    Per lo stesso motivo ogni pagina è registrata anche sotto la sua forma
    *slug* (vedi `slugify`) e ogni `index.md` sotto il nome della sua cartella:
    sono gli altri due rami che l'app prova prima di dichiarare un link morto.

    **L'unica differenza voluta dall'app**: lo slug si applica anche al nome del
    file, mentre `resolve_wikilink` normalizza solo il bersaglio e confronta il
    path su disco così com'è. Serve per il caso di un nome scritto in una forma
    Unicode e linkato nell'altra (NFD/NFC), che l'app *non* risolve su un
    filesystem sensibile alla normalizzazione. Il lint qui è più tollerante di
    proposito: un nome di file non-ASCII non lo scrive nessuno dei due lati —
    scaffold e giardiniere creano già slug — e un lint che sbaglia in questo
    verso tace, invece di segnalare morto un link che si apre.

    **Non è l'elenco delle pagine, ed è per questo che non esclude niente**
    (T9.5). :func:`is_injected_page` risponde a "cosa entra nel prompt" e lascia
    fuori nascosti, ``summaries/`` e la mappa; questo indice risponde a "questo
    link si apre", e togliere una di quelle tre voci vorrebbe dire dichiarare
    morto un link che l'app apre — i `[[summaries/<doc>]]` delle sezioni
    "Sources" sono esattamente il caso, e sono la forma che SKILL.md insegna. Le
    due domande hanno due risposte: sono funzioni diverse di proposito, non due
    versioni della stessa.
    """
    pages: dict[str, Path] = {}
    for p in wiki_dir.rglob("*.md"):
        rel = p.relative_to(wiki_dir).with_suffix("").as_posix()
        for key in (p.stem.lower(), slugify(p.stem), rel.lower(), slug_path(rel)):
            if not key:
                continue
            # Due pagine con lo stesso nome in cartelle diverse (`a/nota.md`,
            # `b/nota.md`) si contendono la chiave `nota`, e qui vinceva l'ultima
            # letta — l'ordine della directory. La regola è quella dell'app
            # (`suffix_rank`): vince la più vicina alla radice.
            previous = pages.get(key)
            if previous is None or suffix_rank(p) < suffix_rank(previous):
                pages[key] = p
    # Link a cartella. Prima ancora di provare `<target>.md`, l'app apre
    # `<target>/index.md` se la cartella esiste. La mappa d'esempio in SKILL.md
    # scrive la forma esplicita `[[concepts/Bar/index|Bar]]`, che risolveva già;
    # questa è la forma abbreviata `[[concepts/Bar]]`, che l'app apre e il lint
    # dava per morta. Le chiavi delle cartelle si scrivono dopo, e vincono,
    # perché è l'ordine in cui l'app prova i rami.
    for p in wiki_dir.rglob("index.md"):
        rel_dir = p.parent.relative_to(wiki_dir).as_posix()
        if rel_dir in ("", "."):
            continue
        for key in (rel_dir.lower(), slug_path(rel_dir)):
            if key:
                pages[key] = p
    return pages


def suffix_rank(path: Path) -> tuple[int, str]:
    """Ordine di preferenza fra più pagine che finiscono per lo stesso path.

    Copia di ``jenny/webui/wiki.py::_suffix_rank``, parola per parola: vince la
    più vicina alla radice, a pari profondità la prima in ordine alfabetico. Lo
    script non può importare il package, quindi la regola sta scritta due volte
    e un test (`tests/skills/llm_wiki/test_lint_wiki.py`) confronta le due
    risposte invece di fidarsi.
    """
    return (len(path.parts), path.as_posix())


def page_for_link(pages: dict[str, Path], link: str) -> Path | None:
    """La pagina a cui punta *link*, con la stessa tolleranza dell'app.

    L'app (``resolve_wikilink``) confronta due forme del bersaglio: quella
    grezza minuscola e quella passata per ``_slugify``. Qui si provano entrambe,
    e ``load_pages`` registra le chiavi corrispondenti — così `[[Città]]` trova
    `citta.md` e `[[concepts/x]]` trova `concepts/x/index.md`, come sul telefono.

    **Un bersaglio a più segmenti si risolve per suffisso, non per stem.** Era il
    contrario, e le due direzioni sbagliavano entrambe: `[[<Topic>/<aspect>]]` —
    la forma che `references/article-guide.md` raccomanda dentro un `index.md`
    diviso in cartella — il lint la dava per viva perché esisteva *una* pagina
    con quello stem, mentre l'app non la apriva affatto; e `[[Altro/<aspect>]]`,
    che non esiste da nessuna parte, passava per lo stesso motivo. Ora entrambi i
    lati chiedono che il path della pagina **finisca** per il link, e l'unico
    tollerante-per-stem resta il link a un solo segmento — che è quel che il
    formato del taccuino usa e che l'app risolve davvero.
    """
    link = link.strip()
    if link.startswith("wiki/"):  # come `_strip_wiki_prefix` dell'app
        link = link[5:]
    if link.endswith(".md"):  # le chiavi di `load_pages` sono senza estensione
        link = link[:-3]
    forms = (link.lower(), slug_path(link))
    for key in forms:
        hit = pages.get(key) if key else None
        if hit is not None:
            return hit
    if "/" not in link:
        return None
    hits = {
        p
        for key, p in pages.items()
        if any(form and key.endswith(f"/{form}") for form in forms)
    }
    if not hits:
        return None
    return min(hits, key=suffix_rank)


def strip_code(text: str) -> str:
    """*text* senza i blocchi recintati e senza il codice in linea.

    Quel che sta fra i backtick è **mostrato**, non scritto: non è un link, e non
    è nemmeno una menzione. Lo spazio al posto del blocco evita che due pezzi di
    testo si saldino su un `[[` che non c'era.
    """
    return INLINE_CODE_RE.sub(" ", CODE_FENCE_RE.sub(" ", text))


def page_list_lines(text: str) -> list[str]:
    """Le righe di *text* che sono **voci di un elenco di pagine**.

    Serve al passo 18, e conta una riga solo se è un bullet *e* nomina una
    pagina: wikilink, link markdown a un ``.md``, o percorso fra backtick. Una
    riga di prosa che passa a menzionare `wiki/index.md` non è una voce
    d'indice, ed è la ragione per cui non basta cercare i `.md`.

    **Sbaglia per difetto di proposito.** Un elenco scritto a nomi nudi
    (``- Semine primaverili``, senza link) non lo vede: quel che distingue una
    voce d'indice da un bullet qualunque è il riferimento, e senza di quello
    l'unico modo di indovinare sarebbe la posizione sotto un titolo — cioè un
    avviso che scatta su un file sano. Un lint che grida al lupo si impara a
    ignorare (la lezione di ``load_pages`` e del passo 17): meglio un conteggio
    prudente, che quando parla ha ragione.

    I blocchi recintati escono (un template mostrato non è un elenco), il codice
    in linea **resta** — è una delle tre forme — e i segnaposto ``<...>`` non
    contano: l'`AGENTS.md` che uno scaffolder ha appena scritto ha già le sue
    tre righe d'esempio, e non ha ancora nessuna pagina.
    """
    out: list[str] = []
    for line in CODE_FENCE_RE.sub("\n", text).splitlines():
        if not LIST_LINE_RE.match(line):
            continue
        match = PAGE_REF_RE.search(line)
        if match is None:
            continue
        ref = next((group for group in match.groups() if group), "")
        if "<" in ref and ">" in ref:
            continue
        out.append(line)
    return out


def extract_wikilinks(text: str) -> list[str]:
    """I wikilink di *text*, **codice escluso**.

    Lo scarto sta qui e non nei singoli passi perché è la stessa domanda per
    tutti: il passo 1 dava per morti gli esempi di una pagina che documenta la
    sintassi dei wikilink, il passo 4 contava l'esempio ripetuto tre volte come
    «linkato spesso e senza pagina», e i passi 10/14 leggevano un esempio come
    la prova che la pagina è collegata. Un solo helper, e tutti guadagnano.
    """
    return WIKILINK_RE.findall(strip_code(text))


def strip_inline_comment(value: str) -> str:
    """Il commento in coda a uno scalare, via — e **solo** quello.

    La regola è quella di YAML: un commento comincia a un ``#`` preceduto da uno
    spazio (o dall'inizio del valore) e **fuori** dallo scalare quotato. Quindi
    ``state: open  # da confermare`` vale ``open``, mentre
    ``source: https://x/y#z`` resta intero — il ``#`` del frammento non ha spazio
    davanti — e ``title: "a # b"`` pure, perché lì il ``#`` sta dentro le
    virgolette.

    Le virgolette contano solo se **aprono** il valore: ``state: it's fine # x``
    non è uno scalare quotato, è un apostrofo, e il commento va comunque via.
    """
    if value[:1] in ('"', "'"):
        quote = value[0]
        i = 1
        while i < len(value):
            if value[i] == "\\" and quote == '"':
                i += 2
                continue
            if value[i] == quote:
                i += 1
                break
            i += 1
        head, tail = value[:i], value[i:]
        m = re.search(r"(?:^|\s)#", tail)
        return (head + tail[: m.start()]).rstrip() if m else value
    m = re.search(r"(?:^|\s)#", value)
    return value[: m.start()].rstrip() if m else value


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML-ish frontmatter parser. Handles the flat key:value fields
    and one-level lists/arrays actually used by audit files. Does not handle
    arbitrary YAML — intentional, to avoid a pyyaml dependency."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    result: dict = {}
    # Track multi-line folded strings via simple heuristic: quoted scalars
    # can contain \n; unquoted values are single-line.
    i = 0
    lines = body.split("\n")
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        val = strip_inline_comment(rest.strip())
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                parts = [p.strip() for p in inner.split(",")]
                parsed: list = []
                for p in parts:
                    if p.isdigit() or (p.startswith("-") and p[1:].isdigit()):
                        parsed.append(int(p))
                    else:
                        parsed.append(p.strip('"').strip("'"))
                result[key] = parsed
        elif val.startswith('"') and val.endswith('"'):
            result[key] = val[1:-1].replace("\\n", "\n").replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            result[key] = val[1:-1]
        elif val == "":
            # Possibly a block-style list:
            #   sources:
            #     - raw/articles/x.md
            #     - raw/articles/y.md
            # Consume the following indented `- item` lines. If none follow,
            # this stays an empty-string scalar (unchanged behaviour).
            block: list = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                stripped = nxt.strip()
                if not stripped:
                    j += 1
                    continue
                # A block item must be indented and start with a dash.
                if nxt[0] in (" ", "\t") and stripped.startswith("- "):
                    item = strip_inline_comment(stripped[2:].strip())
                    item = item.strip('"').strip("'")
                    block.append(item)
                    j += 1
                else:
                    break
            if block:
                result[key] = block
                i = j
                continue
            result[key] = val
        else:
            result[key] = val
        i += 1
    return result


def lint(root: str) -> int:
    root_path = Path(root)
    wiki_path = root_path / "wiki"
    log_path = root_path / "log"
    audit_path = root_path / "audit"

    if not wiki_path.exists():
        # **Su stdout**, e con un codice suo. Questa riga andava su stderr, che
        # ``wiki_lint`` non cattura: il builtin restituiva la stringa letterale
        # "No output" e un errore di battitura nel percorso
        # (``wiki_lint("wikis/typo")``) era indistinguibile da una wiki pulita —
        # l'unico esito che un lint non deve mai poter confondere.
        print(f"🔴 wiki/ directory not found at {wiki_path}")
        print("   (nothing was linted: this is not a clean wiki. Check the path —")
        print("    it must be the wiki root, the folder that *contains* wiki/.)")
        return EXIT_UNUSABLE

    pages = load_pages(wiki_path)
    all_wiki_files = list(wiki_path.rglob("*.md"))
    index_path = wiki_path / "index.md"

    # Il modo si **dice**. Prima era una decisione muta, e un controllo che
    # sparisce senza una riga è il difetto sotto tutti gli altri: chi legge vede
    # un output più corto e lo prende per una wiki più sana. Qui c'è anche il
    # perché — la pagina che ha deciso, o la loro assenza — così sistemarlo è
    # spostare una pagina, non indovinare una regola.
    research = research_pages(wiki_path)
    if research:
        where = research[0].parent.relative_to(wiki_path).as_posix()
        print(f"📐 Layout: research (a page lives under wiki/{where}/) — "
              "the flat-page checks (13-14) do not apply")
    else:
        subdirs = ", ".join(f"{name}/" for name in RESEARCH_SUBDIRS)
        print(f"📐 Layout: notebook (no page under {subdirs}) — "
              "the flat-page checks apply: every page declares `state:` and is linked")

    issues = 0

    # ── Pass 0: pagine che non sono UTF-8 ────────────────────────────────────
    # **Prima di tutti gli altri**, e non per gerarchia: le pagine guaste si
    # leggono da qui in poi con ``errors="replace"`` (v. ``read_md``), quindi chi
    # trova un ``�`` in un risultato più sotto deve aver già letto *quali*
    # file lo hanno messo lì. E il fatto è suo: l'iniettore legge in ``utf-8``
    # stretto e salta la pagina intera, ogni turno — un difetto muto che nessun
    # altro passo può vedere, perché la pagina *esiste*, è linkata e ha il suo
    # stato.
    undecodable = [
        (page.relative_to(root_path).as_posix(), why)
        for page in sorted(all_wiki_files)
        if (why := decode_problem(page)) is not None
    ]
    if undecodable:
        print(f"\n🔴 Pages that are not UTF-8 ({len(undecodable)}):")
        print_entries([f"   {rel} — {why}" for rel, why in undecodable])
        print("   (the project block reads a page as strict UTF-8: one that does not")
        print("    decode is skipped whole, every turn, so it is invisible to the")
        print("    model even though it exists and is linked. wiki/index.md is read")
        print("    leniently instead, so the map still arrives — with a replacement")
        print("    character where the bad byte was. Re-save as UTF-8.")
        print("    The checks below read them with replacement characters, so their")
        print("    findings on these files are usable but their text is not exact.)")
        issues += len(undecodable)

    # **L'identità di una pagina è il suo percorso, non il suo stem.** Queste
    # mappe erano indicizzate per ``p.stem``, quindi ``wiki/a/nota.md`` e
    # ``wiki/b/nota.md`` erano *una* pagina per il conteggio dei link entranti: un
    # link alla prima faceva passare la seconda per collegata, e la seconda non
    # compariva mai fra le orfane. Le chiavi sono ``Path``, gli stessi oggetti che
    # ``load_pages`` conserva e che ``page_for_link`` restituisce, quindi il
    # confronto è esatto e non serve nessuna seconda ricerca per stem: la
    # tolleranza sui nomi (maiuscole, slug, link a cartella) sta tutta nella
    # risoluzione, che è l'unico posto che deve conoscerla.
    inbound: dict[Path, list[str]] = defaultdict(list)

    # ── Pass 1: dead wikilinks ──────────────────────────────────────────────
    dead_links: list[tuple[str, str]] = []
    for md_file in all_wiki_files:
        text = read_md(md_file)
        for link in extract_wikilinks(text):
            link = link.strip()
            target = page_for_link(pages, link)
            if target is None:
                dead_links.append((str(md_file.relative_to(root_path)), link))
            else:
                inbound[target].append(md_file.stem)

    if dead_links:
        print(f"\n🔴 Dead wikilinks ({len(dead_links)}):")
        print_entries([f"   {source} → [[{link}]]" for source, link in dead_links])
        print("   (wikis are isolated — a link to another wiki's page is dead;")
        print("    reference other wikis through wikis/_index.md instead)")
        # Il ripiego per stem teneva in vita `[[raw/notes/x]]` perché *esisteva*
        # una pagina chiamata `x`: il lint taceva e il link era morto comunque.
        # Ora si segnala, quindi va detto anche come si aggiusta — altrimenti
        # sono venti righe senza rimedio, e un lint senza rimedio si ignora.
        print("   (a link only reaches pages under wiki/ — for raw/, log/ or audit/")
        print("    write the plain path instead of a [[wikilink]])")
        issues += len(dead_links)
    else:
        print("✅ No dead wikilinks")

    # ── Pass 2: orphan pages ────────────────────────────────────────────────
    skip_orphan = {"index"}
    orphans = [
        p for p in all_wiki_files
        if p not in inbound and p.stem not in skip_orphan
        and p.parent != wiki_path  # skip index.md at root
    ]
    if orphans:
        print(f"\n🟡 Orphan pages ({len(orphans)}) — no inbound wikilinks:")
        print_entries([f"   {p.relative_to(root_path)}" for p in orphans])
        issues += len(orphans)
    else:
        print("✅ No orphan pages")

    # ── Pass 3: missing index entries ───────────────────────────────────────
    if index_path.exists():
        index_text = read_md(index_path)
        # I link della mappa si risolvono, non si cercano come sottostringa.
        # Il confronto testuale sbagliava in entrambi i versi: `[[Semine]]` non
        # conteneva `[[semine]]`, e il nome della pagina che compare nella prosa
        # bastava a dichiararla indicizzata anche senza un solo link.
        indexed = {
            target
            for link in extract_wikilinks(index_text)
            if (target := page_for_link(pages, link)) is not None
        }
        not_in_index = [p for p in all_wiki_files if p != index_path and p not in indexed]
        if not_in_index:
            print(f"\n🟡 Pages missing from index.md ({len(not_in_index)}):")
            print_entries([f"   {p.relative_to(root_path)}" for p in not_in_index])
            issues += len(not_in_index)
        else:
            print("✅ All pages in index.md")
    else:
        print("⚠️  wiki/index.md not found — skipping index check")

    # ── Pass 4: unlinked concepts ───────────────────────────────────────────
    # Il conteggio passa da `extract_wikilinks`, come tutti gli altri passi: un
    # esempio di sintassi recintato non è una menzione. E **file per file**, non
    # su un'unica stringa concatenata, perché un blocco lasciato aperto in fondo
    # a una pagina si porterebbe via l'inizio della successiva.
    link_counts: dict[str, int] = defaultdict(int)
    for p in all_wiki_files:
        for link in extract_wikilinks(read_md(p)):
            link_counts[link.strip()] += 1

    missing_pages = [
        (link, count) for link, count in link_counts.items()
        if count >= 3 and page_for_link(pages, link) is None
    ]
    if missing_pages:
        print(f"\n🟡 Frequently linked but no page ({len(missing_pages)}):")
        print_entries([
            f"   [[{link}]] — mentioned {count}x"
            for link, count in sorted(missing_pages, key=lambda x: -x[1])
        ])
        issues += len(missing_pages)
    else:
        print("✅ No frequently-linked missing pages")

    # ── Pass 5: log/ shape ───────────────────────────────────────────────────
    if log_path.exists() and log_path.is_dir():
        log_issues: list[str] = []
        for p in sorted(log_path.iterdir()):
            if p.is_dir():
                continue
            if p.name == ".gitkeep":
                continue
            m = LOG_FILENAME_RE.match(p.name)
            if not m:
                log_issues.append(f"   {p.relative_to(root_path)} — filename doesn't match YYYYMMDD.md")
                continue
            y, mo, d = m.groups()
            iso = f"{y}-{mo}-{d}"
            lines = read_md(p).splitlines()
            first_line = lines[:1]
            if not first_line or first_line[0].strip() != f"# {iso}":
                log_issues.append(f"   {p.relative_to(root_path)} — expected H1 '# {iso}'")
            for line in lines:
                entry_m = LOG_ENTRY_RE.match(line)
                if entry_m and entry_m.group(1) not in VALID_LOG_OPS:
                    log_issues.append(
                        f"   {p.relative_to(root_path)} — unknown op '{entry_m.group(1)}' "
                        f"(expected one of {sorted(VALID_LOG_OPS)})"
                    )
        if log_issues:
            print(f"\n🟡 log/ shape issues ({len(log_issues)}):")
            print_entries(log_issues)
            issues += len(log_issues)
        else:
            print("✅ log/ shape OK")
    else:
        print("⚠️  log/ directory not found — skipping log shape check")

    # ── Pass 6: audit/ shape ─────────────────────────────────────────────────
    audit_targets_to_check: list[tuple[str, str]] = []  # (audit_id, target)
    if audit_path.exists() and audit_path.is_dir():
        audit_files = [
            p for p in audit_path.rglob("*.md") if p.name != ".gitkeep"
        ]
        audit_issues: list[str] = []
        seen_ids: dict[str, str] = {}
        for p in audit_files:
            text = read_md(p)
            fm = parse_frontmatter(text)
            rel = p.relative_to(root_path)
            if fm is None:
                audit_issues.append(f"   {rel} — missing YAML frontmatter")
                continue
            missing = AUDIT_REQUIRED_FIELDS - set(fm.keys())
            if missing:
                audit_issues.append(
                    f"   {rel} — missing fields: {', '.join(sorted(missing))}"
                )
                continue
            if not str(fm["source"]).strip():
                audit_issues.append(f"   {rel} — empty source field")
            # id must be unique and its timestamp prefix must match the filename.
            audit_id = str(fm["id"])
            if audit_id in seen_ids:
                audit_issues.append(
                    f"   {rel} — duplicate id '{audit_id}' (also in {seen_ids[audit_id]})"
                )
            else:
                seen_ids[audit_id] = str(rel)
            id_ts = AUDIT_TS_RE.match(audit_id)
            name_ts = AUDIT_TS_RE.match(p.name)
            if id_ts and name_ts and id_ts.group(1) != name_ts.group(1):
                audit_issues.append(
                    f"   {rel} — filename timestamp doesn't match id '{audit_id}'"
                )
            expected_status = "resolved" if "resolved" in p.parts else "open"
            if fm["status"] != expected_status:
                audit_issues.append(
                    f"   {rel} — status '{fm['status']}' doesn't match directory (expected '{expected_status}')"
                )
            if fm["status"] == "open":
                audit_targets_to_check.append((fm["id"], fm["target"]))

        if audit_issues:
            print(f"\n🔴 audit/ shape issues ({len(audit_issues)}):")
            print_entries(audit_issues)
            issues += len(audit_issues)
        else:
            print(f"✅ audit/ shape OK ({len(audit_files)} files)")
    else:
        print("⚠️  audit/ directory not found — skipping audit shape check")

    # ── Pass 7: audit targets exist ──────────────────────────────────────────
    missing_targets: list[tuple[str, str]] = []
    for audit_id, target in audit_targets_to_check:
        target_path = root_path / target
        # Audit target paths are relative to wiki-root but typically point
        # at files under wiki/. Check both locations.
        if not target_path.exists():
            alt = wiki_path / target
            if not alt.exists():
                missing_targets.append((audit_id, target))
    if missing_targets:
        print(f"\n🔴 Open audits with missing target files ({len(missing_targets)}):")
        print_entries([f"   {audit_id} → {target}" for audit_id, target in missing_targets])
        issues += len(missing_targets)
    elif audit_targets_to_check:
        print("✅ All open-audit targets exist")

    # ── Pass 8: possible duplicate pages ─────────────────────────────────────
    dup_groups: dict[str, list[Path]] = defaultdict(list)
    for p in all_wiki_files:
        if p.stem == "index":  # section/root indexes legitimately repeat
            continue
        key = dup_key(page_title(p))
        if key:
            dup_groups[key].append(p)
    dups = {k: v for k, v in dup_groups.items() if len(v) > 1}
    if dups:
        total = sum(len(v) - 1 for v in dups.values())
        print(f"\n🟡 Possible duplicate pages ({total}) — same normalized title:")
        # Il tetto conta le **righe**, gruppi inclusi: un gruppo tagliato a metà
        # sarebbe illeggibile, ma un elenco di gruppi che sfonda i 10 kB si porta
        # via il resto del report.
        grouped: list[str] = []
        for key, files in sorted(dups.items()):
            grouped.append(f"   ~ '{key}':")
            grouped.extend(f"       {f.relative_to(root_path)}" for f in sorted(files))
        print_entries(grouped)
        issues += total
    else:
        print("✅ No duplicate-looking pages")

    # Concept/entity pages (excluding folder-split index.md hubs) are the pages
    # for which `sources:` is a documented precondition, per article-guide.md.
    sourced_pages = [
        p for p in all_wiki_files
        if p.stem != "index"
        and p.relative_to(wiki_path).parts
        and p.relative_to(wiki_path).parts[0] in ("concepts", "entities")
    ]

    # ── Pass 9: source integrity ─────────────────────────────────────────────
    raw_path = root_path / "raw"
    no_sources_field = [
        p for p in sourced_pages
        if not [
            s for s in (parse_frontmatter(read_md(p)) or {}).get("sources", [])
            if str(s).strip()
        ]
    ]
    if no_sources_field:
        print(f"\n🔴 Concept/entity pages missing non-empty `sources:` frontmatter ({len(no_sources_field)}):")
        print_entries([f"   {p.relative_to(root_path)}" for p in no_sources_field])
        print("   (a precondition of writing the page, not later cleanup — see SKILL.md § Definition of done)")
        issues += len(no_sources_field)
    else:
        print("✅ All concept/entity pages have non-empty sources: frontmatter")

    if raw_path.is_dir():
        raw_stems = {f.stem for f in raw_path.rglob("*") if f.is_file()}
        missing_sources: list[tuple[str, str]] = []
        for p in all_wiki_files:
            fm = parse_frontmatter(read_md(p))
            if not fm:
                continue
            srcs = fm.get("sources")
            if not isinstance(srcs, list):
                continue
            for s in srcs:
                s = str(s).strip()
                if not s or ("<" in s and ">" in s):
                    continue
                if Path(s).stem not in raw_stems:
                    missing_sources.append((str(p.relative_to(root_path)), s))
        if missing_sources:
            print(f"\n🟡 Pages citing sources not found in raw/ ({len(missing_sources)}):")
            print_entries([f"   {src_page} → sources: {s}" for src_page, s in missing_sources])
            issues += len(missing_sources)
        else:
            print("✅ All cited sources resolve to raw/ files")
    else:
        print("⚠️  raw/ directory not found — skipping raw/ resolution check")

    # ── Pass 10: cross-link coverage ─────────────────────────────────────────
    # Per percorso, non per stem: v. il commento su ``inbound``, che qui costa
    # anche il verso opposto — due pagine omonime si prestavano i link *uscenti*.
    outbound_count: dict[Path, int] = {
        p: len(extract_wikilinks(read_md(p))) for p in all_wiki_files
    }
    inbound_non_index: dict[Path, list[str]] = defaultdict(list)
    for md_file in all_wiki_files:
        if md_file.stem == "index":
            continue
        for link in extract_wikilinks(read_md(md_file)):
            link = link.strip()
            target = page_for_link(pages, link)
            if target:
                inbound_non_index[target].append(md_file.stem)

    isolated_pages = [
        p for p in sourced_pages
        if outbound_count.get(p, 0) == 0 and not inbound_non_index.get(p)
    ]
    if isolated_pages:
        print(f"\n🟡 Concept/entity pages not cross-linked beyond index.md ({len(isolated_pages)}):")
        print_entries([f"   {p.relative_to(root_path)}" for p in isolated_pages])
        print("   (add a [[...]] link to/from another concept, entity or summary page —")
        print("    being listed in index.md alone doesn't count; see SKILL.md § Definition of done)")
        issues += len(isolated_pages)
    else:
        print("✅ All concept/entity pages are cross-linked beyond index.md")

    # ── Pass 11: summary completeness ────────────────────────────────────────
    # Every ingested text source (raw/articles, raw/papers, raw/notes) must have
    # a wiki/summaries/<slug>.md. raw/refs/ are pointer files, not ingestable
    # text, so they are exempt.
    if raw_path.is_dir():
        summary_stems = {
            p.stem for p in (wiki_path / "summaries").rglob("*.md")
        } if (wiki_path / "summaries").is_dir() else set()
        ingestable_dirs = ("articles", "papers", "notes")
        raw_sources = [
            f for f in raw_path.rglob("*.md")
            if f.is_file()
            and f.relative_to(raw_path).parts
            and f.relative_to(raw_path).parts[0] in ingestable_dirs
        ]
        missing_summaries = [f for f in raw_sources if f.stem not in summary_stems]
        if missing_summaries:
            print(f"\n🔴 Raw sources without a wiki/summaries/ page ({len(missing_summaries)}):")
            print_entries([
                f"   {f.relative_to(root_path)} → expected wiki/summaries/{f.stem}.md"
                for f in missing_summaries
            ])
            print("   (step 3 of ingest — a summary per source is not optional; see SKILL.md § Definition of done)")
            issues += len(missing_summaries)
        elif raw_sources:
            print("✅ Every raw source has a summary page")

    # ── Pass 12: il diario (ogni wiki) ───────────────────────────────────────
    # Universale, perché il diario è universale: ogni wiki l'ha guadagnato, e la
    # cattura scrive lì indipendentemente dal layout delle pagine.
    journals = journal_files(root_path)
    if journals:
        bad_names = [j for j in journals if not JOURNAL_FILENAME_RE.match(j.name)]
        if bad_names:
            print(f"\n🟡 Journal files with an unexpected name ({len(bad_names)}):")
            print_entries([
                f"   {j.relative_to(root_path)} — expected YYYYMMDD.md" for j in bad_names
            ])
            issues += len(bad_names)

        malformed: list[tuple[str, int, str]] = []
        unreadable: list[str] = []
        unreadable_head: set[str] = set()
        digests: dict[str, dict] = {}
        for j in journals:
            rel_j = j.relative_to(root_path).as_posix()
            try:
                raw = j.read_bytes()
            except OSError as exc:
                # Illeggibile è un fatto suo, e va detto. Prima era un
                # ``continue`` muto: il file usciva da ogni controllo — forma,
                # append-only, base — e l'output non se ne accorgeva.
                unreadable.append(f"{rel_j} — {exc.strerror or exc}")
                continue
            digests[rel_j] = {
                "sha": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                print(f"\n🔴 Journal file is not UTF-8: {j.relative_to(root_path)}")
                issues += 1
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if not JOURNAL_ENTRY_RE.match(stripped):
                    malformed.append((j.relative_to(root_path).as_posix(), n, stripped[:60]))
        if malformed:
            print(f"\n🟡 Journal lines not in `- HH:MM — text` form ({len(malformed)}):")
            print_entries([f"   {rel}:{n}  {sample}" for rel, n, sample in malformed])
            print("   (one fact per line, and the timestamp is added by the tool —")
            print("    a line the gardener cannot read is a fact that never becomes a page)")
            issues += len(malformed)

        # L'append-only **verificato**, non predicato. Il diario è l'input del
        # giardiniere e la sola fonte di verità di quel che è stato detto: se una
        # riga già promossa cambia, la pagina che ne è nata resta a dire un'altra
        # cosa e nessuno se ne accorge — il file è intatto e il cursore è
        # plausibile. Confrontare col run precedente è l'unico modo di vederlo.
        #
        # Il confronto è **esatto** e costa due letture: se il file è più corto
        # di prima è stato troncato; se è più lungo o uguale, la sua *testa*
        # (i primi ``size`` byte di allora) deve avere lo stesso digest di
        # allora. Un digest sul file intero non basterebbe: non distingue
        # "cresciuto" — che è il caso normale — da "riscritto".
        previous = read_lint_state(root_path)
        violations: list[tuple[str, str]] = []
        for rel, now in sorted(digests.items()):
            before = previous.get(rel)
            if not isinstance(before, dict):
                continue
            old_size, old_sha = before.get("size"), before.get("sha")
            if not isinstance(old_size, int) or not isinstance(old_sha, str):
                continue
            if now["size"] < old_size:
                violations.append((rel, f"truncated ({old_size} → {now['size']} bytes)"))
                continue
            head = head_digest(root_path / rel, old_size)
            if head is None:
                # ``None`` vuol dire «non ho potuto rileggerlo», non «è
                # cambiato»: dirlo come violazione manda a cercare una riga
                # riscritta dove il problema è un permesso o una corsa con chi
                # sta scrivendo. Due fatti diversi, due messaggi diversi.
                unreadable.append(f"{rel} — could not re-read its first {old_size} bytes")
                unreadable_head.add(rel)
            elif head != old_sha:
                violations.append((rel, "an already-written line was changed"))

        if unreadable:
            print(f"\n🟡 Journal files that could not be read ({len(unreadable)}):")
            print_entries([f"   {line}" for line in unreadable])
            print("   (a journal page nobody can read is a day of facts that never")
            print("    becomes anything — and the append-only check has nothing to")
            print("    compare, so this is not a clean bill of health)")
            issues += len(unreadable)

        if violations:
            print(f"\n🔴 Journal files that are no longer append-only ({len(violations)}):")
            print_entries([f"   {rel} — {why}" for rel, why in violations])
            print("   (the journal is the gardener's input and the only record of what")
            print("    was said. A page promoted from a line that no longer exists now")
            print("    says something nothing supports.)")
            issues += len(violations)
        elif previous:
            print("✅ Journal is append-only since the last lint")

        # Quel che si salva **non** è sempre quel che si è appena letto. Il file
        # che ha appena violato l'append-only tiene l'impronta di prima: se si
        # sovrascrivesse, il run dopo confronterebbe la versione alterata con se
        # stessa e direbbe ✅. È il lavaggio, e la sequenza che lo produce è
        # esattamente quella che SKILL.md prescrive — «lint, correggi, rilancia
        # finché è pulito»: run 1 la base, run 2 il 🔴, run 3 il verde su una
        # riga che nessuno ha rimesso a posto.
        #
        # **Perché tenere ferma l'impronta e non una lista ``violations`` nello
        # stato.** Una lista va poi dichiarata risolta, e il lint non ha un
        # comando per farlo: la voce resterebbe rossa per sempre, anche dopo che
        # la riga è stata rimessa com'era, e un rosso che non si può spegnere si
        # impara a ignorare. L'impronta ferma ottiene lo stesso — la violazione
        # si ripete a ogni run — e si spegne da sé nel solo caso in cui deve:
        # quando la testa del file torna a combaciare con la base, cioè quando
        # il cambiamento è stato annullato davvero.
        #
        # Le voci dei file che non ci sono più, o che oggi non si leggono,
        # **restano**. Qui si diverge di proposito dal cursore del giardiniere,
        # che pota quel che non esiste: un cursore è una posizione e senza il
        # file non vuol dire niente, una base è una prova, e potarla renderebbe
        # «cancella, rilancia, riscrivi» un modo di ripulire la fedina.
        stored = {
            rel: before
            for rel, before in previous.items()
            if isinstance(before, dict)
            and isinstance(before.get("size"), int)
            and isinstance(before.get("sha"), str)
        }
        stored.update(digests)
        for rel in {rel for rel, _ in violations} | unreadable_head:
            if rel in previous and isinstance(previous[rel], dict):
                stored[rel] = previous[rel]
        saved = write_lint_state(root_path, stored)

        # La base si annuncia **solo se è stata scritta davvero**. In un turno in
        # sola lettura la scrittura è rifiutata con un ``OSError``, e annunciarla
        # comunque vuol dire promettere un controllo che al run dopo non c'è.
        if not previous and saved:
            print("ℹ️  Journal baseline recorded (append-only checked from the next lint)")

    # ── Pass 13-14: il formato taccuino ──────────────────────────────────────
    if not research:
        # Ogni ``index.md`` è una mappa, non una pagina — non solo quello alla
        # radice. Una cartella con il suo hub è una forma prevista, e i passi
        # 8/9/10 esentano già `p.stem == "index"` da tutti i loro controlli:
        # chiedere ``state:`` a un hub voleva dire un 🔴 fisso per ogni
        # sottocartella, su una struttura che la skill stessa insegna.
        flat_pages = [p for p in all_wiki_files if p.stem != "index"]

        # ``state:`` è obbligatorio e chiuso a vocabolario. Una pagina vale
        # quanto il suo stato dice; senza, un'ipotesi appuntata di passaggio si
        # rilegge fra un mese come un fatto stabilito.
        #
        # Il confronto è **senza maiuscole**, come tre righe più in là quello sui
        # nomi delle pagine: `state: Open` è `open` scritto da qualcuno che ha
        # cominciato la riga con la maiuscola, non uno stato fuori vocabolario.
        # Normalizzare in scrittura non basterebbe — non tocca i file già su
        # disco, e questo è un controllo di salute, non un editor: il lint non
        # riscrive il materiale dell'utente.
        bad_state: list[tuple[str, str]] = []
        for page in flat_pages:
            fm = parse_frontmatter(read_md(page)) or {}
            value = str(fm.get("state", "")).strip()
            if value.lower() not in PAGE_STATES:
                bad_state.append((
                    page.relative_to(root_path).as_posix(), value or "(missing)"
                ))
        if bad_state:
            print(f"\n🔴 Pages with no valid `state:` ({len(bad_state)}):")
            print_entries([f"   {rel} — {value}" for rel, value in bad_state])
            print(f"   (one of: {', '.join(sorted(PAGE_STATES))})")
            issues += len(bad_state)
        elif flat_pages:
            print("✅ Every page declares a valid state:")

        # ── Pass 17: `source:`, cioè il fatto che la pagina sia verificabile ──
        #
        # Il campo lo prescrive ``templates/agent/gardener.md`` accanto a
        # ``state:``, e lo dice meglio di come lo direbbe un commento: è «il
        # sentiero da una pagina alla frase che l'ha causata, ed è quel che rende
        # una pagina sbagliata *correggibile* invece che solo sbagliata». Il lint
        # chiedeva ``state:`` e di ``source:`` non guardava niente.
        #
        # **Perché 🟡 e non 🔴.** Sono due difetti diversi. Una pagina senza
        # ``state:`` *dice una cosa falsa*: un'ipotesi appuntata di passaggio si
        # rilegge fra un mese come un fatto stabilito, e il rosso è per il
        # contenuto che inganna. Una pagina senza ``source:`` non dice niente di
        # falso: è **inverificabile**, che è un difetto del sentiero e non della
        # pagina. E c'è la ragione di campo, la stessa del passo 13 sulle
        # biblioteche: le pagine nate prima che il giardiniere scrivesse questo
        # campo non ce l'hanno, e un 🔴 su ognuna trasformerebbe progetti sani in
        # muri rossi — che è il modo più rapido di far ignorare un lint.
        #
        # **Perché due elenchi.** Un ``source:`` che nomina un file che non c'è
        # non è «manca il campo»: il sentiero è stato scritto e ora non porta da
        # nessuna parte, e la sua causa è un'altra — un giorno di diario potato o
        # rinominato, o un valore sbagliato. Il diario è append-only per
        # contratto, quindi un giorno svanito è a sua volta qualcosa da guardare
        # (``gardener.md`` lo elenca fra i casi da `FLAG:`). Due fatti, due
        # messaggi: dirli insieme manderebbe a cercare la cosa sbagliata.
        no_source: list[str] = []
        dangling_source: list[str] = []
        # Passo 19 (v. sotto il ciclo): quel che si raccoglie strada facendo, per
        # non rileggere ogni pagina e ogni giorno di diario una seconda volta.
        unattributed: list[str] = []
        inferred_but_decided: list[str] = []
        capped: list[str] = []
        capped_by_history = 0
        capped_by_document = 0
        journal_lines: dict[str, dict[str, str]] = {}
        for page in flat_pages:
            fm = parse_frontmatter(read_md(page)) or {}
            raw_value = fm.get("source")
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            values = [str(v).strip() for v in values if str(v or "").strip()]
            rel_page = page.relative_to(root_path).as_posix()
            state = str(fm.get("state", "")).strip().strip("\"'").lower()
            if not values:
                no_source.append(f"   {rel_page}")
                continue
            for value in values:
                # Un segnaposto (``<...>``) e un indirizzo web non sono file su
                # disco: il primo lo esenta già il passo 9, il secondo è una
                # fonte legittima che nessun controllo di percorso può risolvere.
                if "://" in value or ("<" in value and ">" in value):
                    continue
                # **L'ancoraggio non fa parte del percorso.** Da quando
                # ``gardener.md`` chiede l'ora della riga dopo un ``#``, un
                # ``source:`` giusto è ``raw/journal/20260824.md#19:19`` — e senza
                # questo taglio il controllo di esistenza qui sotto chiamerebbe
                # «dangling» ogni pagina ancorata bene, cioè trasformerebbe la
                # modifica che rende la provenienza verificabile in un muro giallo.
                file_part, _, anchor = value.partition("#")
                file_part = file_part.strip()
                if not (root_path / file_part).exists() and not (wiki_path / file_part).exists():
                    dangling_source.append(f"   {rel_page} → source: {value}")
                    continue
                if state not in _STATES_CLAIMING_A_DECISION:
                    # Il lato muto del passo 19: non si dichiara decisa, e con
                    # questa ``source:`` non potrà mai esserlo.
                    capped_by = _decided_cap_reason(root_path, file_part, anchor, journal_lines)
                    if capped_by:
                        reason, outcome = capped_by
                        if outcome == _CAP_FIXABLE:
                            capped.append(f"   {rel_page} (state: {state}) → {value} — {reason}")
                        elif outcome == _CAP_DOCUMENT:
                            capped_by_document += 1
                        else:
                            capped_by_history += 1
                    continue
                if not anchor:
                    unattributed.append(f"   {rel_page} → source: {value} (no #time)")
                    continue
                marker = _journal_line_marker(root_path, file_part, anchor, journal_lines)
                if marker in _SAID_MARKERS:
                    continue
                if marker == _INFERRED_MARKER:
                    inferred_but_decided.append(
                        f"   {rel_page} (state: {state}) → {value}"
                    )
                elif marker == _MIXED_MINUTE:
                    unattributed.append(
                        f"   {rel_page} → source: {value} "
                        "(that minute holds several lines and not all are the user's: "
                        "add the line's place within it, e.g. #HH:MM.2)"
                    )
                else:
                    unattributed.append(
                        f"   {rel_page} → source: {value} "
                        f"({'line not found' if marker is None else 'line not marked'})"
                    )
        if no_source:
            print(f"\n🟡 Pages with no `source:` ({len(no_source)}) — unverifiable:")
            print_entries(no_source)
            print("   (not wrong, but nothing says where it came from: `source:` is the")
            print("    trail back to the journal line that caused the page, and it is what")
            print("    makes a wrong page correctable. Add `source: raw/journal/<day>.md`.)")
            issues += len(no_source)
        if dangling_source:
            print(f"\n🟡 Pages whose `source:` names a file that is not there ({len(dangling_source)}):")
            print_entries(dangling_source)
            print("   (a different finding from a missing source: the trail was written and")
            print("    now leads nowhere — a journal day pruned or renamed, or a wrong value.")
            print("    The journal is append-only, so a day that vanished is worth a look too.)")
            issues += len(dangling_source)

        # ── Pass 19: chi si dichiara deciso, su parole di chi? ────────────────
        #
        # Il difetto (**D1**): il 24/08 una pagina è nata `state: decided` su un
        # fatto che l'utente non aveva detto — era l'opzione B di una domanda che
        # l'assistente aveva fatto lui — ed è finita sotto «Decided» nella mappa,
        # che entra in ogni turno. In scrittura ora c'è una guardia
        # (`gardener._provenance_guard`), ma quella agisce **solo** sulle
        # scritture: le pagine già sul disco non le vede nessuno. Questo passo è
        # il lato offline, e non dipende da nessun modello.
        #
        # **Due elenchi e due severità, e la riga di confine è quella del passo
        # 17.** Una riga marcata `[inferred]` con una pagina che si dichiara
        # decisa è 🔴: il diario stesso dice che l'ha concluso l'assistente,
        # quindi la pagina *dice una cosa falsa* — l'utente non ha deciso quello.
        # Tutto il resto — riga senza marcatore, `source:` senza ora, ora che non
        # risolve — è 🟡: **inverificabile**, non falso. Ed è anche la ragione di
        # campo che il passo 17 spiega: le pagine nate prima che i marcatori
        # esistessero non ce l'hanno, e un 🔴 su ognuna trasformerebbe otto wiki
        # sane in muri rossi, che è il modo più rapido di far ignorare un lint.
        if inferred_but_decided:
            print(f"\n🔴 Pages claiming a decision the journal attributes to the assistant "
                  f"({len(inferred_but_decided)}):")
            print_entries(inferred_but_decided)
            print("   (the journal line is `[inferred]` — the assistant concluded it, the user")
            print("    did not say it. An answer to a question the assistant asked is not the")
            print("    user's statement. Set the page to `state: open` and put the question in")
            print("    the map's open section.)")
            issues += len(inferred_but_decided)
        if unattributed:
            print(f"\n🟡 Pages claiming a decision on a line nobody attributed "
                  f"({len(unattributed)}):")
            print_entries(unattributed)
            print("   (not wrong, unverifiable: nothing says whether the user stated this or")
            print("    the assistant concluded it. Anchor `source:` at the line's own time")
            print("    (`raw/journal/<day>.md#HH:MM`); a line written before the markers")
            print("    existed cannot be attributed at all, and `open` is what it is worth.)")
            issues += len(unattributed)
        if capped:
            print(f"\nℹ️  Pages that could not be marked `decided`, and whose `source:` can be "
                  f"fixed ({len(capped)}):")
            print_entries(capped)
            print("   (not counted as defects: `open` is what these are worth today. Said here")
            print("    because the write guard speaks only when a pass tries to promote one, so")
            print("    a page nobody attempts stays capped without anyone knowing.)")
        if capped_by_history:
            print(f"\nℹ️  {capped_by_history} more page(s) rest on a journal line that cannot be "
                  "attributed — written before the markers existed, or `[inferred]`. Not listed "
                  "and not counted: `open` is the right value and no edit changes it.")
        if capped_by_document:
            # Frase separata da quella di ``capped_by_history`` perché la
            # situazione è un'altra: qui non c'è niente di vecchio e niente di
            # sbagliato — ``project.md`` chiede *esattamente* questa forma per il
            # materiale che arriva da fuori. Detto comunque perché altrimenti un
            # progetto alimentato da documenti non ha modo di sapere che
            # ``decided`` lì è irraggiungibile: su ``salute`` (26/08) sono cinque
            # pagine su cinque.
            print(f"\nℹ️  {capped_by_document} more page(s) rest on a document copied into "
                  "`raw/` rather than on a journal line — the shape `project.md` asks for when "
                  "material arrives from outside. Nothing there attributes the fact to the user, "
                  "so `open` is what they are worth; not counted, and there is nothing to fix.")

        # Una pagina che non linka niente è una nota in una cartella, non una
        # voce di wiki: la stessa regola del passo 10, per le pagine piatte.
        unlinked = [
            page for page in flat_pages
            if not extract_wikilinks(read_md(page))
            and not inbound_non_index.get(page)
        ]
        if unlinked:
            print(f"\n🟡 Pages with no link in or out ({len(unlinked)}):")
            print_entries([f"   {page.relative_to(root_path)}" for page in unlinked])
            print("   (being listed in the map is not a link — that is what makes")
            print("    this a wiki instead of a folder)")
            issues += len(unlinked)

    # ── Pass 15: la mappa entra in ogni turno ────────────────────────────────
    # Vale per tutti i layout: il blocco di progetto inietta ``wiki/index.md`` in
    # ogni turno di ogni conversazione dentro quella cartella, quindi ogni riga
    # in più si paga a ogni messaggio — e oltre il tetto il resto non arriva.
    if index_path.exists():
        try:
            size = len(index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            # Come il gemello nel passo 16: illeggibile è un altro problema e ha
            # già il suo passo (0 per l'encoding). Qui conta solo che non porti
            # via i passi che vengono dopo.
            size = 0
        if size > MAP_MAX_CHARS:
            print(f"\n🟡 The map is {size} characters (over {MAP_MAX_CHARS}):")
            print(f"   {index_path.relative_to(root_path)}")
            print("   (it is injected into every turn, and past that ceiling the rest")
            print("    is not injected at all. What outgrew a few lines belongs on a page.)")
            issues += 1

    # ── Pass 16: una pagina troppo lunga non entra affatto ───────────────────
    # Il gemello del passo 15, e vale per tutti i layout perché il blocco di
    # progetto inietta le pagine di ogni progetto. La differenza con la mappa è
    # tutta nel dopo-soglia: la mappa entra troncata, una pagina no — nessuna
    # pagina entra a metà, quindi oltre il tetto viene saltata intera. L'insieme
    # è quello che l'iniettore guarda, e la regola sta in :func:`is_injected_page`
    # — la copia dichiarata di ``wiki_paths.is_wiki_page_rel`` (T9.5).
    oversized: list[tuple[str, int]] = []
    for page in sorted(all_wiki_files):
        if not is_injected_page(page.relative_to(wiki_path)):
            continue
        try:
            # ``strip()`` come l'iniettore: il conto deve essere lo stesso, o il
            # lint discute di un numero che il prompt non usa.
            size = len(page.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            continue  # illeggibile è un altro problema, non questo
        if size > PAGE_MAX_CHARS:
            oversized.append((page.relative_to(root_path).as_posix(), size))
    if oversized:
        print(f"\n🟡 Pages too long to be injected at all ({len(oversized)}):")
        print_entries([
            f"   {rel} — {size} characters (over {PAGE_MAX_CHARS})"
            for rel, size in sorted(oversized, key=lambda item: -item[1])
        ])
        print("   (a page past that ceiling is skipped whole, every turn, in every")
        print("    conversation in this project — and the order is alphabetical, so no")
        print("    question can call it up. Split it along the things it talks about:")
        print("    one page per thing, each linking to the others.)")
        issues += len(oversized)

    # ── Pass 18: l'elenco di pagine dentro AGENTS.md ─────────────────────────
    #
    # Il terzo della famiglia dei passi 15-16, e l'unico che non guarda una
    # pagina: `AGENTS.md` entra **intero** in ogni turno di questo progetto — è
    # la sola superficie di lettura senza tetto e senza curatore (T7.10) — e
    # quel che ci si accumula dentro è un elenco di pagine, cioè il mestiere di
    # `wiki/index.md`.
    #
    # **Perché 🟡 e non 🔴**, con l'argomento del passo 17. Il rosso è per il
    # contenuto che *inganna*: una pagina senza `state:` si rilegge fra un mese
    # come un fatto stabilito. Un elenco duplicato non dice niente di falso —
    # dice due volte una cosa vera, e si paga a ogni messaggio. È un difetto di
    # collocazione, non di verità, come `source:` era un difetto del sentiero.
    # E c'è la ragione di campo, che qui è più forte che altrove: l'elenco lo
    # prescrive `references/schema-guide.md` («Maintained article list», «after
    # every new concept page: add to Current articles»), quindi i file che lo
    # hanno ce l'hanno per aver seguito le istruzioni. Un 🔴 su una wiki che ha
    # obbedito alla skill è il modo più rapido di far ignorare il lint.
    #
    # La cosa che *sarebbe* rossa esiste, e non è questa: una voce che nomina
    # una pagina cancellata è un'affermazione falsa. Ma nessun passo la vede —
    # il passo 1 cammina `wiki/**` e `AGENTS.md` sta fuori — e quello è metà
    # dell'argomento per spostare l'elenco invece di controllarlo qui: nella
    # mappa quei link li controlla già il passo 1.
    schema_file = root_path / "AGENTS.md"
    if schema_file.is_file():
        # Come i passi 15-16 il conto è in caratteri, ma **non** via ``read_md``:
        # quello toglie il BOM, e l'iniettore no — il budget di un turno lo paga.
        try:
            schema_text = schema_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            schema_text = ""
        entries = page_list_lines(schema_text)
        listed = sum(len(line) + 1 for line in entries)
        if listed > AGENTS_LIST_MAX_CHARS:
            total = len(schema_text)
            share = round(100 * listed / total) if total else 0
            print(
                f"\n🟡 AGENTS.md carries a page list ({listed} of {total} characters, "
                f"{share}% — {len(entries)} entries):"
            )
            print(f"   {schema_file.relative_to(root_path)}")
            print("   (this file is injected whole into every turn of this project, with no")
            print("    ceiling — while the map it duplicates, wiki/index.md, is cut at")
            print(f"    {MAP_MAX_CHARS} characters. A list of pages is what the map is for: that is")
            print("    the file the gardener curates and the one the checks above read, so an")
            print("    index here is a second one that nobody keeps and no check looks at —")
            print("    an entry naming a page that was deleted stays there, silently. Nothing")
            print("    here is wrong; it is paid for twice. Move the entries into")
            print("    wiki/index.md and leave AGENTS.md the scope, the conventions and the")
            print("    open questions — and do NOT trim the tail instead: that is where the")
            print("    open questions live, and it is the only part nothing else carries.)")
            issues += 1

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    if issues == 0:
        print("✅ Wiki is healthy — no issues found")
    else:
        print(f"⚠️  {issues} issue(s) found — review above and fix before next ingest")

    return EXIT_OK if issues == 0 else EXIT_ISSUES


def lint_workspace(wikis_dir_path: str, fix: bool = False) -> int:
    """Lint every wiki under <wikis-dir>, then check the workspace registry.

    With fix=True, registry drift is repaired in place (only the mechanical
    _index.md registry block — per-wiki issues always need human judgement)."""
    # Su **stdout** e con il codice dell'input inutilizzabile, per la stessa
    # ragione del ramo gemello in ``lint()``: chi cattura solo stdout vedeva un
    # report vuoto e non un errore.
    wikis_dir = reindex_wikis.resolve_wikis_dir(wikis_dir_path)
    if not wikis_dir.is_dir():
        print(f"🔴 wikis dir not found at {wikis_dir} — nothing was linted")
        return EXIT_UNUSABLE

    wikis = reindex_wikis.discover_wikis(wikis_dir)
    if not wikis:
        print(f"🔴 no wikis found under {wikis_dir} — nothing was linted")
        return EXIT_UNUSABLE

    failed = 0
    for name in wikis:
        print(f"\n{'='*50}\n📚 {name}\n{'='*50}")
        # **Una wiki rotta costa una wiki.** Senza questo ``except`` una sola
        # eccezione in ``lint`` — un file che non decodifica, un permesso, un
        # ``.jenny/`` che è un file — portava via *tutte* le wiki dopo di essa,
        # e in ordine alfabetico: la prima cartella malandata nascondeva le
        # altre sette. Il ``BaseException`` non si prende (Ctrl-C e
        # ``ReadOnlyTurnError`` sotto un turno in sola lettura devono uscire).
        try:
            code = lint(str(wikis_dir / name))
        except Exception as exc:  # noqa: BLE001 — vedi sopra: isolamento per wiki
            print(f"\n🔴 the lint crashed on this wiki: {type(exc).__name__}: {exc}")
            print("   (the other wikis below were still linted — this one was not,")
            print("    so it is neither clean nor a known set of issues)")
            failed += 1
            continue
        if code != EXIT_OK:
            failed += 1

    # ── Workspace pass: _index.md registry sync ──────────────────────────────
    print(f"\n{'='*50}\n🗂  workspace registry (_index.md)\n{'='*50}")
    # Stessa ragione, ultimo anello: ``check_index`` legge ``_index.md`` in
    # ``utf-8`` stretto (``reindex_wikis.py``, che è un checkout dell'utente e
    # non si tocca da qui). Se scoppia lì, il report di ogni wiki — già
    # calcolato — sparisce dal buffer di chi cattura stdout.
    try:
        registry_problems = reindex_wikis.check_index(wikis_dir)
    except Exception as exc:  # noqa: BLE001 — vedi sopra
        print(f"🔴 the registry check crashed: {type(exc).__name__}: {exc}")
        print(f"   ({wikis_dir / reindex_wikis.INDEX_FILENAME} could not be read —")
        print("    the per-wiki reports above stand, the registry was not checked)")
        print(f"\n{'─'*40}")
        print(f"⚠️  {failed} wiki(s) with issues + registry unchecked")
        return EXIT_ISSUES
    if registry_problems and fix:
        print(f"🔧 registry out of sync — repairing ({len(registry_problems)}):")
        print_entries([f"   {p}" for p in registry_problems])
        reindex_wikis.regenerate_index(wikis_dir)
        registry_problems = reindex_wikis.check_index(wikis_dir)
        if registry_problems:
            print("🔴 still out of sync after repair:")
            print_entries([f"   {p}" for p in registry_problems])
        else:
            print("✅ registry repaired")
    elif registry_problems:
        print(f"🔴 wikis/_index.md out of sync ({len(registry_problems)}):")
        print_entries([f"   {p}" for p in registry_problems])
        print("   (re-run with --fix, or: reindex_wikis.py <wikis-dir>)")
    else:
        print("✅ wikis/_index.md registry is in sync")

    print(f"\n{'─'*40}")
    total_bad = failed + (1 if registry_problems else 0)
    if total_bad == 0:
        print(f"✅ Workspace healthy — {len(wikis)} wiki(s), registry in sync")
        return EXIT_OK
    print(f"⚠️  {failed} wiki(s) with issues" +
          (" + registry out of sync" if registry_problems else ""))
    return EXIT_ISSUES


if __name__ == "__main__":
    args = sys.argv[1:]
    fix = "--fix" in args
    args = [a for a in args if a != "--fix"]
    # Un'invocazione sbagliata è input inutilizzabile come una root senza
    # ``wiki/``: stesso codice, così «non ho controllato niente» ha un solo
    # numero in tutto lo script.
    if args and args[0] == "--workspace":
        if len(args) < 2:
            print(__doc__)
            sys.exit(EXIT_UNUSABLE)
        sys.exit(lint_workspace(args[1], fix=fix))
    if fix:
        print("--fix only applies to --workspace mode")
        sys.exit(EXIT_UNUSABLE)
    if not args:
        print(__doc__)
        sys.exit(EXIT_UNUSABLE)
    sys.exit(lint(args[0]))

