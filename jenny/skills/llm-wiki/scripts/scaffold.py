#!/usr/bin/env python3
"""
scaffold.py — Create an LLM Wiki directory structure, or top up an existing one.

Usage:
    python3 scaffold.py <wiki-root> "<Topic Title>"

Example:
    python3 scaffold.py workspace/wikis/ai-research "AI Research"

The canonical layout puts every wiki under a workspace's wikis/ directory, so
<wiki-root> is normally wikis/<name>. After creating the wiki, this script
registers it in wikis/_index.md via reindex_wikis.regenerate_index().

Safe to re-run on a wiki that already exists: every file is written only when it
is absent, so nothing already on disk is rewritten. That makes this the way to
add what a wiki is missing after the scaffold has drifted — it creates the gaps,
leaves the rest byte-identical, and reports what it added.

**On a folder that is already a notebook project** — flat pages under wiki/, no
page under concepts/entities/summaries — it tops up *that* shape instead: the
journal and the plain tree, not the research taxonomy. Creating the taxonomy
there used to flip the linter's layout mode and silently switch off the `state:`
check, i.e. re-running the scaffolder "safely" removed an invariant.

Creates:
    <wiki-root>/
    ├── AGENTS.md          (this wiki's scope and notes)
    ├── log/
    │   └── YYYYMMDD.md    (first day's log with scaffold entry)
    ├── audit/
    │   ├── .gitkeep
    │   └── resolved/
    │       └── .gitkeep
    ├── raw/
    │   ├── journal/       (the conversation, one day per file — every wiki has one)
    │   ├── articles/
    │   ├── papers/
    │   ├── notes/
    │   └── refs/
    ├── wiki/
    │   ├── index.md       (category-structured catalog)
    │   ├── concepts/
    │   ├── entities/
    │   └── summaries/
    └── outputs/
        └── queries/

``raw/journal/`` is in both shapes because the journal is universal: the lint
checks it in every layout and the capture writes there whatever the pages look
like. What a project has in common with a research library is defined **once**,
in ``jenny/webui/project_scaffold.py::PROJECT_DIRS``, and copied here (see
``_COMMON_DIRS``) because this checkout cannot import ``jenny``.
"""

import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lint_wiki  # noqa: E402  (sibling script, same scripts/ dir)
import reindex_wikis  # noqa: E402  (sibling script, same scripts/ dir)

# ── Un solo posto dice com'e' fatto un progetto ───────────────────────────────
#
# Quel che **ogni** wiki ha, qualunque sia il suo formato. Due scaffolder
# esistono — questo e ``jenny/webui/project_scaffold.py`` — e fino al 23/08
# costruivano due alberi diversi: solo quello nel package creava
# ``raw/journal/``, mentre SKILL.md, il lint (passo 12) e il giardiniere danno il
# diario per universale. Una wiki di ricerca nata da qui non aveva il posto dove
# la cattura scrive.
#
# **Copia deliberata** di ``project_scaffold.py::PROJECT_DIRS``, stessa ragione
# di ``_VALID_WIKI_NAME`` qui sotto: questi script sono un checkout della skill,
# copiato nel workspace e modificabile dall'utente, e non possono importare
# ``jenny``. L'originale resta quello nel package — la definizione unica di
# com'e' fatto un progetto — e ``tests/skills/llm_wiki/test_scaffold_topup.py``
# confronta le due liste, cosi' la prossima divergenza e' un test che cade e non
# una wiki storta sul telefono.
#
# ``wiki`` per prima come nell'originale: e' quel che rende la cartella una wiki
# per tutti i lettori (``is_wiki_root``), e se lo scaffold morisse a meta' meglio
# che il pezzo scritto sia visibile al picker.
_COMMON_DIRS: tuple[str, ...] = (
    "wiki",
    "raw/journal",
    "log",
    "audit",
    "audit/resolved",
)

# L'albero di un **progetto-taccuino**: pagine piatte, un diario, la mappa. Non
# serve a *creare* un taccuino (lo fa il picker della UI, ed e' il confine fra i
# due scaffolder): serve al top-up, per aggiungere quel che manca **nella forma
# che quel progetto ha davvero**.
_NOTEBOOK_DIRS: tuple[str, ...] = _COMMON_DIRS + ("raw/research",)

# Quel che la **biblioteca di ricerca** aggiunge al comune: la tassonomia sotto
# ``wiki/``, le fonti per tipo sotto ``raw/``, gli output delle interrogazioni.
_RESEARCH_ONLY_DIRS: tuple[str, ...] = (
    "raw/articles",
    "raw/papers",
    "raw/notes",
    "raw/refs",
    "wiki/concepts",
    "wiki/entities",
    "wiki/summaries",
    "outputs/queries",
)

