"""L'ordine dei blocchi nel system prompt di un progetto, dove è una decisione.

**T3.13.** Due blocchi si contraddicono per costruzione, e chi vince lo decide la
posizione: la prosa più vicina alla fine è quella che il modello segue. In
``build_system_prompt`` c'erano due scelte opposte prese di proposito, entrambe
spiegate in un commento e **nessuna delle due tenuta ferma da un test** — mutare
``parts.append(bootstrap)`` in ``parts.insert(0, bootstrap)`` passava la suite
intera.

Le due scelte, e perché sono opposte:

- ``agent/project.md`` sta **prima** dell'``AGENTS.md`` del progetto, cioè
  *perde*. Quel blocco è la pianta generale di un progetto; l'``AGENTS.md`` è il
  posto in cui l'utente — o Jenny — scrive come si lavora *in questo* progetto, e
  un'eccezione scritta lì non serve a niente se la regola generale la segue e la
  sovrascrive.
- ``agent/scheduling.md`` sta **dopo**, cioè *vince*: è prosa di sistema estratta
  da un template di ``AGENTS.md`` che si crea al primo avvio e non si aggiorna
  mai più, quindi su un'installazione vecchia deve poter battere il testo che si
  porta dietro.

Il file sta a parte e non in ``test_project_prompt_contract.py`` — che sarebbe la
casa naturale — perché quel file è in mano a un'altra sessione mentre questo
viene scritto.
"""

from __future__ import annotations

import pathlib

from jenny.agent.context import ContextBuilder

# Il marcatore del blocco di bootstrap è l'intestazione che
# ``_load_bootstrap_files`` mette davanti a ogni file (``## <nome>``): è la sola
# cosa che dice *dove* quel blocco è finito, e non dipende dal contenuto che
# l'utente ci ha scritto.
BOOTSTRAP_MARKER = "## AGENTS.md"
PROJECT_MARKER = "# Project Folder"
SCHEDULING_MARKER = "# Work for Later"


def _project_prompt(root: pathlib.Path, *, instructions: str) -> str:
    """Il prompt di un turno dentro un progetto che ha il suo ``AGENTS.md``."""
    project = root / "wikis" / "etna-guide"
    (project / "wiki").mkdir(parents=True)
    # Non il template: ``_BOOTSTRAP_SKIP_IF_TEMPLATE`` contiene ``AGENTS.md``,
    # quindi un file ancora identico al default non entrerebbe affatto e il test
    # passerebbe senza aver misurato niente.
    (project / "AGENTS.md").write_text(instructions, encoding="utf-8")
    return ContextBuilder(root).build_system_prompt(
        workspace=project, session_key="project:etna-guide"
    )


def test_the_project_block_comes_before_the_projects_own_instructions(tmp_path) -> None:
    """La regola generale prima, l'eccezione locale dopo: **qui il sistema perde.**

    L'asserzione è posizionale e non testuale, perché la posizione *è* il
    meccanismo: entrambi i blocchi ci sono comunque, e un ordine invertito non si
    vede in nessuna ricerca di sottostringa.
    """
    prompt = _project_prompt(
        tmp_path,
        instructions=(
            "# etna-guide\n\nIn questo progetto le pagine si scrivono in inglese, "
            "al contrario della regola generale.\n"
        ),
    )

    assert PROJECT_MARKER in prompt and BOOTSTRAP_MARKER in prompt
    assert prompt.index(PROJECT_MARKER) < prompt.index(BOOTSTRAP_MARKER), (
        "l'AGENTS.md del progetto deve poter contraddire il blocco di sistema"
    )


def test_the_recurring_work_block_comes_after_them_both(tmp_path) -> None:
    """Il controllo che rende la prima asserzione una decisione e non un caso.

    Se l'ordine fosse indifferente, questi due blocchi starebbero dalla stessa
    parte dell'``AGENTS.md``. Non lo sono: ``agent/scheduling.md`` è stato
    *estratto* da quel template proprio perché su un telefono aggiornato da mesi
    il testo vecchio resta lì, e deve perdere.
    """
    prompt = _project_prompt(
        tmp_path, instructions="# etna-guide\n\nAppunti di lavoro di questo progetto.\n"
    )

    assert SCHEDULING_MARKER in prompt
    assert prompt.index(BOOTSTRAP_MARKER) < prompt.index(SCHEDULING_MARKER), (
        "la prosa di sistema sul lavoro ricorrente deve battere un AGENTS.md vecchio"
    )
