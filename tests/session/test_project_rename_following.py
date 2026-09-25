"""Rinominare una cartella non perde più la sua chat.

Passo **7.2** e **7.3** di ``roadmap/progetti-passi.md``, strada **B**.

Il piano diceva «id stabile, e le chat passano a ``project:<id>``». Quella strada
**inverte l'invariante del 21/08** — la cartella si deduce dalla chiave e non è
scritta da nessuna parte — rimettendo il secondo dato che quella decisione aveva
evitato, e trasformando i nomi dei file in ``project_3f9a2c1b7e04.jsonl``.

Qui l'indirizzo resta il nome della cartella, e l'id serve solo a **ritrovare**
una chat orfana. La riparazione vive dentro il rifiuto del passo 6, che era già
l'unico punto che scopre che una cartella legata è sparita: quindi gira solo
quando qualcosa è già andato storto, e non sul percorso di ogni turno.

Le due asserzioni che pesano più delle altre:

- ``test_nothing_moves_when_the_destination_is_taken``: ci si arriva scambiando
  due nomi, e spostare comunque mescolerebbe due storie;
- ``test_a_move_stopped_halfway_is_written_down_and_finished``: sessione sotto un
  nome e trascrizione sotto un altro è lo schermo di una chat con la memoria di
  un'altra — il difetto che il passo 1 ha faticato a evitare.

**Un test che si chiamava ``test_a_partial_move_is_impossible``** viveva qui, e
provava soltanto il controllo delle destinazioni: cioè che *prima* di cominciare
non si comincia se una destinazione è occupata. Il nome prometteva l'invariante
per tutta la sequenza di ``rename``, che non era vera e non era provata. Ora si
chiama per quel che fa
(``test_the_destination_check_covers_every_trace_not_just_the_first``) e
l'invariante vera — uno stato a metà è scritto e finibile — ha i suoi test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.session.project_rename import (
    follow_renamed_project,
    pending_project_renames,
    repair_pending_project_renames,
)
from jenny.session.project_traces import PROJECT_WIKI_ID_KEY, project_trace_paths

WIKI_ID = "3f9a2c1b7e04"


@pytest.fixture
def loop(tmp_path: Path, monkeypatch) -> AgentLoop:
    monkeypatch.setattr(
        "jenny.config.paths.get_webui_dir", lambda: _ensure(tmp_path / ".jenny" / "webui")
    )
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def published(loop: AgentLoop) -> list[str]:
    sent: list[str] = []

    async def capture(message) -> None:
        sent.append(message.content)

    loop.bus.publish_outbound = capture  # type: ignore[assignment]
    return sent


def _wiki(root: Path, name: str, wiki_id: str | None = WIKI_ID) -> Path:
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True)
    (project / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    head = f"---\nid: {wiki_id}\n---\n\n" if wiki_id else "---\nsummary: x\n---\n\n"
    (project / "AGENTS.md").write_text(head + f"# {name}\n", encoding="utf-8")
    return project


def _msg() -> InboundMessage:
    return InboundMessage(
        channel="websocket", chat_id="default", sender_id="u", content="ciao"
    )


def _traces(loop: AgentLoop, key: str) -> list[Path]:
    """Le tracce **esistenti**, create a mano per simulare una chat vissuta."""
    made = []
    for path in project_trace_paths(loop.workspace, key):
        if path.suffix == ".segments":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        made.append(path)
    return made


# ── 7.2 — la sessione si annota di chi è ─────────────────────────────────


def test_a_project_turn_records_its_wiki_id(loop: AgentLoop, tmp_path: Path) -> None:
    _wiki(tmp_path, "patreon")
    loop._remember_project_id("project:patreon")
    assert loop.sessions.get_or_create("project:patreon").metadata[PROJECT_WIKI_ID_KEY] == WIKI_ID


def test_it_is_written_once_and_not_re_read(loop: AgentLoop, tmp_path: Path) -> None:
    """Il costo è una lettura di frontmatter al primo turno, e zero dopo.

    Provato cambiando l'id **nel file** invece di cancellarlo: cancellarlo
    lasciava passare anche una versione che rilegge a ogni turno, perché la
    seconda lettura non trovava niente da riscrivere.
    """
    project = _wiki(tmp_path, "patreon")
    loop._remember_project_id("project:patreon")
    (project / "AGENTS.md").write_text("---\nid: ffffffffffff\n---\n", encoding="utf-8")

    loop._remember_project_id("project:patreon")

    metadata = loop.sessions.get_or_create("project:patreon").metadata
    assert metadata[PROJECT_WIKI_ID_KEY] == WIKI_ID, (
        "l'id si annota una volta: rileggerlo a ogni turno è I/O per niente, e su un "
        "file che l'utente può cambiare sotto i piedi"
    )


def test_a_wiki_without_an_id_records_nothing(loop: AgentLoop, tmp_path: Path) -> None:
    """Non è un errore: quella chat si comporta come prima del passo 7."""
    _wiki(tmp_path, "senza", wiki_id=None)
    loop._remember_project_id("project:senza")
    assert PROJECT_WIKI_ID_KEY not in loop.sessions.get_or_create("project:senza").metadata


@pytest.mark.parametrize("key", ["unified:default", "cron:update_check", ""])
def test_nothing_is_recorded_outside_a_project(loop: AgentLoop, key: str) -> None:
    loop._remember_project_id(key)
    if key:
        assert PROJECT_WIKI_ID_KEY not in loop.sessions.get_or_create(key).metadata


# ── 7.3 — il rifiuto che ripara ──────────────────────────────────────────


async def test_a_renamed_folder_takes_its_chat_with_it(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    made = _traces(loop, "project:vecchio")
    # Il rinomino, fatto fuori da Jenny: è il caso reale.
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "nuovo")

    refused = await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert refused is True, "il turno non parte: il messaggio non è stato letto"
    assert "nuovo" in published[0] and "renamed" in published[0].lower()
    assert PROJECT_WIKI_ID_KEY not in loop.sessions.get_or_create("project:vecchio").metadata, (
        "la sessione vecchia era in cache e i suoi file hanno cambiato nome: tenerla "
        "vorrebbe dire riscriverla al nome vecchio al primo salvataggio, e ritrovarsi "
        "la storia in due posti"
    )
    old = [p for p in project_trace_paths(loop.workspace, "project:vecchio") if p.exists()]
    new = [p for p in project_trace_paths(loop.workspace, "project:nuovo") if p.exists()]
    assert old == [], f"tracce rimaste sotto il nome vecchio: {[p.name for p in old]}"
    # Quattro: sessione, trascrizione, thread legacy e record dei subagent. La
    # ``.segments`` non la crea questo test perché è una directory.
    assert len(new) == len(made), "tutte le tracce esistenti devono essere arrivate"


async def test_nothing_moves_when_the_destination_is_taken(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Lo scambio di due nomi: meglio due chat da rinominare a mano che due storie mescolate."""
    _wiki(tmp_path, "a")
    loop._remember_project_id("project:a")
    _traces(loop, "project:a")
    _traces(loop, "project:b")           # "b" ha già una conversazione sua
    (tmp_path / "wikis" / "a").rename(tmp_path / "wikis" / "b")

    await loop._refuse_missing_project(_msg(), "project:a")

    assert [p for p in project_trace_paths(loop.workspace, "project:a") if p.exists()]
    assert "already has a conversation of its own" in published[0]


