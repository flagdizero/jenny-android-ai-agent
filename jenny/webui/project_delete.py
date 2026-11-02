"""Cancellazione di un progetto dalla WebUI: l'albero **e** la sua conversazione.

Lo specchio di :mod:`jenny.webui.project_create`, e la meta' che mancava. Prima
di questo modulo un progetto si creava con un'operazione che sapeva cos'e' un
progetto e si cancellava con una ``rmtree`` che non lo sapeva: la cartella
spariva e le quattro tracce della chat restavano, sotto un nome ormai libero. Il
progetto successivo creato con quel nome se le riprendeva tutte — riprodotto sul
telefono il 24/08/2026.

**L'ordine delle operazioni sceglie lo stato intermedio, quindi e' il disegno.**

1. si sgombera la sessione dalla cache, o il primo salvataggio la riscriverebbe;
2. **prima le tracce**: interrotti qui, restano un progetto e una chat vuota —
   visibile, non corrotto, e si finisce ritentando;
3. **poi l'albero**: e' l'unica cosa che puo' far nascere tracce nuove, quindi
   chiuderlo per secondo e' anche chiudere la sorgente;
4. **le tracce un'altra volta**, perche' solo adesso la sorgente e' chiusa: un
   turno che fosse partito nella finestra fra 2 e 3 ha riscritto la sessione, e
   questa e' la passata che la trova. Costa quattro ``stat``;
5. il registro, che e' derivato e non fa fallire niente.

L'ordine opposto — albero prima — lascerebbe l'orfano, cioe' il difetto. E'
la ragione per cui qui non c'e' un giornale come in
``session/project_rename.py``: quello protegge stati intermedi tutti cattivi,
qui lo stato intermedio e' gia' quello innocuo.

**Si puo' cancellare anche un progetto che non c'e' piu'.** Cartella assente e
tracce presenti e' esattamente l'orfano che il difetto produceva, e su un
telefono non c'e' altro modo di ripulirlo: e' un caso da servire, non da
rifiutare. Torna con ``orphan: True``, cosi' chi chiama puo' dirlo.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.session.keys import project_session_key
from jenny.session.project_traces import (
    TraceReport,
    delete_project_traces,
    describe_project_traces,
)
from jenny.utils.wiki_paths import is_wiki_root
from jenny.webui.wiki_registry import refresh_wiki_registry
from jenny.webui.workspace_files import os_error_text


class ProjectDeleteError(Exception):
    """Cancellazione non riuscita, con un messaggio mostrabile all'utente."""


def _tree_files(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file())


def describe_project(*, wikis_dir: Path, workspace: Path, name: str) -> dict[str, Any]:
    """Cosa porterebbe via una cancellazione di *name*. Sole letture.

    Serve alla conferma, e la conferma e' meta' del fix: la sicurezza di una
    cancellazione sta nel dire il vero su cosa sparisce — «3 file e 68 messaggi»
    — non in una copia nascosta che nessuno sa raggiungere.
    """
    root = wikis_dir / name
    traces: TraceReport = describe_project_traces(workspace, project_session_key(name))
    return {
        "name": name,
        "exists": root.is_dir(),
        "is_project": is_wiki_root(root),
        "files": _tree_files(root),
        "conversation": {
            "files": traces.files,
            "bytes": traces.bytes,
            "messages": traces.messages,
        },
        # L'orfano: nessuna cartella, ma una conversazione che vive ancora sotto
        # quel nome. E' lo stato che il difetto lasciava dietro di se'.
        "orphan": not root.exists() and traces.exists,
    }


def delete_project(
    *,
    wikis_dir: Path,
    scripts_dir: Path,
    workspace: Path,
    name: str,
    invalidate_session: Callable[[str], None],
) -> dict[str, Any]:
    """Cancella il progetto *name*: le tracce della chat, l'albero, il registro.

    Sincrona e con I/O: il chiamante la mette in un thread, come la creazione. Il
    nome arriva gia' validato — questo modulo non e' il gate.

    ``invalidate_session`` sgombera dalla cache in memoria la sessione che sta
    per sparire. E' un parametro e non un import perche' il ``SessionManager``
    vive nel composition root: prenderlo da qui vorrebbe dire che questo modulo
    sa come si costruisce il runtime.
    """
    root = wikis_dir / name
    key = project_session_key(name)
    traces_before = describe_project_traces(workspace, key)

    if root.exists() and not is_wiki_root(root):
        # Stessa distinzione della creazione, e per la stessa ragione: «c'e'
        # qualcosa di mezzo» si risolve rinominando, e cancellarlo non e'
        # compito di questa operazione.
        raise ProjectDeleteError(
            f"a folder named {name} is in the way: it exists but is not a project"
        )
    if not root.exists() and not traces_before.exists:
        raise ProjectDeleteError(f"no project named {name}")

    orphan = not root.exists()

    # 1. La cache prima di tutto: una sessione viva in memoria riscriverebbe il
    #    proprio file appena qualcuno la salva, e il file lo stiamo togliendo.
    try:
        invalidate_session(key)
    except Exception:  # noqa: BLE001 — una cache che non si sgombera non ferma la cancellazione
        logger.opt(exception=True).warning("Cache di sessione non sgomberata per {}", key)

    # 2. Le tracce, che e' lo stato intermedio innocuo.
    removed = delete_project_traces(workspace, key)

    # 3. L'albero, che e' la sola sorgente di tracce nuove.
    removed_tree = False
    if root.exists():
        try:
            shutil.rmtree(root)
            removed_tree = True
        except OSError as exc:
            # Le tracce sono gia' andate: lo stato che resta e' «progetto con
            # chat vuota», quello ritentabile. Si dice, e non si finge riuscito.
            # Il perche' senza il dove: ``str(exc)`` porta il percorso assoluto
            # della cartella privata dell'app, e questo messaggio va al client.
            logger.opt(exception=True).warning("Project folder not removed: {}", name)
            raise ProjectDeleteError(
                f"could not remove the project folder: {os_error_text(exc)}"
            ) from exc

    # 4. Solo ora la sorgente e' chiusa: quel che e' nato nella finestra fra 2 e
    #    3 e' qui, e questa e' l'unica passata che lo puo' vedere.
    late = delete_project_traces(workspace, key)
    if late:
        logger.warning(
            "Traces reappeared while deleting {} and were removed on the second "
            "pass: {}", name, ", ".join(late)
        )
        removed = [*removed, *late]

    registry = refresh_wiki_registry(wikis_dir, scripts_dir)
    logger.info(
        "Progetto {} cancellato (albero: {}, tracce: {})",
        name, removed_tree, len(removed),
    )
    return {
        "name": name,
        "removed_tree": removed_tree,
        "removed_traces": removed,
        "orphan": orphan,
        "messages": traces_before.messages,
        "registry": registry,
    }
