"""``journal_append``: la cattura di un progetto, e i suoi tre cancelli.

Passo **T2.5** del piano del taccuino. Il tool nasce da una misura sul
telefono, non da un disegno: la politica di cattura funzionava, ma passava da uno
spawn di subagent, perché ``orchestrator_mode`` toglie all'agente principale ogni
scrittura. Una corsa di subagent per una riga di testo, a ogni turno con un fatto
dentro.

I test che contano più degli altri sono due, e sono quelli sull'**append-only**:
il diario è l'ingresso del giardiniere (T4), che lo legge assumendolo immobile. Se
questo tool potesse riscrivere una riga, quel presupposto cadrebbe qui — e
cadrebbe in silenzio, perché nessuno rilegge il diario di ieri.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jenny.agent.tools.journal import JournalAppendTool
from jenny.security.workspace_access import (
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)

_DAY = date(2026, 8, 22)


def _wiki(root: Path, name: str = "viaggio") -> Path:
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True)
    (project / "raw" / "journal").mkdir(parents=True)
    return project


def _bind(project: Path | None, *, writable: bool = True):
    """Lega uno scope di turno. ``project=None`` = nessun progetto."""
    if project is None:
        return None
    scope = WorkspaceScope(
        project_path=project,
        access_mode="restricted",
        restrict_to_workspace=True,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=True, workspace=project
        ),
        writable=writable,
    )
    return bind_workspace_scope(scope)


@pytest.fixture
def tool() -> JournalAppendTool:
    return JournalAppendTool(today=lambda: _DAY)


def _page(project: Path) -> Path:
    return project / "raw" / "journal" / "20260822.md"


# ── Quel che scrive ──────────────────────────────────────────────────────────


async def test_the_first_line_creates_the_page_with_its_heading(tool, tmp_path) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text="il furgone ha le gomme da cambiare", attribution="said")
    finally:
        reset_workspace_scope(token)

    text = _page(project).read_text(encoding="utf-8")
    assert text.startswith("# 2026-08-22\n\n")
    assert "— [said] il furgone ha le gomme da cambiare\n" in text
    assert "journal/20260822.md" in out


async def test_the_timestamp_is_added_and_the_text_is_not(tool, tmp_path) -> None:
    """L'ora la mette il codice: chiedendola al modello si otterrebbe un orario
    plausibile invece di quello vero, che è il tipo di dato che non si nota mai
    sbagliato."""
    import re

    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        await tool.execute(text="12:00 non è l'ora, è il testo", attribution="said")
    finally:
        reset_workspace_scope(token)

    line = _page(project).read_text(encoding="utf-8").strip().splitlines()[-1]
    assert re.fullmatch(r"- \d{2}:\d{2} — \[said\] 12:00 non è l'ora, è il testo", line), line


async def test_a_multiline_text_becomes_one_line(tool, tmp_path) -> None:
    """Il formato del diario è una riga per fatto, e la riga la fa il tool: un
    a-capo passato dal modello spezzerebbe il file, e il giardiniere leggerebbe
    due mezzi fatti."""
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        await tool.execute(text="prima parte\n\nseconda   parte\n", attribution="said")
    finally:
        reset_workspace_scope(token)

    body = _page(project).read_text(encoding="utf-8")
    entries = [ln for ln in body.splitlines() if ln.startswith("- ")]
    assert len(entries) == 1, f"un testo su più righe deve fare una voce, non {len(entries)}"
    assert body.strip().endswith("— [said] prima parte seconda parte")


# ── L'append-only ────────────────────────────────────────────────────────────


async def test_a_second_call_appends_and_leaves_the_first_line_alone(tool, tmp_path) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        await tool.execute(text="primo fatto")
        first = _page(project).read_bytes()
        await tool.execute(text="secondo fatto")
    finally:
        reset_workspace_scope(token)

    after = _page(project).read_bytes()
    assert after.startswith(first), "la coda è cresciuta ma la testa deve essere identica"
    lines = [ln for ln in after.decode().splitlines() if ln.startswith("- ")]
    assert len(lines) == 2
    assert "primo fatto" in lines[0] and "secondo fatto" in lines[1]


async def test_it_does_not_touch_a_page_written_by_someone_else(tool, tmp_path) -> None:
    """Su un diario che esiste già — scritto a mano, o dal giorno prima — non c'è
    nessuna riscrittura: si apre in coda e basta. È la proprietà su cui poggia
    il giardiniere, e il modo più facile di perderla era un leggi-modifica-scrivi.
    """
    project = _wiki(tmp_path)
    _page(project).write_text("# 2026-08-22\n\n- 09:00 — scritto a mano\n", encoding="utf-8")
    token = _bind(project)
    try:
        await tool.execute(text="aggiunto dal tool")
    finally:
        reset_workspace_scope(token)

    text = _page(project).read_text(encoding="utf-8")
    assert text.startswith("# 2026-08-22\n\n- 09:00 — scritto a mano\n")
    assert text.count("# 2026-08-22") == 1, "l'intestazione non si riscrive"


# ── I cancelli ───────────────────────────────────────────────────────────────


async def test_no_project_means_no_journal_and_it_says_where_one_lives(tool, tmp_path) -> None:
    """Fuori da un progetto la cattura non ha una cartella dove andare, e il
    rifiuto **dice dove** invece di dire solo che qui non si può — la lezione del
    passo 6: un rifiuto che manda via a mani vuote costa un altro turno."""
    plain = tmp_path / "workspace"
    plain.mkdir()
    token = _bind(plain)
    try:
        out = await tool.execute(text="un fatto")
    finally:
        reset_workspace_scope(token)

    assert "No journal here" in out
    assert "chip above the composer" in out
    assert not list(plain.rglob("*.md"))


async def test_with_no_bound_scope_it_refuses_too(tool) -> None:
    """Nessuno scope legato vuol dire "fuori da un turno": test, ispezione,
    sessioni interne. Non c'è un progetto, quindi non c'è un diario — e il
    default deve essere il rifiuto, non una cartella indovinata."""
    assert "No journal here" in await tool.execute(text="un fatto")


async def test_read_only_refuses_and_writes_nothing(tool, tmp_path) -> None:
    project = _wiki(tmp_path)
    token = _bind(project, writable=False)
    try:
        out = await tool.execute(text="un fatto")
    finally:
        reset_workspace_scope(token)

    assert "read-only" in out.lower()
    assert not _page(project).exists()


async def test_the_missing_project_wins_over_read_only(tool, tmp_path) -> None:
    """L'ordine dei due rifiuti non è indifferente: dire «sola lettura» a chi non
    ha nemmeno una cartella dove scrivere lo manda a cercare l'interruttore per
    un problema che l'interruttore non risolve."""
    plain = tmp_path / "workspace"
    plain.mkdir()
    token = _bind(plain, writable=False)
    try:
        out = await tool.execute(text="un fatto")
    finally:
        reset_workspace_scope(token)

    assert "No journal here" in out


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
async def test_an_empty_text_writes_nothing(tool, tmp_path, text) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text=text)
    finally:
        reset_workspace_scope(token)

    assert "Nothing to append" in out
    assert not _page(project).exists()


