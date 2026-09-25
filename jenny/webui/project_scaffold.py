"""Lo scaffolder dei progetti: la forma con cui una wiki nasce dalla UI.

Passo **T1** di ``roadmap/taccuino-passi.md``. Un progetto nuovo nasce con
questo albero:

    <progetto>/
      AGENTS.md            le istruzioni di *questo* progetto (id, summary)
      wiki/
        index.md           la mappa: di cosa si tratta, deciso, aperto, le pagine
      raw/journal/         la cattura della conversazione, una pagina al giorno
      raw/research/        l'ingest: quel che arriva da fuori, verbatim
      log/                 una riga per operazione
      audit/               il canale di correzione umana

**Pagine piatte sotto ``wiki/``, e nessuna tassonomia obbligatoria.** Non
esistono ``concepts/``, ``entities/``, ``summaries/``: erano la tassonomia del
pattern document-first (una knowledge base di ricerca che digerisce paper), e
obbligano a scegliere «concept o entity?» *nel momento peggiore*, cioe' mentre
si prende un appunto. Le sottocartelle non sono vietate — sono libere, si aprono
quando un gruppo di pagine se le guadagna. I ``[[link]]`` restano nudi:
``webui/wiki.py::resolve_wikilink`` risolve per stem in tutto ``wiki/``, quindi
spostare una pagina in una sottocartella non rompe niente.

**Non e' lo scaffolder della skill, e non lo sostituisce.**
``skills/llm-wiki/scripts/scaffold.py`` costruisce l'altro formato — la
biblioteca di ricerca, con ``raw/papers`` e le cinque operazioni — vive nel
checkout che l'utente puo' modificare, e resta la strada per creare una wiki di
ricerca (si chiede a Jenny). Questo vive nel package, ha i suoi test nel repo, ed
e' quel che il picker della UI usa. Due formati nel mondo, confine netto, nessun
file conteso: la differenza fra i due la dice la **struttura su disco**, e nessun
consumatore ha bisogno di un'etichetta per leggerla.

**La riga dell'utente entra alla nascita, quotata.** Lo scaffolder della skill
scrive un segnaposto che il chiamante sostituisce dopo; qui il seme e' un
argomento, quindi non esiste la finestra in cui il file contiene un placeholder
al posto di un dato. Quotato perche' finisce *dentro* la frontmatter: un due
punti nel testo libero rende il blocco YAML non parsabile, e allora si perde
**tutta** la frontmatter, non solo quella riga (visto sul telefono il 22/08).

**Scrive solo quel che manca.** Ogni file e' scritto se assente e lasciato stare
se c'e', come il top-up della skill: e' la regola che rende sicuro rilanciarlo su
una cartella a metà, ed e' anche il motivo per cui ritorna l'elenco di quel che
ha creato davvero.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from loguru import logger

from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import (
    JOURNAL_DIRNAME,
    WIKI_INDEX_FILENAME,
    WIKI_SCHEMA_FILENAME,
    new_wiki_id,
)

# Le cartelle dell'albero. ``wiki/`` per prima perche' e' quella che rende la
# cartella una wiki per tutti i lettori (``is_wiki_root``): se un giorno lo
# scaffold morisse a metà, meglio che il pezzo già scritto sia visibile al
# picker che invisibile.
#
# **Questa è la definizione unica della forma di un progetto**, e ha una copia
# fuori dal pacchetto: ``jenny/skills/llm-wiki/scripts/scaffold.py`` la ripete
# come ``_COMMON_DIRS`` perche' quello e' un checkout modificabile dall'utente e
# non puo' importare ``jenny`` (stessa ragione della regola di slug copiata in
# ``lint_wiki.py``). A tenerle allineate non c'e' la buona volontà: c'e'
# ``tests/skills/llm_wiki/test_scaffold_topup.py::
# test_the_two_scaffolders_cannot_diverge``, che importa questa tupla e la
# confronta — la prossima divergenza è un test rosso, non un difetto silenzioso
# scoperto sul telefono (era: i due scaffolder disaccordavano sul diario, cioè
# sull'unica cartella che il ramo chiama universale).
PROJECT_DIRS: tuple[str, ...] = (
    "wiki",
    JOURNAL_DIRNAME,
    "raw/research",
    "log",
    "audit",
    "audit/resolved",
)

# ``.gitkeep`` solo dove serve davvero: ``audit/`` e' l'unica coppia di cartelle
# che nasce vuota e resta vuota a lungo. ``wiki/`` ha l'indice, ``log/`` la voce
# di oggi, e ``raw/`` si riempie appena si lavora.
_GITKEEP_DIRS: tuple[str, ...] = ("audit", "audit/resolved")


def _write_if_absent(root: Path, rel: str, content: str) -> bool:
    """Scrive ``root/rel`` se non c'e'. True se l'ha scritto."""
    target = root / rel
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, content)
    return True