_RESEARCH_DIRS: tuple[str, ...] = _COMMON_DIRS + _RESEARCH_ONLY_DIRS


def _is_existing_notebook(root: str) -> bool:
    """Vero se sotto ``root`` c'e' **gia'** un progetto-taccuino: il diario, delle
    pagine, e nessuna pagina sotto ``concepts/``/``entities/``/``summaries/``.

    La parte sulle pagine e' la regola del lint, e viene da lui apposta
    (``lint_wiki.is_research_layout``): lo scaffolder e il controllore non devono
    poter dissentire sul formato, perche' e' esattamente il dissenso che ha
    prodotto il difetto. Il lint decide dalle **pagine**, quindi le tre cartelle
    vuote che questo script creava non spostano piu' il modo — ma crearle
    resterebbe comunque sbagliato: sono l'invito a usare un formato che quel
    progetto non usa.

    Le tre condizioni servono **tutte e tre**, e le due in piu' non sono
    prudenza: sono i due modi in cui questa funzione sbaglierebbe verso la
    ricerca, che e' il verso sicuro (l'albero in piu' e' rumore, l'albero in meno
    e' una wiki che resta rotta).

    - Senza pagine non e' un taccuino, e' una cartella vuota: la wiki che nasce
      adesso non ha niente sotto ``wiki/`` e deve prendere l'albero di ricerca
      intero, che e' il motivo per cui si chiama questo script.
    - Senza ``raw/journal/`` non e' un taccuino nemmeno se le pagine sono piatte:
      una biblioteca di ricerca con la tassonomia ancora vuota — la deriva
      misurata su ``patreon-creator``, che e' proprio il caso che si viene a
      riparare — ha esattamente quella forma, e negarle ``outputs/queries`` la
      lascerebbe rotta. Sul telefono il diario ce l'hanno tutte (lo crea la
      migrazione all'avvio), quindi da solo non distingue niente: qui conta la
      sua **assenza**, che dice "questa cartella non e' un progetto".
    - Se c'e' una cartella che **solo** la ricerca usa (``raw/papers/``,
      ``outputs/queries/``, la tassonomia...) la cartella si e' gia' dichiarata,
      e vince su tutto il resto. Questa condizione e' nuova e non e' prudenza: da
      quando anche l'albero di ricerca crea ``raw/journal/`` (v. ``_COMMON_DIRS``,
      ed e' giusto — il diario e' universale) il diario da solo non puo' piu'
      dire "questa cartella non e' un progetto", e senza questa riga il *secondo*
      top-up su una biblioteca con la tassonomia vuota la leggerebbe come un
      taccuino, cioe' il difetto di prima al giro dopo.
    """
    root_path = Path(root)
    wiki = root_path / "wiki"
    if not wiki.is_dir() or not (root_path / "raw" / "journal").is_dir():
        return False
    if any((root_path / d).is_dir() for d in _RESEARCH_ONLY_DIRS):
        return False
    if not any(p.is_file() for p in wiki.rglob("*.md")):
        return False
    return not lint_wiki.is_research_layout(wiki)