async def test_page_material_is_refused_instead_of_truncated(tool, tmp_path) -> None:
    """Un fatto sta in una riga. Quel che non ci sta è materiale da pagina, e il
    posto di una pagina non lo decide la cattura: troncare in silenzio
    scriverebbe metà fatto e lo farebbe sembrare intero."""
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text="x" * 501)
    finally:
        reset_workspace_scope(token)

    assert "Too long" in out and "page material" in out
    assert not _page(project).exists()


async def test_the_ceiling_is_measured_on_the_text_not_on_the_marker(tmp_path) -> None:
    """Il marcatore d'origine non si paga sul tetto del chiamante.

    ``[recovered] `` sono dodici caratteri che il modello non scrive e non vede:
    prefissati **prima** del controllo restringevano l'allowance a 488, quindi una
    riga da 495 tornava rifiutata citando un limite di 500 che il chiamante non
    aveva sforato — un rifiuto su cui non si può agire. Il tetto è del fatto.
    """
    project = _wiki(tmp_path)
    tool = JournalAppendTool(today=lambda: _DAY, origin_marker="[recovered]")
    token = _bind(project)
    try:
        out = await tool.execute(text="x" * 495)
    finally:
        reset_workspace_scope(token)

    assert "Too long" not in out
    assert "[recovered] " + "x" * 495 in _page(project).read_text(encoding="utf-8")