def _agents_md(title: str, seed: str, wiki_id: str, quoted_seed: str) -> str:
    """``AGENTS.md``: quel che di questo progetto e' vero e di nessun altro.

    Niente convenzioni generali, niente pianta delle cartelle, niente regole di
    lavoro: le dice ``agent/project.md``, che sta nel prompt di sistema e si
    riscrive a ogni avvio. Copiarle qui vorrebbe dire congelarle alla versione in
    cui il progetto e' nato — l'errore che il 22/08 ha tolto da questo file.

    **``## How we work here`` nasce vuota, e il 24/08 non lo era.** Ci stava un
    segnaposto fra parentesi angolari che spiegava cosa scriverci: era l'ultima
    istanza rimasta dell'errore che il paragrafo qui sopra dice di aver tolto.
    Quel testo e' un'istruzione a chi compila il file, ma il file finisce nel
    prompt di sistema di ogni turno del progetto — e ci finisce **intero**, perche'
    ``id:`` e ``summary:`` sono gia' compilati e quindi ``_is_template_content``
    non lo riconosce come template. Cioe' arrivava al modello sotto l'intestazione
    «this project's own instructions», congelato alla versione del giorno in cui il
    progetto e' nato, e in un file che il giardiniere ha il divieto esplicito di
    riscrivere.

    E non lascia scoperta nessuna promessa: la stessa istruzione la porta
    ``agent/project.md``, viva, alla riga di ``AGENTS.md`` — «this project's own
    instructions: scope, conventions, what it deliberately excludes … keep it
    current when the answer to "how we work here" changes. It is yours to edit».

    L'intestazione **resta**: e' il posto, e dice dove si scrive senza dire cosa.
    Misurato il 25/08 sulle otto wiki del dispositivo, il campione utile e' uno —
    le altre sette sono di un formato che precede questo scaffold e quel
    segnaposto non l'hanno mai avuto — e in quell'uno era sopravvissuto a una
    giornata, una conversazione e una passata del giardiniere.

    ``summary:`` non e' decorazione: e' il campo da cui ``read_wiki_scope``
    costruisce la riga del registro. ``id:`` e' l'identita' della wiki, e serve a
    ritrovare la propria chat se la cartella cambia nome (passo 7): nasce qui
    perche' un progetto creato oggi non deve aspettare la migrazione del
    prossimo avvio.
    """
    return f"""---
id: {wiki_id}
summary: {quoted_seed}
---

# {title}

## What this is

{seed}

## How we work here
"""


def _index_md(title: str, seed: str) -> str:
    """``wiki/index.md``: **la mappa**, e il posto da cui parte ogni risposta.

    Le quattro sezioni nascono vuote ma nascono: il giardiniere (T4) aggiorna
    sezioni che esistono invece di inventarsi una struttura ogni volta, che e' il
    modo in cui due sessioni diverse producono due mappe diverse.

    Piccola **per costituzione**, non per buona volonta': entra nel prompt di
    ogni turno (T3), quindi ogni riga qui e' un costo per messaggio. Se cresce
    oltre una schermata sta assorbendo contenuto che spetta alle pagine — la
    riga di avviso in fondo lo dice a chi la legge, ed e' quel che il lint
    misurera' (T5).

    Il diario e' citato come **percorso e non come ``[[link]]``**: sta fuori da
    ``wiki/``, e un wikilink che punta fuori dalle pagine e' un link morto per
    ``resolve_wikilink`` e per il lint.
    """
    return f"""---
title: {title}
---

# {title}

> {seed}

## Decided

*(nothing yet)*

## Open

*(nothing yet)*

## Pages

*(none yet)*

---

Working journal: `{JOURNAL_DIRNAME}/` — one file per day, append-only.

<!-- This map is read on every turn: keep it to one screen. When a section
     outgrows a few lines, that content belongs on its own page under wiki/. -->
"""