def scaffold(root: str, title: str) -> list[str]:
    """Crea quel che manca sotto ``root``, senza toccare quel che c'e' gia'.

    Ritorna i percorsi creati, relativi a ``root``: su una wiki completa e' una
    lista vuota, ed e' anche il report che viene stampato.
    """
    # Distingue "wiki nuova" da "top-up" per una cosa sola: come si intitola la
    # voce di log. Il comportamento sui file e' lo stesso nei due casi.
    root_existed = os.path.isdir(root)
    created: list[str] = []
    # I percorsi dove c'e' *qualcosa* della forma sbagliata: una cartella dove va
    # un file, o un file dove va una cartella. Non si toccano e non si contano fra
    # i creati — si dicono, perche' un run che li ignora dichiara successo su una
    # wiki che il lint non riesce nemmeno a leggere.
    collisions: list[str] = []

    _warn_if_unopenable(root)

    today = date.today()
    today_iso = today.isoformat()
    today_compact = today.strftime("%Y%m%d")
    now_hm = datetime.now().strftime("%H:%M")

    # Il formato non lo si impone: lo si legge. Su un progetto-taccuino questo
    # script aggiungeva ``wiki/concepts``, ``wiki/entities``, ``wiki/summaries``
    # — cioe' la tassonomia di un formato che quel progetto non usa, e finche' il
    # lint decideva il modo dalle cartelle bastava a spegnergli il controllo su
    # ``state:``. SKILL.md dice che rilanciare questo script e' sicuro, e questa
    # riga e' cio' che lo rende vero.
    notebook = _is_existing_notebook(root)
    dirs = list(_NOTEBOOK_DIRS if notebook else _RESEARCH_DIRS)
    if notebook:
        print(
            # ``resolve()`` come in ``_warn_if_unopenable``: chiamato con "." il
            # nome sarebbe vuoto, e il report direbbe "/".
            f"· {Path(root).resolve().name}/ is already a notebook project (flat pages "
            "under wiki/): topping up that shape — no concepts/, entities/ or summaries/"
        )

    new_dirs: list[str] = []
    for d in dirs:
        full = os.path.join(root, d)
        if os.path.isdir(full):
            continue
        if os.path.exists(full):
            # Un *file* dove va una cartella: ``makedirs`` qui esplodeva con un
            # ``FileExistsError`` a metà scaffold, lasciando la wiki peggio di
            # come l'ha trovata e senza dire perché.
            _note_collision(collisions, f"{d}/")
            continue
        new_dirs.append(d)
    for d in new_dirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    created.extend(f"{d}/" for d in new_dirs)
    if new_dirs:
        print(f"✓ Created under {root}/: " + ", ".join(f"{d}/" for d in new_dirs))
    else:
        print(f"· Directory tree already complete under {root}/")

    # .gitkeep for empty audit dirs
    for keep in ("audit/.gitkeep", "audit/resolved/.gitkeep"):
        if _write(root, keep, "", collisions):
            created.append(keep)

    # AGENTS.md — le istruzioni di *questa* wiki, e basta.
    #
    # Fino al 22/08 qui c'era uno schema da 2,5 kB: convenzioni di naming,
    # mermaid e KaTeX, policy sui raw, le cinque operazioni. Roba vera, ma
    # identica in ogni wiki e ricopiata in ognuna — quindi ferma alla versione
    # in cui la wiki e' stata creata. Ora la dice `agent/project.md`, che sta nel
    # prompt di sistema e si riscrive a ogni avvio. Qui resta solo cio' che di
    # questa wiki e' vero e di nessun'altra.
    #
    # `summary:` e la riga "What this wiki covers:" non sono decorazione: sono
    # esattamente cio' che `read_wiki_scope` cerca per comporre `_index.md`.
    # `id:` e' l'identita' della wiki, e serve a una cosa sola: ritrovare la
    # propria chat se un giorno la cartella cambia nome (passo 7). Non e' un
    # indirizzo e non finisce in nessun nome di file. Nasce qui perche' una wiki
    # creata oggi non deve aspettare la migrazione del prossimo avvio;
    # `secrets` e non `uuid` per la stessa forma da 12 esadecimali che legge
    # `utils/wiki_paths.py::wiki_id`.
    wiki_id = __import__("secrets").token_hex(6)
    agents_md = f"""---
id: {wiki_id}
summary: <one-line scope — shown next to this wiki in wikis/_index.md>
---

# {title}

## Scope

What this wiki covers:
- <describe the topic area>

What this wiki deliberately excludes:
- <describe out-of-scope areas>

## Notes

<Anything true of this wiki and no other: sources to prefer, conventions that
depart from the default, open questions. The folder layout and the five
operations are not written here — the agent already has them.>
"""
    # Non lo si crea accanto a un `CLAUDE.md`. Questo script gira anche in
    # top-up su wiki vere, e una wiki non ancora migrata quel file ce l'ha col
    # nome vecchio: aggiungerne un secondo alla radice produrrebbe di proposito
    # lo stato che la migrazione (`utils/wiki_migration.py`) si rifiuta di
    # risolvere — e la migrazione, al prossimo avvio, lo rinomina da se'.
    legacy = os.path.isfile(os.path.join(root, "CLAUDE.md"))
    if not legacy and _write(root, "AGENTS.md", agents_md, collisions):
        created.append("AGENTS.md")
        print("✓ Created AGENTS.md")
    elif legacy:
        print("· CLAUDE.md still there — the next start renames it, left as it is")
    elif "AGENTS.md" in collisions:
        print("🔴 AGENTS.md is not a file — see the collisions below")
    else:
        print("· AGENTS.md already there — left as it is")

    # wiki/index.md — la mappa, nella forma del progetto che si sta toccando. Su
    # un taccuino le sezioni della tassonomia sarebbero tre inviti a un formato
    # che quel progetto non usa, in cima al file che l'agente legge per primo.
    # Il caso non e' teorico: un progetto con le pagine e senza mappa e' un
    # albero rimasto a meta', ed e' proprio quello che si viene a riparare.
    if notebook:
        index_md = f"""# {title}

> One-sentence scope of the project.

## Decided

*(nothing yet)*

## Open

*(nothing yet)*

## Pages

*(none yet)*

---

Working journal: `raw/journal/` — one file per day, append-only.
"""
    else:
        index_md = f"""# Index — {title}

> One-sentence scope of the wiki.

## 🔖 Navigation
- [[#Concepts]] · [[#Entities]] · [[#Summaries]] · [[#Open Questions]]

## Concepts

*(none yet)*

## Entities

*(none yet)*

## Summaries (chronological)

*(none yet)*

## Open Questions

- <First research question>
"""
    if _write(root, "wiki/index.md", index_md, collisions):
        created.append("wiki/index.md")
        print("✓ Created wiki/index.md")
    elif "wiki/index.md" in collisions:
        print("🔴 wiki/index.md is not a file — see the collisions below")
    else:
        print("· wiki/index.md already there — left as it is")

    # log/<today>.md — per ultimo, perche' la sua voce elenca quel che questo run
    # ha creato davvero, e per saperlo devono essere passati tutti gli altri file.
    # Se il log di oggi c'e' gia', resta intatto: la voce non viene appesa. La
    # skill chiede di loggare ogni operazione, ma "non toccare quel che c'e'" e'
    # la regola che rende un top-up sicuro su una wiki vera, e vince su questa.
    # Il report finisce su stdout, che e' cio' che l'agente legge comunque.
    log_rel = f"log/{today_compact}.md"
    if created:
        if root_existed:
            headline = f"Topped up {title} scaffolding"
            bullets = "".join(f"- Created {c}\n" for c in created)
        else:
            headline = f"Initialized {title} knowledge base"
            bullets = (
                "- Created directory tree (raw/, wiki/, log/, audit/, outputs/)\n"
                "- Created AGENTS.md with the wiki's scope\n"
                "- Created wiki/index.md category skeleton\n"
            )
        log_md = f"# {today_iso}\n\n## [{now_hm}] scaffold | {headline}\n{bullets}"
        if _write(root, log_rel, log_md, collisions):
            created.append(log_rel)
            print(f"✓ Created {log_rel}")
        elif log_rel in collisions:
            print(f"🔴 {log_rel} is not a file — see the collisions below")
        else:
            print(f"· {log_rel} already there — left as it is, nothing appended")

    # Register this wiki in the workspace registry (wikis/_index.md).
    wikis_dir = Path(root).resolve().parent
    if wikis_dir.name != "wikis":
        print(
            f"⚠️  Parent dir is '{wikis_dir.name}', not 'wikis' — the canonical "
            f"layout is wikis/<name>. Registering in {wikis_dir}/_index.md anyway.",
            file=sys.stderr,
        )
    index_path = reindex_wikis.regenerate_index(wikis_dir)
    print(f"✓ Registered in {index_path}")

    # Le collisioni **prima** del verdetto, e il verdetto le tiene in conto: un
    # "✅ Nothing to add" su una cartella chiamata ``wiki/index.md`` era il modo
    # di dichiarare sana una wiki che il lint non riesce nemmeno ad aprire.
    if collisions:
        print("\n🔴 Wrong kind of thing in the way — nothing was written there:")
        for rel in collisions:
            what = "a file where a directory belongs" if rel.endswith("/") else \
                   "a directory where a file belongs"
            print(f"   {rel} — {what}")
        print("   (this is not «already there»: the wiki stays broken while it stands,")
        print("    and the lint dies on it. Move or remove it by hand, then re-run.)")

    if created:
        verb = "scaffolded" if not root_existed else "topped up"
        added = "".join(f"  + {c}\n" for c in created)
        mark = "✅" if not collisions else "·"
        print(f"\n{mark} Wiki {verb} at: {root}/\n\nAdded:\n{added}")

    if collisions:
        print(f"⚠️  {root}/ is still incomplete: {len(collisions)} path(s) above are the "
              "wrong kind of thing. Fix those, then re-run.")
    elif not created:
        print(f"\n✅ Nothing to add — {root}/ already has the whole scaffolding.")
    elif not root_existed:
        print("""Next steps:
  1. Fill in AGENTS.md — define what this wiki covers and what it excludes
  2. Add sources to raw/ (copy articles/papers/notes into raw/<subfolder>/)
  3. Run ingest: tell your LLM agent "ingest raw/<file>.md"
  4. Ask questions: "what does the wiki say about X?"
""")

    print(f"""Run these through python_exec, with working_dir="<workspace>/skills/llm-wiki/scripts":
       lint:               import lint_wiki; lint_wiki.lint({root!r})
       feedback:           import audit_review; audit_review.main({root!r}, 'open')
       whole workspace:    import lint_wiki; lint_wiki.lint_workspace({str(wikis_dir)!r})
""")

    return created