async def test_page_material_is_refused_even_with_a_marker(tmp_path) -> None:
    """Il gemello del test qui sopra: spostando il controllo non si è aperto il
    tetto. Il conto è su ``text``, e 501 caratteri restano materiale da pagina."""
    project = _wiki(tmp_path)
    tool = JournalAppendTool(today=lambda: _DAY, origin_marker="[recovered]")
    token = _bind(project)
    try:
        out = await tool.execute(text="x" * 501)
    finally:
        reset_workspace_scope(token)

    assert "Too long" in out and "501 characters" in out
    assert not _page(project).exists()


# ── L'orologio ───────────────────────────────────────────────────────────────


async def test_the_page_and_the_time_come_from_one_reading_of_the_clock(tmp_path) -> None:
    """Un turno a cavallo della mezzanotte non scrive ``- 00:00 —`` su ieri.

    L'orologio finto avanza di un giorno **a ogni lettura**: con due letture — una
    per la pagina, una per l'ora, com'era — la riga finiva nel file di un giorno e
    portava l'ora di un altro. Un fatto datato a un giorno in cui non è stato
    detto, in un file append-only che nessuno rilegge.
    """
    from datetime import datetime

    project = _wiki(tmp_path)
    readings = iter([
        datetime(2026, 8, 22, 23, 59, 40),
        datetime(2026, 8, 23, 0, 0, 30),
        datetime(2026, 8, 24, 0, 0, 30),
    ])
    tool = JournalAppendTool(now=lambda: next(readings))
    token = _bind(project)
    try:
        await tool.execute(text="si parte domani", attribution="said")
    finally:
        reset_workspace_scope(token)

    assert not (project / "raw" / "journal" / "20260823.md").exists()
    assert "- 23:59 — [said] si parte domani" in _page(project).read_text(encoding="utf-8")


async def test_the_hour_is_the_configured_timezone_not_the_system_one(tmp_path) -> None:
    """L'ora della riga è quella che il modello ha in testa.

    Il prompt gli dice l'ora nel fuso configurato (``context.py``,
    ``current_time_str``); la riga di diario la scriveva col fuso di **sistema**,
    e su un device fuori dal proprio fuso il fatto entrava nel diario a un'ora in
    cui non era stato detto.

    Il confronto è fra due fusi a venticinque ore di distanza invece che contro un
    valore atteso: l'orologio è quello vero, quindi un'asserzione su una stringa
    esatta cadrebbe al cambio di minuto — mentre due ore a venticinque ore di
    distanza non possono coincidere mai.
    """
    project = _wiki(tmp_path)

    hours = []
    for tz in ("Pacific/Kiritimati", "Pacific/Midway"):
        token = _bind(project)
        try:
            out = await JournalAppendTool(
                today=lambda: _DAY, timezone=tz
            ).execute(text="un fatto")
        finally:
            reset_workspace_scope(token)
        assert "Appended" in out, out
        hours.append(_page(project).read_text(encoding="utf-8").splitlines()[-1][2:7])

    assert hours[0] != hours[1], hours


# ── Il perché esiste ─────────────────────────────────────────────────────────


def test_it_is_reachable_by_the_orchestrator() -> None:
    """È la ragione del tool. L'agente principale gira con
    ``orchestrator_mode`` acceso — niente ``python_exec``, niente scrittura,
    niente patch — quindi senza questo tool la cattura è per forza uno spawn: una
    corsa di subagent per una riga di testo, a ogni turno con un fatto dentro.
    Misurato sul telefono il 22/08, ed è la sola ragione per cui una scrittura
    entra in uno scope fatto di sola lettura e controllo.
    """
    assert "orchestrator" in JournalAppendTool._scopes