async def test_the_destination_check_covers_every_trace_not_just_the_first(
    loop: AgentLoop, tmp_path: Path
) -> None:
    """Il controllo *precede* la sequenza, e guarda tutte le destinazioni.

    Questo è il pezzo davvero atomico: una sola destinazione occupata, e non la
    prima, e non parte niente. Non prova nulla su cosa accade *dentro* la
    sequenza di ``rename`` — quello è il mestiere dei due test qui sotto.
    """
    _wiki(tmp_path, "src")
    made = _traces(loop, "project:src")
    # Una sola destinazione occupata, e non la prima: se lo spostamento fosse
    # incrementale, le precedenti sarebbero già partite.
    project_trace_paths(loop.workspace, "project:dst")[-1].write_text("x\n", encoding="utf-8")

    moved, why = follow_renamed_project(loop.workspace, "project:src", "project:dst")

    assert moved is False and why
    assert all(p.exists() for p in made), "nessuna traccia deve essersi mossa"
    assert pending_project_renames(loop.workspace) == [], (
        "un rifiuto prima di cominciare non deve lasciare niente da riparare"
    )


# ── Lo spostamento a metà: scritto, quindi finibile ──────────────────────
#
# La finestra è di microsecondi fra cinque ``rename``, ma il processo su Android
# può morire in qualunque momento — e lo stato che ne uscirebbe non lo ripara
# nessuno: il rifiuto del passo 6 scatta quando la cartella *manca*, e dopo un
# rinomino la cartella c'è. Quindi si scrive prima di cominciare.


