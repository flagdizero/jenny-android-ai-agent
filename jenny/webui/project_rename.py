"""Rinominare un quaderno da Jenny: la cartella, poi la chat che la segue.

**La meta' che mancava.** ``session/project_rename.py`` sa gia' *inseguire* una
wiki che ha cambiato nome — qualcuno rinomina la cartella a mano, e al turno
dopo la chat la ritrova per id e le va dietro, con un giornale che rende il
salto ripetibile dopo un crollo. Mancava la strada in avanti: chiederlo a
Jenny. Questo modulo e' quella strada, e non inventa un secondo meccanismo:
fa **nello stesso ordine** quel che succede quando il rinomino lo fa una mano —
prima la cartella, poi :func:`follow_renamed_project` per le tracce della chat.

L'ordine non e' casuale. Se il processo muore fra i due passi, lo stato che
resta e' esattamente quello che il gateway sa gia' riparare: una cartella col
nome nuovo e una chat rimasta indietro, che al primo turno la ritrova per id.
L'ordine opposto — prima la chat — lascerebbe una chat sotto un nome che non ha
cartella, cioe' il caso che il passo 6 **rifiuta**.

Si rifiuta **prima** di toccare qualunque cosa quando: il nome nuovo non si
aprirebbe (la stessa regola del canale), e' gia' preso da una cartella o da una
chat, il vecchio non e' un quaderno. Se la chat non puo' seguire per un motivo
che si sa dire (una destinazione occupata a meta' strada), la cartella torna
indietro: meglio un rinomino non fatto che due meta'.

Sincrona e con I/O, come la creazione e la cancellazione: il chiamante la mette
in un thread. Il seguito che e' della casa — le pagine appese — sta nel comando.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.session.keys import is_valid_project_name, project_session_key
from jenny.session.project_rename import follow_renamed_project, pending_project_renames
from jenny.session.project_traces import describe_project_traces
from jenny.utils.wiki_paths import is_wiki_root
from jenny.webui.wiki_registry import refresh_wiki_registry


class ProjectRenameError(Exception):
    """Un rifiuto che si puo' dire all'utente. Niente e' stato toccato.

    ``code`` e' il codice di ``CommandError`` con cui il comando lo inoltra. I due
    rifiuti **attesi** hanno il loro — ``name_taken`` (il nome nuovo e' gia' di
    una cartella o di una conversazione) e ``not_found`` (il vecchio non e' un
    quaderno) — perche' il client li dica nella lingua di chi legge invece di
    mostrare il testo inglese del server; gli altri restano ``bad_request``.
    """

    def __init__(self, message: str, *, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code


def rename_project(
    *,
    wikis_dir: Path,
    scripts_dir: Path,
    workspace: Path,
    name: str,
    new_name: str,
    invalidate_session: Callable[[str], None],
) -> dict[str, Any]:
    """Rinomina il quaderno *name* in *new_name*: cartella, chat, registro."""
    if not is_valid_project_name(new_name):
        raise ProjectRenameError(f"{new_name} cannot be the name of a notebook")
    if new_name == name:
        raise ProjectRenameError("the new name is the same as the old one")
    root = wikis_dir / name
    target = wikis_dir / new_name
    old_key = project_session_key(name)
    new_key = project_session_key(new_name)

    if not root.exists() or not is_wiki_root(root):
        raise ProjectRenameError(f"no notebook named {name}", code="not_found")
    if target.exists():
        raise ProjectRenameError(f"a folder named {new_name} already exists", code="name_taken")
    if describe_project_traces(workspace, new_key).exists:
        # Una chat senza cartella sotto il nome nuovo: e' lo scambio di due nomi
        # di `session/project_rename.py`, e come li' non si sceglie — si dice.
        raise ProjectRenameError(
            f"a conversation named {new_name} already exists", code="name_taken"
        )

    # La cache prima di tutto, per tutti e due i nomi: una sessione viva in
    # memoria riscriverebbe il proprio file sotto il nome vecchio appena
    # qualcuno la salva.
    for key in (old_key, new_key):
        try:
            invalidate_session(key)
        except Exception:  # noqa: BLE001 — una cache non sgomberata non ferma il rinomino
            logger.opt(exception=True).warning("Session cache not cleared for {}", key)

    root.rename(target)

    chat_moved = False
    if describe_project_traces(workspace, old_key).exists:
        chat_moved, why = follow_renamed_project(workspace, old_key, new_key)
        # A meta' strada si riconosce dal **giornale**, non dalla frase del
        # motivo: la voce resta aperta solo quando qualcosa si e' gia' mosso, ed
        # e' quella che il prossimo avvio porta a termine.
        halfway = (old_key, new_key) in pending_project_renames(workspace)
        if not chat_moved and not halfway:
            # Un rifiuto pulito: niente si e' mosso nella chat, e la cartella
            # torna al suo nome. Lasciarla rinominata vorrebbe dire una chat
            # orfana che il prossimo turno proverebbe a inseguire per id.
            try:
                target.rename(root)
            except OSError as exc:
                logger.opt(exception=True).error(
                    "Rename of {} not undone: the folder stays {}", name, new_name
                )
                raise ProjectRenameError(
                    f"the conversation could not follow ({why}), and the folder "
                    f"stayed as {new_name}"
                ) from exc
            raise ProjectRenameError(f"the conversation could not follow: {why}")
        if halfway:
            # A meta' strada e scritto nel giornale: il prossimo avvio finisce il
            # lavoro. La cartella resta col nome nuovo, che e' la direzione giusta.
            logger.warning(
                "Rename {} -> {}: chat moved halfway, startup will finish it", name, new_name
            )

    refresh_wiki_registry(wikis_dir, scripts_dir)
    logger.info("Notebook renamed: {} -> {} (chat moved: {})", name, new_name, chat_moved)
    return {"name": name, "new_name": new_name, "chat_moved": chat_moved}
