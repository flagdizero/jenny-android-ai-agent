"""Chi può cambiare i dati di un'app: la regola della posa.

Le app sono dell'utente, e generali. Fino al 06/10/2026 la chat di un
quaderno non poteva cambiare i dati di nessuna app («le app sono personali»,
decisione del 22/08): una Todo aggiornata da dentro un progetto aveva
sorpreso. Adesso un'app si **posa** sulla bacheca di un quaderno, e la posa è
anche il permesso: la chat di quel quaderno può usarla — «comprato 4 VWCE»
detto in Soldi finisce nell'app ETF posata in Soldi — e solo quella. Un'app
non posata resta di sola lettura da lì, come prima.

Due chiamanti: le azioni ``storage`` che scrivono (``storage.py``) e, novità,
le azioni ``http`` che non sono ``GET`` (``executor.py``), che prima passavano
senza nessun controllo di progetto.
"""

from __future__ import annotations

from pathlib import Path

NOT_PLACED_REFUSAL = (
    "Not from here: the app '{name}' is not on this notebook's board. A notebook's chat can "
    "change only the apps placed on its board; reading works. Ask the user whether to place it "
    "here (app action=place) or to do it from the personal chat — do not look for another way "
    "to write it."
)


def write_refusal(app_dir: Path) -> str | None:
    """Il rifiuto per una scrittura sui dati dell'app in *app_dir*, o ``None``.

    Fuori da un quaderno (la chat personale, le rotte HTTP dello schermo, i
    bottoni del cassetto: niente turno) non c'e' niente da rifiutare. Dentro un
    quaderno, si scrive solo un'app posata sulla sua bacheca.
    """
    from jenny.apps.storage import _in_a_project
    from jenny.board.placements import placed_slugs
    from jenny.security.workspace_access import current_workspace_scope
    from jenny.utils.wiki_paths import is_wiki_root

    if not _in_a_project(app_dir):
        return None
    scope = current_workspace_scope()
    root = scope.write_root() if scope is not None else None
    if root is not None and is_wiki_root(root) and Path(app_dir).name in placed_slugs(root):
        return None
    return NOT_PLACED_REFUSAL.format(name=Path(app_dir).name)