def _all_traces(loop: AgentLoop, key: str) -> list[Path]:
    """Tutte e cinque, ``.segments`` compresa: è una directory, non un file."""
    made = []
    for path in project_trace_paths(loop.workspace, key):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".segments":
            path.mkdir()
        else:
            path.write_text("{}\n", encoding="utf-8")
        made.append(path)
    return made


def _rename_that_breaks(nth: int, *, and_cannot_go_back: bool):
    """Un ``Path.rename`` che rompe all'*n*-esimo spostamento in avanti."""
    real = Path.rename
    seen = {"forward": 0}

    def fake(self: Path, target):  # type: ignore[no-untyped-def]
        going_forward = "project_dst" in Path(target).name
        if going_forward:
            seen["forward"] += 1
            if seen["forward"] == nth:
                raise OSError(28, "No space left on device")
        elif and_cannot_go_back:
            raise OSError(13, "Permission denied")
        return real(self, target)

    return fake


async def test_an_error_halfway_rolls_back_and_says_nothing_moved(
    loop: AgentLoop, tmp_path: Path, monkeypatch
) -> None:
    """Terzo di cinque: quel che era partito torna, e il giornale si chiude."""
    _wiki(tmp_path, "src")
    made = _all_traces(loop, "project:src")
    monkeypatch.setattr(Path, "rename", _rename_that_breaks(3, and_cannot_go_back=False))

    moved, why = follow_renamed_project(loop.workspace, "project:src", "project:dst")

    assert moved is False
    assert why == "moving the conversation's files failed, so nothing was moved"
    assert all(p.exists() for p in made), (
        "le due tracce già partite devono essere tornate indietro: metà sotto un nome "
        "e metà sotto l'altro è la chat vuota con la memoria di un'altra"
    )
    assert [p for p in project_trace_paths(loop.workspace, "project:dst") if p.exists()] == []
    assert pending_project_renames(loop.workspace) == [], (
        "tornato indietro del tutto: non resta niente da riparare"
    )