def test_it_is_registered_in_the_loader() -> None:
    """La registrazione è esplicita (``TOOLS`` + la lista in ``loader.py``): un
    tool che esiste e non è in lista è un tool che nessuno chiamerà mai, e non
    fallisce — semplicemente non c'è."""
    from jenny.agent.tools import loader

    src = Path(loader.__file__).read_text(encoding="utf-8")
    assert '"journal",' in src

    from jenny.agent.tools.journal import TOOLS

    assert TOOLS == [JournalAppendTool]


# ── L'attribuzione: di chi è il fatto che la riga registra ───────────────────
#
# Il difetto (D1): il 24/08 Jenny ha chiesto «il telescopio te lo porti, *o quello
# resta a casa*?», l'utente ha risposto «il telescopio che cenrtra?» — una domanda,
# nessuna scelta — e la cattura ha registrato «resta a casa» come decisione
# dell'utente. Poi il giardiniere l'ha promossa a `state: decided`.
#
# Il bit lo dichiara il modello. Quel che il codice impone è la *conseguenza*, e
# sta in `tests/agent/test_gardener_provenance.py`.


@pytest.mark.parametrize(
    ("attribution", "marker"),
    [("said", "[said]"), ("inferred", "[inferred]")],
)
async def test_the_attribution_becomes_the_line_marker(tool, tmp_path, attribution, marker) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text="si parte a maggio", attribution=attribution)
    finally:
        reset_workspace_scope(token)

    assert f"— {marker} si parte a maggio" in _page(project).read_text(encoding="utf-8")
    # Il marcatore torna anche nella risposta: il modello deve vedere cosa ha scritto.
    assert marker in out


@pytest.mark.parametrize("given", ["", "   ", "detto", "maybe", "true", "said,inferred"])
async def test_a_missing_or_unknown_attribution_falls_back_to_inferred(tool, tmp_path, given) -> None:
    """**Fail-closed, e detto.** Dedurre «detto» dal silenzio è il modo in cui
    un'omissione diventa una certificazione falsa, che è D1. E la cattura non
    fallisce per questo: un fatto perduto costa più di un fatto sottostimato.

    Maiuscole e spazi **non** sono in questa lista: il tool normalizza, e quel
    caso ha un test suo. Qui ci sono solo valori che non sono `said` nemmeno
    normalizzati — compreso `detto`, che è la traduzione ovvia e non è il token.
    """
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text="un fatto", attribution=given)
    finally:
        reset_workspace_scope(token)

    body = _page(project).read_text(encoding="utf-8")
    assert "[inferred] un fatto" in body
    assert "[said]" not in body
    assert "inferred" in out, "il ripiegamento va detto: silenzioso, perde un fatto vero"


async def test_the_attribution_is_case_and_space_insensitive(tool, tmp_path) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        await tool.execute(text="un fatto", attribution="  SaId  ")
    finally:
        reset_workspace_scope(token)

    assert "[said] un fatto" in _page(project).read_text(encoding="utf-8")


async def test_the_cap_is_still_measured_on_the_fact(tmp_path) -> None:
    """La regressione che `journal.py` documenta per `[recovered]`: col marcatore
    messo *prima* del controllo, una riga da 495 caratteri veniva rifiutata citando
    un limite di 500 che il chiamante non aveva sforato — un rifiuto su cui non si
    può agire. Vale per i marcatori nuovi come per quello vecchio.
    """
    tool = JournalAppendTool(today=lambda: _DAY)
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text="x" * 495, attribution="said")
    finally:
        reset_workspace_scope(token)

    assert "Too long" not in out
    assert "[said] " + "x" * 495 in _page(project).read_text(encoding="utf-8")


