"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from jenny.utils.helpers import ensure_dir


def set_workspace_dir(path: str | Path) -> None:
    """Set the workspace root explicitly.

    The workspace is the writable directory that holds config, templates,
    skills, memory, and runtime subdirectories. On Android it is provided
    from Java/Kotlin (e.g. ``context.getFilesDir()/workspace``). Lo stato vive
    nel ``RuntimeContext`` (unica fonte di verità).
    """
    from jenny.runtime.context import get_runtime_context
    from jenny.utils.prompt_templates import forget_templates_root

    # **Risolto qui, una volta, e non ai punti d'uso.** Su Android la cartella
    # dati è raggiungibile con due nomi — Java passa ``/data/user/0/<pkg>`` e
    # ``resolve()`` la riscrive in ``/data/data/<pkg>`` — e le due forme non
    # combaciano come prefisso. Il 26/08 quello è costato l'esecuzione del lint
    # dentro un progetto: la guardia di ``python_exec`` confronta il percorso
    # dell'operazione col confine (che è risolto), e con le due radici in forme
    # diverse rifiutava anche uno ``stat`` legittimo. Lo stesso allineamento che
    # ``GardenerStore.__init__`` fa sulle sue due radici, per la stessa ragione.
    #
    # **Al setter e non all'accessor**: ``resolve()`` fa un ``lstat`` per
    # componente, e ``get_workspace_path`` è chiamata a ogni render di prompt e da
    # dentro il sandbox, dove ogni lstat passa dalla guardia e produce una raffica
    # di rifiuti spuri (v. ``_path_guard_bypass``). Qui si paga una volta per
    # avvio.
    get_runtime_context().workspace_dir = (
        Path(path).resolve(strict=False) if path else None
    )
    # L'ambiente Jinja e memoizzato sulla radice dei template, che *e* il
    # workspace: senza questo, cambiare workspace lascia in piedi un loader che
    # legge ancora dal precedente. In produzione succede una volta all'avvio,
    # nei test a ogni caso — ed e li che un prompt costruito dai file di un
    # altro test non fallisce, mente.
    forget_templates_root()


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory (inside workspace)."""
    workspace = get_workspace_path()
    data_dir = workspace / ".jenny"
    # Migrate from earlier data-dir names in order (.minijenny is the more recent legacy).
    for legacy_name in (".minijenny", ".nanobot"):
        legacy_dir = workspace / legacy_name
        if not data_dir.exists() and legacy_dir.exists():
            legacy_dir.rename(data_dir)
            logger.info("Migrated runtime data directory %s -> .jenny", legacy_name)
    return ensure_dir(data_dir)


def get_workspace_path() -> Path:
    """Return the agent workspace path.

    The workspace must be set explicitly via ``set_workspace_dir`` before use.

    **Non crea la cartella, e la correzione è del 26/08.** Chiamava
    ``ensure_dir``, cioè un ``mkdir(parents=True, exist_ok=True)`` sulla radice
    del workspace **a ogni chiamata** — un accessor che scrive. Dentro
    ``python_exec`` il confine di lettura è il workspace ma quello di *mutazione*
    è la cartella del progetto, quindi quel ``mkdir`` era una scrittura fuori dal
    progetto e la guardia lo rifiutava, correttamente: ``wiki_lint``,
    ``wiki_audit`` e ``wiki_scaffold`` non erano eseguibili in nessun turno legato
    a un progetto, e il «hard gate» della skill ``llm-wiki`` era un passo che non
    poteva riuscire.

    Ed era inutile: la cartella la crea ``android_entry`` con un ``mkdir``
    esplicito **prima** di ``set_workspace_dir``, che è il posto giusto — una
    volta, all'avvio, fuori da ogni sandbox. Chi ha bisogno di creare una
    *sottocartella* continua a passare da ``ensure_dir`` (v. ``get_data_dir``):
    quelle stanno dentro il workspace e non è questo il caso.

    Raises:
        RuntimeError: if no workspace directory has been configured.
    """
    from jenny.runtime.context import get_runtime_context

    workspace_dir = get_runtime_context().workspace_dir
    if workspace_dir is not None:
        return workspace_dir
    raise RuntimeError(
        "Workspace directory is not configured. "
        "Call set_workspace_dir() before get_workspace_path()."
    )


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_uploads_dir() -> Path:
    """Cartella degli allegati caricati dall'utente dalla chat.

    Vive dentro il workspace (``workspace/uploads``) così da essere leggibile
    dai tool filesystem dell'agente (path sotto la radice del workspace) e
    visibile nel browser file della WebUI, a differenza della media dir
    runtime nascosta (``.jenny/media``). Gli allegati non-immagine vengono
    referenziati per path e letti on-demand dall'agente.
    """
    return ensure_dir(get_workspace_path() / "uploads")


OUTPUT_SUBDIR = "output"

_DISPLACED_SUFFIX = "displaced"
_MAX_DISPLACED = 100


def _displace_output_obstruction(target: Path) -> None:
    """Toglie di mezzo ciò che occupa il nome ``output`` senza essere una cartella.

    ``mkdir(exist_ok=True)`` perdona solo una directory: un file regolare — o un
    symlink rotto, che esiste per il filesystem ma per nessun altro — solleva
    ``FileExistsError``. Quella eccezione risaliva fino a ``build()`` del
    container e il gateway non partiva più: watchdog, riavvio, di nuovo lì. E il
    punto in cui cadeva è dopo l'estrazione di prompt, skill e UI, quindi il log
    finiva su una riga che diceva "estratti 198 file" e nient'altro.

    Chi lo mette lì, quel file, è l'agente stesso: la cartella ``output/`` nasce
    per togliergli di mano la radice del workspace come destinazione di default,
    e finché non c'era ci lasciava i risultati — uno dei quali può benissimo
    chiamarsi ``output``.

    **Si sposta, non si cancella.** È lavoro prodotto, e distruggerlo per fare
    posto a una cartella vuota sarebbe il rimedio peggiore del male. Le altre due
    strade valutate: proseguire senza la cartella rimette l'agente a scrivere
    nella radice, cioè riapre esattamente il problema che ``output/`` chiude;
    ripiegare su un nome diverso (``output-1/``) fa mentire il prompt, che la
    cita per nome in ``agent/context.py``. Rinominare di lato è l'unica che
    lascia il sistema nello stato che tutti gli altri pezzi si aspettano.

    Se persino la rinomina fallisce non si solleva: il boot vale più della
    cartella. Il chiamante ritorna un path che non esiste, e la prima scrittura
    fallirà con un errore suo — leggibile, e non in crash-loop.
    """
    if target.is_dir():
        # Anche un symlink a una directory *è* una directory per mkdir: non si tocca.
        return
    if not target.exists() and not target.is_symlink():
        return

    for n in range(_MAX_DISPLACED):
        suffix = _DISPLACED_SUFFIX if n == 0 else f"{_DISPLACED_SUFFIX}.{n}"
        candidate = target.with_name(f"{target.name}.{suffix}")
        if candidate.exists() or candidate.is_symlink():
            continue
        try:
            target.rename(candidate)
        except OSError as exc:
            logger.error(
                "The name {} is taken by a file and moving it to {} failed ({}): "
                "the results folder does not exist for this start",
                target, candidate.name, exc,
            )
            return
        logger.error(
            "Found a file (not a folder) named {}: moved to {} to free up the agent's "
            "results folder. It is produced work and was not deleted — move it "
            "into {}/ or rename it.",
            target, candidate.name, OUTPUT_SUBDIR,
        )
        return

    logger.error(
        "The name {} is taken and all {} fallback names are taken too: "
        "the results folder does not exist for this start",
        target, _MAX_DISPLACED,
    )


def get_output_path(workspace: Path | None = None, *, create: bool = False) -> Path:
    """Cartella dei file che l'agente *produce* (``workspace/output``).

    Esiste per togliere di mezzo l'unica destinazione che l'agente sceglieva
    quando non ne aveva una: la radice del workspace. Lì vivono i documenti di
    bootstrap (``AGENTS.md``, ``SOUL.md``, ``USER.md``, ``HEARTBEAT.md``), che
    l'agente deve poter *modificare* ma mai affiancare — un risultato di lavoro
    lasciato accanto a loro non si distingue più da un file di sistema, e
    l'unico modo di riconoscerlo diventa aprirlo.

    Sorella di ``get_uploads_dir``: dentro il workspace, quindi leggibile dai
    tool filesystem e visibile nel browser file. Nessuno la spazza a tempo —
    contiene lavoro finito, non scarti.

    ``create=False`` di default perché i chiamanti che compongono un prompt la
    citano soltanto: la directory la crea ``sync_workspace_templates`` una volta
    per avvio, non un ``mkdir`` per ogni prompt costruito.

    Con ``create=True`` la creazione **non solleva mai**: l'unico chiamante è la
    sync all'avvio, e nessun ostacolo su questo path vale un gateway che non
    parte (vedi ``_displace_output_obstruction``).
    """
    base = workspace if workspace is not None else get_workspace_path()
    target = base / OUTPUT_SUBDIR
    if not create:
        return target
    _displace_output_obstruction(target)
    try:
        return ensure_dir(target)
    except OSError as exc:
        logger.error(
            "Could not create the results folder {} ({}): starting without it",
            target, exc,
        )
        return target


def get_ssh_dir() -> Path:
    """Chiave privata SSH e ``known_hosts``, **fuori dal workspace**.

    Sta accanto al workspace, non dentro (su Android: ``filesDir/ssh`` mentre il
    workspace è ``filesDir/workspace``). Le tre conseguenze sono tutte volute:

    * i tool filesystem dell'agente non possono leggerla **finché
      ``security.restrict_to_workspace`` resta acceso** (il default):
      ``resolve_allowed_path`` rifiuta un path fuori dalla radice workspace. Con
      quell'impostazione spenta la radice diventa illimitata e la chiave torna
      leggibile — è una rinuncia esplicita dell'utente, non una svista, ma qui
      la conseguenza è più pesante che altrove: a differenza di ``config.json``
      (che sta *dentro* il workspace e che l'agente può già leggere) una chiave
      SSH privata dà accesso a una macchina terza;
    * non entra negli snapshot né nel backup cifrato esportabile, perché
      ``snapshot/engine.py`` cammina solo la radice del workspace;
    * resta comunque nello storage privato dell'app.

    Il prezzo, da documentare per l'utente: un restore del workspace **non**
    ripristina l'accesso SSH. Si rigenera la chiave e si reinstalla la pubblica
    sul server.
    """
    return ensure_dir(get_workspace_path().parent / "ssh")


def get_webui_dir() -> Path:
    """Return the directory for WebUI-only persisted display threads (JSON)."""
    return get_runtime_subdir("webui")