def _log_md(title: str, day: date, at: str, created: list[str]) -> str:
    bullets = "".join(f"- Created {c}\n" for c in created)
    return f"# {day.isoformat()}\n\n## [{at}] scaffold | Started {title}\n{bullets}"


def scaffold_project(
    root: Path,
    title: str,
    seed: str,
    quoted_seed: str,
    *,
    today: date | None = None,
    adopt_id: str | None = None,
) -> list[str]:
    """Crea quel che manca sotto *root*. Ritorna i percorsi creati, relativi.

    *seed* e' la riga dell'utente su cos'e' il progetto, in chiaro; *quoted_seed*
    la stessa riga resa scalare YAML dal chiamante (che e' l'unico posto in cui
    quella regola di quoting vive — v. ``project_create._yaml_scalar``).

    *today* esiste per i test: il default e' la data di oggi, e nessun chiamante
    di produzione lo passa.

    *adopt_id* fa nascere la wiki con un id **gia' esistente** invece di uno
    nuovo. Serve a un caso solo: l'utente ricrea un progetto il cui nome porta
    ancora una conversazione e dice di volerla riprendere. Dire "questo *e'*
    quel progetto" e ridargli il suo id sono la stessa frase, e senza di essa la
    chat ripresa verrebbe rifiutata al primo turno da
    ``AgentLoop._refuse_reincarnated_project`` — giustamente, perche' fino a
    quel momento sarebbe la chat di un'altra wiki.
    """
    day = today or date.today()
    created: list[str] = []

    for rel in PROJECT_DIRS:
        d = root / rel
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            created.append(f"{rel}/")

    for rel in _GITKEEP_DIRS:
        if _write_if_absent(root, f"{rel}/.gitkeep", ""):
            created.append(f"{rel}/.gitkeep")

    if _write_if_absent(root, WIKI_SCHEMA_FILENAME, _agents_md(
        title, seed, adopt_id or new_wiki_id(), quoted_seed
    )):
        created.append(WIKI_SCHEMA_FILENAME)

    # Il nome della mappa viene da ``wiki_paths``, non da un letterale qui
    # (T6.13): **questo e' il modulo che il file lo crea**, e gli altri cinque
    # lettori danno per buono il nome che sceglie. Una copia qui sarebbe la copia
    # peggiore delle sei — la mappa nascerebbe con un nome che il blocco di
    # progetto non apre e che l'elenco delle pagine non esclude, e la si vedrebbe
    # solo dal fatto che il prompt non porta piu' la mappa.
    map_rel = f"wiki/{WIKI_INDEX_FILENAME}"
    if _write_if_absent(root, map_rel, _index_md(title, seed)):
        created.append(map_rel)

    # Il log per ultimo: la sua voce elenca quel che questo giro ha creato
    # davvero, e per saperlo devono essere passati tutti gli altri file. Se il
    # log di oggi c'e' gia' resta intatto e non si appende niente — «non toccare
    # quel che c'e'» vince sulla completezza della voce, ed e' la regola che
    # rende sicuro rilanciare lo scaffold.
    if created:
        log_rel = f"log/{day.strftime('%Y%m%d')}.md"
        at = datetime.now().strftime("%H:%M")
        if _write_if_absent(root, log_rel, _log_md(title, day, at, created)):
            created.append(log_rel)

    logger.info("scaffolded project {}: {}", root.name, ", ".join(created) or "nothing to do")
    return created
