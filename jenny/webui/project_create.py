"""Creazione di un progetto dalla WebUI: una wiki nuova, completa e vuota.

**Un progetto e' una wiki**: non esiste una
`projects/` separata, quindi "nuovo progetto" vuol dire scaffoldare
`wikis/<nome>/` e registrarlo in `wikis/_index.md`. Prima di questo modulo il
chip creava una cartella nuda con `/api/workspace/mkdir`: nessun albero, nessun
file di istruzioni, nessuna voce nel registro — una wiki rotta che sembrava un
progetto.

**Lo scaffolder e' `project_scaffold.py`, nel package** *(dal 22/08, passo T1 del
piano del taccuino)*. Fino a quel giorno era `scaffold.py` della skill,
per non avere due scaffolder che divergono; ora sono due di proposito, perche'
costruiscono **due formati diversi**: la skill fa la biblioteca di ricerca
(`raw/papers`, `concepts|entities|summaries`, le cinque operazioni), e resta la
strada per crearne una chiedendola a Jenny; il picker della UI fa un progetto —
pagine piatte, un diario, la mappa. Il confine e' netto e nessun file e'
conteso.

**La riga dell'utente non e' un extra.** *"Quando crei il progetto devi scrivere
tu qualcosa, sennò la chat è ferma"*: senza una riga di scope il primo turno non
ha su cosa appoggiarsi, e uno scope indovinato dall'agente e' peggio di nessuno
scope, perche' tutto quel che viene archiviato dopo lo eredita. Finisce in due
posti — il `summary:` dell'`AGENTS.md`, da cui `reindex_wikis` costruisce la riga
del registro, e la mappa del progetto, che e' quel che l'agente legge per primo —
e ci entra **alla nascita**, non per sostituzione di un segnaposto dopo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.session.keys import project_session_key
from jenny.session.project_traces import (
    delete_project_traces,
    describe_project_traces,
    recorded_wiki_id,
)
from jenny.utils.errors import CodedError
from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import (
    FRONTMATTER_RE,
    LEGACY_WIKI_SCHEMA_FILENAME,
    WIKI_INDEX_FILENAME,
    WIKI_SCHEMA_FILENAME,
    is_wiki_root,
)
from jenny.webui.project_scaffold import scaffold_project
from jenny.webui.wiki_registry import refresh_wiki_registry

_TITLE_SPLIT_RE = re.compile(r"[-_.]+")

# La riga di scope dentro la frontmatter (``wiki_paths.FRONTMATTER_RE``). Regex e non YAML per
# la stessa ragione di ``wiki_paths.wiki_id``: un due punti nel testo libero rende
# il blocco non parsabile, e ``yaml.safe_load`` non perde *quella* riga, perde
# tutte le altre. Qui va toccata una riga sola e riscritto il resto byte per
# byte, che un round-trip di ``yaml.dump`` non sa fare (riordina le chiavi,
# normalizza le virgolette, perde i commenti).
_SUMMARY_LINE_RE = re.compile(r"^summary:[ \t]*(.*)$", re.M)


class ProjectCreateError(CodedError):
    """Creazione non riuscita, con un messaggio mostrabile all'utente."""

    def __init__(self, message: str, *, code: str = "bad_request") -> None:
        super().__init__(code, message)