# La forma di un nome di progetto. **Copia deliberata** di
# ``jenny/session/keys.py::is_valid_project_name``, per la stessa ragione per cui
# ``utils/wiki_paths.py::read_wiki_scope`` ricopia la logica di
# ``reindex_wikis``: questi script sono un checkout della skill, copiato nel
# workspace e modificabile dall'utente, non una libreria del package — non
# possono importare ``jenny``. Se la regex canonica cambia, cambia anche questa.
_VALID_WIKI_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _warn_if_unopenable(root: str) -> bool:
    """Avvisa se il nome della cartella non potra' essere una chat. True = avvisato.

    Il nome di una cartella sotto ``wikis/`` e' anche il nome di una sessione
    (``project:<nome>``), e la sessione la puo' aprire solo un nome che passa la
    regex qui sopra: ``Ricerca ETF``, ``universita``-con-l'accento,
    ``progetto (2026)`` no. Una wiki con un nome cosi' funziona come wiki —
    ingest, lint, grafo — ma la sua chat non si apre: il chip non la offre, e un
    frame che la nomina viene rifiutato.

    **Avvisa e continua, non rifiuta.** Questo script gira anche in top-up su
    wiki che esistono, e una wiki con il nome sbagliato e' proprio quella che ha
    piu' bisogno di essere riparata: negarle lo scaffolding la lascerebbe rotta
    due volte. Il rinomino non lo facciamo noi — sposterebbe sotto i piedi una
    cartella che potrebbe avere una chat, un id e un cursore altrove.
    """
    name = Path(root).resolve().name
    if _VALID_WIKI_NAME.match(name) and ".." not in name:
        return False
    print(
        f"⚠️  '{name}' cannot be a project chat name: use letters, numbers, dot, dash "
        f"and underscore only (max 64, first character a letter or digit). The wiki "
        f"itself works, but it will not appear in the chat scope chip and messages "
        f"addressed to it are refused. Rename the folder to fix it.",
        file=sys.stderr,
    )
    return True