async def test_a_move_stopped_halfway_is_written_down_and_finished(
    loop: AgentLoop, tmp_path: Path, monkeypatch
) -> None:
    """Né avanti né indietro: lo stato a metà è **visibile** e viene finito.

    È il caso peggiore: il terzo ``rename`` fallisce *e* il ritorno indietro
    fallisce anche lui. Prima di questa correzione qui finiva la storia — due
    tracce sotto il nome nuovo, tre sotto quello vecchio, e nessuno che lo
    scoprisse mai. Ora la voce nel giornale resta, e la riparazione la chiude.
    """
    _wiki(tmp_path, "src")
    _all_traces(loop, "project:src")
    # Un contesto annidato, e non ``monkeypatch.setattr`` diretto: qui serve
    # ridare indietro *solo* ``Path.rename``, mentre ``monkeypatch.undo()``
    # smonterebbe anche il ``get_webui_dir`` della fixture — e le tracce webui
    # tornerebbero a puntare fuori da ``tmp_path``, dove il test le cerca.
    with monkeypatch.context() as breaks:
        breaks.setattr(Path, "rename", _rename_that_breaks(3, and_cannot_go_back=True))
        moved, why = follow_renamed_project(loop.workspace, "project:src", "project:dst")

    assert moved is False
    assert why is not None and "finish it the next time I start" in why
    assert pending_project_renames(loop.workspace) == [("project:src", "project:dst")], (
        "uno stato a metà che non è scritto da nessuna parte non lo ripara nessuno"
    )

    # Il processo riparte: il disco è di nuovo scrivibile, e il boot chiude la voce.
    completed = repair_pending_project_renames(loop.workspace)

    assert completed == [("project:src", "project:dst")]
    assert [p for p in project_trace_paths(loop.workspace, "project:src") if p.exists()] == []
    assert len([p for p in project_trace_paths(loop.workspace, "project:dst") if p.exists()]) == 5
    assert pending_project_renames(loop.workspace) == []


async def test_a_process_killed_between_two_renames_is_repaired_at_the_next_boot(
    loop: AgentLoop, tmp_path: Path
) -> None:
    """Il caso vero: nessun ``OSError``, il processo è semplicemente morto.

    Si ricostruisce a mano lo stato che lascia — due tracce arrivate, la voce
    ancora aperta — perché è l'unico modo di simulare un SIGKILL fra due
    ``rename``, ed è per quello stato che il giornale esiste.
    """
    _wiki(tmp_path, "src")
    old = project_trace_paths(loop.workspace, "project:src")
    new = project_trace_paths(loop.workspace, "project:dst")
    _all_traces(loop, "project:src")
    import json

    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sessions" / ".project-rename-pending.json").write_text(
        json.dumps([{"old": "project:src", "new": "project:dst"}]), encoding="utf-8"
    )
    for i in (0, 1):
        new[i].parent.mkdir(parents=True, exist_ok=True)
        old[i].rename(new[i])

    completed = repair_pending_project_renames(loop.workspace)

    assert completed == [("project:src", "project:dst")]
    assert [p for p in old if p.exists()] == []
    assert len([p for p in new if p.exists()]) == 5
    assert pending_project_renames(loop.workspace) == []


async def test_repairing_nothing_is_a_no_op(loop: AgentLoop, tmp_path: Path) -> None:
    """Il costo al boot quando non c'è niente da fare: una lettura che non trova."""
    assert repair_pending_project_renames(loop.workspace) == []
    assert not (tmp_path / "sessions" / ".project-rename-pending.json").exists()


async def test_a_completed_move_leaves_nothing_to_repair(
    loop: AgentLoop, tmp_path: Path
) -> None:
    """Il percorso felice non lascia rifiuti: il giornale non è un registro storico."""
    _wiki(tmp_path, "src")
    made = _all_traces(loop, "project:src")

    moved, why = follow_renamed_project(loop.workspace, "project:src", "project:dst")

    assert moved is True and why is None
    assert [p for p in made if p.exists()] == []
    assert len([p for p in project_trace_paths(loop.workspace, "project:dst") if p.exists()]) == 5
    assert pending_project_renames(loop.workspace) == []
    assert not (tmp_path / "sessions" / ".project-rename-pending.json").exists()