async def test_a_recovery_pass_keeps_its_own_marker_and_ignores_attribution(tmp_path) -> None:
    """Un marcatore per riga, e chi ce l'ha fisso vince.

    La cassetta di una passata monta `origin_marker="[recovered]"`, che vale *come
    detto* per contratto del suo prompt: chiederle anche l'attribuzione sarebbe
    offrirle una scelta che non ha. Il parametro resta nello schema — lo schema è
    del tool, non dell'istanza — e qui va ignorato, anche quando dice `inferred`.
    """
    project = _wiki(tmp_path)
    tool = JournalAppendTool(today=lambda: _DAY, root=project, origin_marker="[recovered]")

    out = await tool.execute(text="base Roma", attribution="inferred")

    body = _page(project).read_text(encoding="utf-8")
    assert "— [recovered] base Roma" in body
    assert "[inferred]" not in body, "l'attribuzione non deve poter degradare un recupero"
    assert "[said]" not in body, "e nemmeno raddoppiare il prefisso"
    assert "inferred" not in out, "niente nota di ripiegamento: non ha ripiegato niente"


# ── Lo specchio nel diario della memoria ─────────────────────────────────────


def _history(store_root: Path) -> list[dict]:
    import json

    path = store_root / "memory" / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def test_a_line_leaves_its_short_copy_in_the_diary(tmp_path) -> None:
    """Il fatto, chi l'ha detto, di quale quaderno parla e dove sta l'originale."""
    from jenny.agent.memory import MemoryStore
    from jenny.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )

    project = _wiki(tmp_path, "film")
    tool = JournalAppendTool(today=lambda: _DAY, memory_store=MemoryStore(tmp_path))
    token = _bind(project)
    request = bind_request_context(RequestContext(
        channel="websocket", chat_id="project:film", session_key="project:film"))
    try:
        await tool.execute(text="Ha visto Perfect Days, gli è piaciuto", attribution="said")
    finally:
        reset_request_context(request)
        reset_workspace_scope(token)
    [row] = _history(tmp_path)
    assert row["content"] == "Ha visto Perfect Days, gli è piaciuto"
    assert row["kind"] == "said" and row["about"] == "film"
    assert row["session_key"] == "project:film"
    assert row["ref"] == "wikis/film/raw/journal/20260822.md:3"
    assert "untrusted" not in row
    assert _page(project).read_text().splitlines()[2].endswith(row["content"])


async def test_a_line_from_a_tainted_turn_is_marked_untrusted(tmp_path) -> None:
    from jenny.agent.memory import MemoryStore
    from jenny.security.subagent_policy import TurnTaint, enter_turn_taint

    project = _wiki(tmp_path)
    tool = JournalAppendTool(today=lambda: _DAY, memory_store=MemoryStore(tmp_path))
    token = _bind(project)
    try:
        with enter_turn_taint(TurnTaint(["web_fetch:evil.example"])):
            await tool.execute(text="Il volo parte alle 7", attribution="inferred")
    finally:
        reset_workspace_scope(token)
    [row] = _history(tmp_path)
    assert row["untrusted"] is True and row["kind"] == "inferred"
    assert row["session_key"] == "project:viaggio"


async def test_a_recovered_line_is_not_mirrored(tmp_path) -> None:
    from jenny.agent.memory import MemoryStore

    project = _wiki(tmp_path)
    tool = JournalAppendTool(today=lambda: _DAY, root=project, origin_marker="[recovered]",
                             memory_store=MemoryStore(tmp_path))
    await tool.execute(text="Parte il 3 settembre")
    assert "[recovered]" in _page(project).read_text()
    assert _history(tmp_path) == []


async def test_a_failing_diary_does_not_lose_the_journal_line(tmp_path) -> None:
    class _Broken:
        workspace = tmp_path

        def append_history(self, *a, **k):
            raise OSError("disco pieno")

    project = _wiki(tmp_path)
    tool = JournalAppendTool(today=lambda: _DAY, memory_store=_Broken())
    token = _bind(project)
    try:
        out = await tool.execute(text="Parte il 3 settembre", attribution="said")
    finally:
        reset_workspace_scope(token)
    assert out.startswith("Appended")
    assert "Parte il 3 settembre" in _page(project).read_text()