def _write(root: str, path: str, content: str, collisions: list[str] | None = None) -> bool:
    """Scrive ``content`` solo se il file non c'e'. True se l'ha creato.

    Prima questa funzione era ``open(full, "w")`` secco: rilanciare lo scaffold
    su una wiki esistente per "aggiungere quel che manca" ne azzerava
    ``wiki/index.md`` e riscriveva il log di oggi. Il confine sta qui, in un
    punto solo, e non in ognuno dei chiamanti.

    **«Esiste» non basta: deve essere un file.** Il test era ``os.path.exists``,
    che dice si' anche a una *cartella* chiamata ``AGENTS.md`` o
    ``wiki/index.md``: il report diceva "already there — left as it is" e il run
    si dichiarava riuscito su una wiki rotta, dove il lint poi muore con
    ``IsADirectoryError``. Una collisione cosi' non la si ripara indovinando —
    spostare o cancellare roba dell'utente non e' compito di uno scaffolder — ma
    dirla e' il minimo: finisce in *collisions* e il chiamante la riporta.
    """
    full = os.path.join(root, path)
    if os.path.isfile(full):
        return False
    if os.path.exists(full):
        _note_collision(collisions, path)
        return False
    # E la stessa domanda sul **genitore**: con un *file* chiamato ``log``,
    # ``makedirs`` moriva qui con un ``FileExistsError`` a metà scaffold — la
    # wiki peggio di come l'aveva trovata, e nessuna riga a dire perché.
    parent_full = os.path.dirname(full)
    if parent_full and os.path.exists(parent_full) and not os.path.isdir(parent_full):
        _note_collision(collisions, f"{os.path.dirname(path)}/")
        return False
    os.makedirs(parent_full or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def _note_collision(collisions: list[str] | None, rel: str) -> None:
    """Registra *rel* una volta sola: la stessa cartella la incontrano sia il
    giro sull'albero sia i ``_write`` che ci vogliono scrivere dentro, e dirla
    due volte farebbe contare due problemi per uno."""
    if collisions is not None and rel not in collisions:
        collisions.append(rel)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    scaffold(sys.argv[1], sys.argv[2])