async def test_a_chat_that_never_recorded_an_id_gets_the_plain_refusal(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    (tmp_path / "wikis").mkdir()
    refused = await loop._refuse_missing_project(_msg(), "project:mai-vista")
    assert refused is True
    assert "never recorded" in published[0]
    assert "renaming it back" in published[0]


async def test_a_folder_whose_id_is_nowhere_gets_the_plain_refusal(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """La wiki è stata cancellata, non rinominata: non c'è niente da inseguire."""
    _wiki(tmp_path, "sparita")
    loop._remember_project_id("project:sparita")
    import shutil

    shutil.rmtree(tmp_path / "wikis" / "sparita")

    await loop._refuse_missing_project(_msg(), "project:sparita")

    assert "no folder here claims" in published[0]


async def test_an_ambiguous_id_is_not_followed(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Due wiki con lo stesso id: si rifiuta invece di scegliere (passo 6)."""
    _wiki(tmp_path, "originale")
    loop._remember_project_id("project:originale")
    _traces(loop, "project:originale")
    _wiki(tmp_path, "copia")             # stesso id, arrivata copiando la cartella
    (tmp_path / "wikis" / "originale").rename(tmp_path / "wikis" / "rinominata")

    await loop._refuse_missing_project(_msg(), "project:originale")

    assert [p for p in project_trace_paths(loop.workspace, "project:originale") if p.exists()]
    assert "no folder here claims" in published[0]


# ── La riparazione non gira sotto un turno in volo ───────────────────────
#
# La riparazione stava trenta righe **prima** del controllo del turno in volo,
# quindi girava anche mentre un turno di quella stessa chat stava lavorando. Il
# turno in volo tiene in mano la sua ``Session``: quando finisce la salva, e la
# salva al nome che aveva quando e' partito. Se nel frattempo le tracce sono
# passate al nome nuovo, quel salvataggio **ricrea** il file vecchio — la storia
# di prima del rinomino piu' lo scambio appena concluso — e quello scambio, cioe'
# l'ultima cosa che l'utente ha detto, resta fuori dal progetto rinominato.


async def test_the_repair_is_deferred_while_a_turn_is_in_flight(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    made = _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "nuovo")
    # Un turno di *questa* chat sta lavorando: e' esattamente il segnale che
    # ``run()`` usa trenta righe sotto per iniettare un follow-up nel turno.
    loop._pending_queues["project:vecchio"] = asyncio.Queue()

    refused = await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert refused is True, "il turno non parte: la cartella manca comunque"
    assert all(p.exists() for p in made), (
        "niente si muove mentre un turno di questa chat e' in volo"
    )
    assert [p for p in project_trace_paths(loop.workspace, "project:nuovo") if p.exists()] == []
    assert pending_project_renames(loop.workspace) == [], (
        "rinviata vuol dire non cominciata: non resta niente a meta'"
    )
    assert "still finishing the previous message" in published[0], (
        "e lo dice, invece di raccontare che ha cercato la cartella senza cercarla"
    )


async def test_the_turn_that_lands_after_it_does_not_resurrect_the_old_name(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Il difetto vero, in fila: rinomino a meta' turno, poi il turno salva.

    Senza il rinvio la sequenza e' questa — la riparazione porta le tracce a
    ``nuovo``, il turno in volo finisce e ``sessions.save()`` riscrive
    ``project_vecchio.jsonl``: due storie in due posti, e lo scambio appena
    concluso e' in quello sbagliato.
    """
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    _traces(loop, "project:vecchio")
    # Quel che il turno in volo tiene in mano, e che salvera' finendo.
    session = loop.sessions.get_or_create("project:vecchio")
    loop._pending_queues["project:vecchio"] = asyncio.Queue()
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "nuovo")

    await loop._refuse_missing_project(_msg(), "project:vecchio")
    # Il turno in volo finisce, come fa ogni turno.
    session.add_message("assistant", "quel che stavo dicendo")
    loop.sessions.save(session)

    assert [p for p in project_trace_paths(loop.workspace, "project:nuovo") if p.exists()] == [], (
        "un file sotto il nome nuovo qui vuol dire che la storia si e' divisa in due"
    )
    survived = project_trace_paths(loop.workspace, "project:vecchio")[0]
    assert "quel che stavo dicendo" in survived.read_text(encoding="utf-8"), (
        "e lo scambio appena concluso deve stare dove sta tutto il resto"
    )


async def test_the_next_message_after_the_turn_does_the_repair(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Rinviata, non annullata: il messaggio dopo la fine del turno ripara."""
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "nuovo")
    loop._pending_queues["project:vecchio"] = asyncio.Queue()
    await loop._refuse_missing_project(_msg(), "project:vecchio")

    # Il turno finisce: ``_dispatch`` toglie la coda (v. ``run``/``_dispatch``).
    loop._pending_queues.pop("project:vecchio")
    await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert "nuovo" in published[1] and "renamed" in published[1].lower()
    assert [p for p in project_trace_paths(loop.workspace, "project:vecchio") if p.exists()] == []


async def test_a_turn_in_flight_elsewhere_does_not_defer_anything(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Il rinvio guarda *questa* chiave: scritto piu' largo non riparerebbe mai."""
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "nuovo")
    loop._pending_queues["unified:default"] = asyncio.Queue()
    loop._pending_queues["project:altro"] = asyncio.Queue()

    await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert "renamed" in published[0].lower()
    assert [p for p in project_trace_paths(loop.workspace, "project:vecchio") if p.exists()] == []


# ── E il rifiuto non promette piu' di quel che mantiene ──────────────────


async def test_a_move_stopped_halfway_does_not_claim_nothing_is_lost(
    loop: AgentLoop, published: list[str], tmp_path: Path, monkeypatch
) -> None:
    """L'unico motivo per cui «Nothing is lost» sarebbe falso.

    Il passo 7.4 ha reso recuperabile uno spostamento interrotto, non invisibile:
    fino al prossimo avvio metà delle tracce sta sotto il nome nuovo. Dirci sopra
    «Nothing is lost» e' la bugia piu' costosa che questo codice possa dire,
    perche' arriva nel momento in cui l'utente decide se fidarsi o copiarsi la
    chat a mano.
    """
    _wiki(tmp_path, "src")
    loop._remember_project_id("project:src")
    _all_traces(loop, "project:src")
    (tmp_path / "wikis" / "src").rename(tmp_path / "wikis" / "dst")

    with monkeypatch.context() as breaks:
        breaks.setattr(Path, "rename", _rename_that_breaks(3, and_cannot_go_back=True))
        refused = await loop._refuse_missing_project(_msg(), "project:src")

    assert refused is True
    text = published[0]
    assert pending_project_renames(loop.workspace) == [("project:src", "project:dst")], (
        "presupposto del test: qui lo spostamento *e'* rimasto a meta'"
    )
    assert "Nothing is lost" not in text, (
        "meta' delle tracce e' sotto il nome nuovo: non e' vero, e questa e' la frase "
        "che l'utente legge per decidere se il suo storico c'e' ancora"
    )
    assert "part of its history is under a new name" in text
    assert "restart" in text.lower(), "e cosa fare perche' le due metà si ricongiungano"


@pytest.mark.parametrize(
    "scenario",
    ["destination-taken", "rolled-back", "id-nowhere"],
    ids=["destinazione-occupata", "tornato-indietro", "id-che-non-c'e'"],
)
async def test_a_refusal_with_nothing_moved_still_says_nothing_is_lost(
    loop: AgentLoop, published: list[str], tmp_path: Path, monkeypatch, scenario: str
) -> None:
    """Gli altri motivi descrivono cose ferme, e lì la promessa è esatta.

    Vale la pena tenerli insieme: la correzione del 7.4 è facile da scrivere
    troppo larga — «se un motivo c'è, non promettere niente» — e toglierebbe la
    rassicurazione vera dai tre casi in cui non si è mosso un file.
    """
    _wiki(tmp_path, "src")
    loop._remember_project_id("project:src")
    made = _all_traces(loop, "project:src")

    if scenario == "id-nowhere":
        import shutil

        shutil.rmtree(tmp_path / "wikis" / "src")
        await loop._refuse_missing_project(_msg(), "project:src")
    else:
        (tmp_path / "wikis" / "src").rename(tmp_path / "wikis" / "dst")
        if scenario == "destination-taken":
            _all_traces(loop, "project:dst")
            await loop._refuse_missing_project(_msg(), "project:src")
        else:
            with monkeypatch.context() as breaks:
                breaks.setattr(Path, "rename", _rename_that_breaks(3, and_cannot_go_back=False))
                await loop._refuse_missing_project(_msg(), "project:src")

    assert all(p.exists() for p in made), "presupposto: non si e' mosso niente"
    assert pending_project_renames(loop.workspace) == []
    assert "Nothing is lost" in published[0]
    assert "renaming it back" in published[0], "e la via di uscita resta detta"


# ── L'elenco delle tracce ────────────────────────────────────────────────


def test_the_trace_list_covers_the_three_that_move(loop: AgentLoop) -> None:
    """La quarta traccia vive **dentro** la cartella, quindi si è già spostata da sé.

    ``<progetto>/.jenny/tool-results/project_<nome>/`` viaggia col rinomino; il
    suo nome resta quello vecchio e ``_cleanup_tool_result_buckets`` lo rimuove
    al primo turno. Se un giorno nascesse una quinta traccia *fuori* dalla
    cartella, va aggiunta qui — ed è questo il test che se ne accorge.
    """
    paths = project_trace_paths(loop.workspace, "project:patreon")
    kinds = {p.parent.name for p in paths}
    assert "sessions" in kinds
    assert "webui" in kinds
    assert "records" in kinds
    assert all("project_patreon" in p.name for p in paths)


# ── T4.15 — non si insegue dentro un nome che nessuno puo' riaprire ──────
#
# La cartella la rinomina l'utente **fuori** da Jenny, quindi il nome nuovo non
# e' passato da nessun controllo. ``wikis/Ricerca ETF`` non supera
# ``is_valid_project_name``, e la chat portata su ``project:Ricerca ETF`` non la
# apre ne' il canale (``session_key_for_channel``) ne' il chip (non la elenca):
# uno spostamento riuscito verso il nulla, mentre sotto il nome vecchio la chat
# funzionava ancora.


_IMPOSSIBLE_NAMES = [
    ("Ricerca ETF", "spazio"),
    ("università", "accento"),
    (".nascosto", "punto-iniziale"),
    ("progetto(2026)", "parentesi"),
]


@pytest.mark.parametrize(
    ("folder", "_why"), _IMPOSSIBLE_NAMES, ids=[w for _f, w in _IMPOSSIBLE_NAMES]
)
async def test_a_rename_into_an_impossible_name_is_not_followed(
    loop: AgentLoop, published: list[str], tmp_path: Path, folder: str, _why: str
) -> None:
    """Le tracce restano dove sono, e la vecchia chiave continua a risolverle."""
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    made = _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / folder)

    refused = await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert refused is True
    assert all(p.exists() for p in made), (
        "spostarle su una chiave che nessun canale apre e' peggio del non spostarle: "
        "sotto il nome vecchio la chat funziona ancora"
    )
    assert [
        p for p in project_trace_paths(loop.workspace, f"project:{folder}") if p.exists()
    ] == []


async def test_the_refusal_names_the_folder_and_the_rule(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Non «I could not find where it went»: la cartella si e' trovata.

    E' la riga da cui l'utente capisce se il suo storico c'e' ancora, quindi
    dev'essere quella vera — e dire cosa fare, perche' il chip quella cartella
    non la elenca (T4.1) e mandarlo la' non e' un'istruzione eseguibile.
    """
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "Ricerca ETF")

    await loop._refuse_missing_project(_msg(), "project:vecchio")

    text = published[0]
    assert "Ricerca ETF" in text, "quale cartella e' diventata: senza questo non si ripara"
    assert "cannot be the name of a conversation" in text
    assert "no spaces and no accents" in text, "la regola, non un rimando al chip"
    assert "I could not find where it went" not in text, (
        "la cartella e' stata trovata: dire il contrario e' la sola frase di questo "
        "rifiuto che l'utente usa per decidere se copiarsi la chat a mano"
    )
    assert "Nothing is lost" in text, "e qui e' vero: non si e' mosso un file"


async def test_the_refusal_leaves_no_journal_entry_open(
    loop: AgentLoop, tmp_path: Path
) -> None:
    """Il rifiuto sta **prima** del giornale, ed e' l'ordine che conta.

    Una voce aperta la finirebbe l'avvio dopo — cioe' il rifiuto diventerebbe uno
    spostamento differito verso la stessa chiave irraggiungibile, che e' peggio
    del difetto che questo test copre.
    """
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    made = _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "Ricerca ETF")

    await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert pending_project_renames(loop.workspace) == []
    assert not (tmp_path / "sessions" / ".project-rename-pending.json").exists()

    # E l'avvio dopo non completa niente: e' la seconda meta' della stessa
    # asserzione, e la sola che prova che il rifiuto non e' un rinvio.
    assert repair_pending_project_renames(loop.workspace) == []
    assert all(p.exists() for p in made)
    assert [
        p for p in project_trace_paths(loop.workspace, "project:Ricerca ETF") if p.exists()
    ] == []


async def test_the_session_is_not_invalidated_by_the_refusal(
    loop: AgentLoop, tmp_path: Path
) -> None:
    """Niente si muove, quindi la sessione in cache resta valida sotto il nome vecchio.

    ``invalidate`` esiste perche' i file stanno per cambiare nome; qui non
    cambiano, e buttarla via costerebbe una rilettura per niente.
    """
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "Ricerca ETF")

    await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert loop.sessions.get_or_create("project:vecchio").metadata[PROJECT_WIKI_ID_KEY] == (
        WIKI_ID
    ), "la sessione non e' stata invalidata: non c'era motivo"


async def test_a_rename_into_a_valid_name_is_unchanged(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """La controprova: il controllo e' sul nome, non un blocco generico."""
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    made = _traces(loop, "project:vecchio")
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "Ricerca-ETF")

    await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert "Ricerca-ETF" in published[0] and "renamed" in published[0].lower()
    assert [p for p in made if p.exists()] == []
    assert len(
        [p for p in project_trace_paths(loop.workspace, "project:Ricerca-ETF") if p.exists()]
    ) == len(made)


def test_the_mover_itself_refuses_an_unopenable_destination(
    loop: AgentLoop, tmp_path: Path
) -> None:
    """Il controllo sta anche in ``follow_renamed_project``, e non e' ridondanza.

    E' quella la funzione che apre il giornale: se un chiamante futuro coniasse
    la chiave senza controllarla, la voce aperta la porterebbe a termine l'avvio
    dopo. Qui il rifiuto e' *prima* di ogni scrittura, quindi non resta niente.
    """
    made = _all_traces(loop, "project:src")

    moved, why = follow_renamed_project(loop.workspace, "project:src", "project:Ricerca ETF")

    assert moved is False
    assert why == "the new name cannot be the name of a conversation"
    assert all(p.exists() for p in made)
    assert pending_project_renames(loop.workspace) == []


def test_the_mover_still_moves_onto_keys_that_are_not_projects(
    loop: AgentLoop, tmp_path: Path
) -> None:
    """Il controllo guarda solo le chiavi ``project:``.

    Scritto piu' largo bloccherebbe qualunque chiave futura di cui questo modulo
    non e' l'autorita' — e non e' questa la funzione che decide quali chiavi
    esistono.
    """
    made = _all_traces(loop, "project:src")

    moved, why = follow_renamed_project(loop.workspace, "project:src", "qualcosa:altro")

    assert moved is True and why is None
    assert [p for p in made if p.exists()] == []