def project_title(name: str) -> str:
    """Titolo leggibile da un nome di cartella: ``diario-ricette`` -> ``Diario Ricette``.

    Il titolo finisce negli H1 del template e non e' un identificatore: l'utente
    lo puo' correggere nell'`AGENTS.md`, il nome della cartella no (e' l'indirizzo
    della wiki). Per questo lo deriviamo invece di chiederlo: un campo in piu' nel
    dialogo per un dato che si cambia dopo.
    """
    words = [w for w in _TITLE_SPLIT_RE.split(name) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or name


def _yaml_scalar(value: str) -> str:
    """*value* come scalare YAML su una riga, sempre valido.

    Perche' quotare: la riga di scope e' testo libero dell'utente e finisce
    *dentro* la frontmatter. Un due punti in mezzo — "Prova del passo 7: la chat
    segue" — rende il blocco YAML non parsabile, e allora si perde **tutta** la
    frontmatter, non solo quella riga. Visto sul telefono il 22/08 su una wiki
    appena creata: ``read_wiki_scope`` cadeva sul ripiego e l'id risultava assente.

    Non si usa ``yaml.dump``: quello aggiunge il documento e a volte manda a
    capo. Qui basta la regola delle virgolette doppie — raddoppiare i backslash,
    scappare le virgolette — che rende sicuro qualunque testo, due punti e
    cancelletti compresi.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _is_complete_project(root: Path) -> bool:
    """Vero se *root* e' un progetto **finito**, cioe' da non toccare piu'.

    Il marcatore e' la **mappa** (``wiki/index.md``), e non il file di
    istruzioni. La ragione e' la migrazione dell'avvio
    (``utils/wiki_migration.py``): a ogni boot ogni cartella che contiene
    ``wiki/`` si prende un ``AGENTS.md`` minimo se non ce l'ha. Con quello come
    marcatore, un albero rimasto a meta' — la ``wiki/`` c'e', il resto no —
    risulterebbe "completo" dal riavvio successivo, e ci resterebbe per sempre
    con la riga di scope a segnaposto: cioe' esattamente il progetto che il
    picker elenca e che non si puo' finire.

    ``wiki/index.md`` invece non lo scrive nessun passaggio automatico: lo
    scrive uno scaffolder, il nostro o quello della skill — i due formati
    differiscono in tutto tranne che nella mappa — quindi la sua presenza vuol
    dire che un progetto e' nato davvero. Ed e' anche il file a cui punta il
    wikilink del registro: senza, la voce in ``wikis/_index.md`` e' un link
    morto.
    """
    if (root / "wiki" / WIKI_INDEX_FILENAME).is_file():
        return True
    # Una wiki scritta a mano prima del passo 7 tiene le istruzioni in
    # ``CLAUDE.md``. Lo scaffolder non lo vede e le scriverebbe accanto un
    # ``AGENTS.md``: e' lo stato che la migrazione si rifiuta di risolvere
    # («ha sia CLAUDE.md sia AGENTS.md, non tocco niente»). Una cartella cosi'
    # e' roba dell'utente, non un albero a meta'.
    return (root / LEGACY_WIKI_SCHEMA_FILENAME).is_file()


def _seed_scope_if_placeholder(root: Path, quoted_seed: str) -> bool:
    """Scrive la riga di scope in un ``AGENTS.md`` che ha solo il segnaposto.

    Serve al ripasso su un albero rimasto a meta'. Se l'avvio ha gia' scritto il
    suo ``AGENTS.md`` minimo (``wiki_migration._ensure_id``, ``summary:`` a
    segnaposto), lo scaffolder lo lascia stare — e' la regola che rende sicuro
    rilanciarlo — e la riga che l'utente ha appena scritto si perderebbe in
    silenzio: il progetto sarebbe completo e senza scopo, che e' la cosa per cui
    la riga viene chiesta.

    Uno ``summary:`` **vero** non si tocca: completare un progetto non e'
    riscriverne lo scope. Ritorna True se ha scritto.
    """
    schema = root / WIKI_SCHEMA_FILENAME
    try:
        text = schema.read_text(encoding="utf-8")
    except OSError:
        return False
    block = FRONTMATTER_RE.match(text)
    if block is None:
        return False
    raw = block.group(1)
    line = _SUMMARY_LINE_RE.search(raw)
    if line is None:
        # Frontmatter senza ``summary:``: la riga si aggiunge in fondo al blocco,
        # che e' l'unico posto in cui non spezza niente di quel che c'e'.
        updated = f"{raw.rstrip(chr(10))}\nsummary: {quoted_seed}"
    else:
        # Stessa regola di ``wiki_paths._is_placeholder``, piu' il caso della
        # chiave vuota: entrambi vogliono dire "scope da riempire".
        value = line.group(1).strip().strip('"').strip("'")
        if value and not ("<" in value and ">" in value):
            return False
        updated = f"{raw[: line.start()]}summary: {quoted_seed}{raw[line.end() :]}"
    atomic_write(schema, f"{text[: block.start(1)]}{updated}{text[block.end(1) :]}")
    logger.info("project {}: scope line written into {}", root.name, WIKI_SCHEMA_FILENAME)
    return True


def create_project(
    *,
    wikis_dir: Path,
    scripts_dir: Path,
    name: str,
    seed: str,
    workspace: Path | None = None,
    conversation: str = "refuse",
) -> dict[str, Any]:
    """Scaffolda `wikis_dir/name`, ci scrive la riga di scope, aggiorna il registro.

    Sincrona e con I/O: il chiamante la mette in un thread. Il nome arriva gia'
    validato — questo modulo non e' il gate.

    **Su una cartella che esiste gia' rifiuta in due modi diversi**, e la
    differenza e' quel che il client mostra all'utente. Un progetto completo e'
    un "ce l'hai gia'"; una cartella che non e' un progetto e' un "c'e' qualcosa
    di mezzo", che si risolve rinominando. In mezzo ai due c'e' il caso per cui
    lo scaffolder e' scritto a top-up: l'albero a meta' — la ``wiki/`` c'e' (la
    crea per prima di proposito, cosi' resta visibile al picker) e il resto no,
    perche' la creazione e' morta a meta'. Prima di questo, quella cartella era
    irrecuperabile: elencata dal picker, senza mappa, e su un telefono l'utente
    non ha modo di ripararla a mano.

    **E su un nome che ha ancora una conversazione non decide: chiede.**
    *workspace* serve a saperlo (le tracce di una chat stanno fuori da
    ``wikis/``) e *conversation* a rispondere:

    - ``refuse``, il default — non tocca niente e torna con
      ``status: "conversation_exists"`` e il conto di cosa c'e'. Non e' un
      errore: e' una domanda, e sollevarla la renderebbe indistinguibile da un
      guasto.
    - ``discard`` — toglie le tracce, poi crea. Il progetto nasce con la chat
      vuota, che e' quel che «progetto nuovo» di solito vuol dire.
    - ``keep`` — crea **adottando l'id della wiki che quella chat ricorda**, cosi'
      la conversazione riprende ed e' davvero sua. Senza l'adozione il primo
      turno verrebbe rifiutato per discordanza di identita', giustamente.

    Senza *workspace* il controllo non si fa e il comportamento e' quello di
    prima: e' il default dei chiamanti che non hanno una radice da dare (i test
    che scaffoldano e basta), non una scorciatoia per la produzione.
    """
    root = wikis_dir / name
    if root.exists():
        if _is_complete_project(root):
            raise ProjectCreateError(f"project already exists: {name}")
        if not is_wiki_root(root):
            raise ProjectCreateError(
                f"a folder named {name} is in the way: it exists but is not a project"
            )
        logger.info("project {}: half-built tree, completing it instead of refusing", name)

    # **Un nome libero di cartella puo' non essere un nome libero.** Le tracce di
    # una conversazione stanno fuori da ``wikis/`` (v.
    # ``session/project_traces.py``), quindi un nome che qui sembra vergine puo'
    # portarsi dietro la chat di un progetto cancellato — che e' come il difetto
    # del 24/08/2026 arrivava all'utente: progetto nuovo, memoria di un altro.
    #
    # **Una domanda non e' un errore.** Le due risposte sono entrambe legittime
    # — «l'avevo cancellato per sbaglio, ridammela» e «riparto pulito» — e
    # indovinare vuol dire sbagliarne una in silenzio. Quindi non si solleva: si
    # torna con uno stato che il client sa trasformare in una scelta, e il
    # disco non si tocca.
    adopt_id: str | None = None
    if workspace is not None and not root.exists():
        key = project_session_key(name)
        traces = describe_project_traces(workspace, key)
        if traces.exists:
            if conversation == "refuse":
                return {
                    "status": "conversation_exists",
                    "name": name,
                    "conversation": {
                        "files": traces.files,
                        "bytes": traces.bytes,
                        "messages": traces.messages,
                    },
                }
            if conversation == "discard":
                delete_project_traces(workspace, key)
            elif conversation == "keep":
                # Riprendere la chat vuol dire dire che questo *e'* quel
                # progetto, e la forma in cui lo si dice e' il suo id: senza,
                # il primo turno verrebbe rifiutato per discordanza.
                adopt_id = recorded_wiki_id(workspace, key)
            else:
                raise ProjectCreateError(f"unknown conversation choice: {conversation}")

    try:
        created = scaffold_project(
            root, project_title(name), seed, _yaml_scalar(seed), adopt_id=adopt_id
        )
    except Exception as exc:
        raise ProjectCreateError(f"scaffold failed: {exc}") from exc

    # Il seme: scritto dallo scaffolder quando l'``AGENTS.md`` nasce adesso,
    # applicato a mano quando il file c'era gia' col segnaposto dell'avvio.
    seeded = WIKI_SCHEMA_FILENAME in created or _seed_scope_if_placeholder(
        root, _yaml_scalar(seed)
    )

    # Il registro lo scrive il chiamante e non lo scaffolder: quello conosce una
    # cartella, questo conosce `wikis_dir`. `reindex_wikis` resta della skill —
    # e' il registro del *workspace*, comune ai due formati, e non c'e' niente da
    # duplicare.
    registry = refresh_wiki_registry(wikis_dir, scripts_dir)

    return {
        "status": "created",
        "name": name,
        "created": list(created or []),
        # La riga di scope entra alla nascita, quindi su un progetto nuovo e'
        # sempre `True`. Su un albero completato puo' essere `False`: se
        # l'``AGENTS.md`` c'era gia' con uno scope **vero** — la creazione era
        # morta fra quel file e la mappa — quello resta, e la riga di stavolta
        # finisce solo nella mappa. Il campo dice quel che e' successo invece di
        # dichiarare sempre riuscita una cosa che non sempre lo e'.
        "seeded": seeded,
        "registry": registry,
    }
