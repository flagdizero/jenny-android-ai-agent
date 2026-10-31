"""Il review pass di Dream: il run che può solo ridurre.

Tre di questi test non provano una feature ma un'assenza — cursore, helper di
commit, snapshot. Sono le tre cose che il review pass condivide con Dream per
somiglianza e non deve condividere per comportamento: ognuna, se ci scivolasse
dentro, romperebbe qualcosa che nessun altro test guarda.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent import dream_review as dream_review_module
from jenny.agent.dream_review import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NO_CHANGE,
    review_session_key,
    run_dream_review,
)
from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import FileBudget, budget_report
from jenny.session.keys import is_internal_session_key
from jenny.session.manager import SessionManager
from jenny.utils import prompt_templates
from jenny.utils.helpers import sync_workspace_templates

# Testo iniziale dei tre file misurati. Deve essere abbastanza lungo da poter
# essere accorciato in modo visibile dai test che simulano una potatura.
_MEMORY_TEXT = "# Memory\n" + "".join(f"- fact number {i}\n" for i in range(40))
_USER_TEXT = "# User\n- Name: Marco\n- Timezone: Europe/Rome\n"
_SOUL_TEXT = "# Soul\n- Helpful, concise.\n"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``MemoryStore`` su un workspace con i template davvero estratti.

    ``render_template`` carica da ``get_workspace_path()``, non dal package: un
    prompt che non arriva nel workspace non si rende affatto, quindi montare un
    workspace vero è l'unico modo di provare il rendering del review pass (ed è
    anche ciò che rende leggibile ``agent/dream.md``, v.
    ``TestTheCriteriaAreReachable``). L'ambiente Jinja è memoizzato per processo
    (``lru_cache``): va invalidato prima **e** dopo, o la prima chiamata della
    suite fissa la root per tutte le altre.
    """
    from jenny.runtime.context import get_runtime_context

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    sync_workspace_templates(workspace, silent=True)
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", workspace)
    prompt_templates._environment.cache_clear()

    memory_store = MemoryStore(workspace)
    memory_store.memory_file.write_text(_MEMORY_TEXT, encoding="utf-8")
    memory_store.user_file.write_text(_USER_TEXT, encoding="utf-8")
    memory_store.soul_file.write_text(_SOUL_TEXT, encoding="utf-8")

    yield memory_store

    prompt_templates._environment.cache_clear()


class _FakeAgent:
    """Sostituto di ``AgentLoop`` per il solo ``process_direct``.

    *effect* è la coroutine che simula quel che il modello fa durante il turno:
    riceve il registry del run, così un test può potare davvero un file o
    provare a scrivere passando dai tool veri invece che dal filesystem.
    """

    def __init__(
        self,
        *,
        stop_reason: str = "completed",
        effect: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.stop_reason = stop_reason
        self.effect = effect
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def process_direct(self, content: str, **kwargs: Any) -> Any:
        self.calls.append({"prompt": content, **kwargs})
        if self.error is not None:
            raise self.error
        if self.effect is not None:
            await self.effect(kwargs["tools"])
        return SimpleNamespace(metadata={"_stop_reason": self.stop_reason})

    @property
    def prompt(self) -> str:
        assert self.calls, "process_direct non è mai stato chiamato"
        return self.calls[-1]["prompt"]

    @property
    def session_key(self) -> str:
        return self.calls[-1]["session_key"]


def _report(store: MemoryStore, *, memory: int = 6000, user: int = 3000, soul: int = 0):
    return budget_report(store, memory_chars=memory, user_chars=user, soul_chars=soul)


async def _run(store: MemoryStore, agent: _FakeAgent, **kwargs: Any):
    """Chiama il review pass con i default dei test (report fresco, no snapshot)."""
    report = kwargs.pop("report", None)
    if report is None:
        report = _report(store)
    return await run_dream_review(
        agent,
        store=store,
        report=report,
        snapshotted=kwargs.pop("snapshotted", False),
        **kwargs,
    )


def _shrink(store: MemoryStore, *, to: str = "# Memory\n- one fact\n"):
    """Effetto che pota MEMORY.md, come farebbe il modello."""

    async def effect(_tools: Any) -> None:
        store.memory_file.write_text(to, encoding="utf-8")

    return effect


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# ---------------------------------------------------------------------------
# Le tre cose che non deve fare
# ---------------------------------------------------------------------------


class TestTheThreeThingsItMustNotDo:
    async def test_it_never_touches_the_dream_cursor(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il review pass non processa storia: non ha nessun cursore da avanzare.

        Avanzarlo qui dichiarerebbe digerite delle voci di ``history.jsonl`` che
        nessuno ha letto — perse per sempre — e riaprirebbe proprio il livelock
        che il review pass esiste per rompere. Il test spegne entrambi gli
        accessori: anche solo *leggere* il cursore qui dentro è il primo passo
        verso lo scriverlo.
        """

        def _boom(*_args: Any, **_kwargs: Any):
            raise AssertionError("il review pass ha toccato .dream_cursor")

        store.set_last_dream_cursor(42)
        monkeypatch.setattr(store, "get_last_dream_cursor", _boom)
        monkeypatch.setattr(store, "set_last_dream_cursor", _boom)

        outcome = await _run(store, _FakeAgent(effect=_shrink(store)))

        assert outcome.status == STATUS_COMPLETED
        assert store._dream_cursor_file.read_text(encoding="utf-8") == "42"

    async def test_it_does_not_ask_whether_progress_may_be_committed(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``dream_should_advance_cursor`` risponde a una domanda che qui non esiste.

        Quella regola dice "questo input può dirsi digerito?" e, non avendo
        scritture riuscite, risponde no. Usarla come esito del review pass
        marchierebbe come fallito ogni run che non aveva niente da potare —
        cioè il caso che il prompt dichiara esplicitamente valido.
        ``internal_run_completed`` resta l'unico helper consentito.

        Le ``setattr`` da sole non bastano più. Da quando la regola vive in
        ``jenny/agent/internal_run.py`` la si raggiunge anche con
        ``from jenny.agent.internal_run import internal_run_should_commit``, che
        lega il nome all'import e ignora qualunque patch — ed è proprio la forma
        scritta in ``gardener.py``, cioè quella che un lettore
        copierebbe per prima. Da qui la seconda metà del test, che guarda il
        sorgente invece della chiamata.
        """

        def _boom(*_args: Any, **_kwargs: Any):
            raise AssertionError("esito deciso con gli helper del cursore")

        monkeypatch.setattr(MemoryStore, "dream_should_advance_cursor", staticmethod(_boom))
        monkeypatch.setattr(MemoryStore, "internal_run_should_commit", staticmethod(_boom))

        outcome = await _run(store, _FakeAgent(effect=_shrink(store)))

        assert outcome.status == STATUS_COMPLETED

        source = Path(dream_review_module.__file__ or "").read_text(encoding="utf-8")
        # Il docstring di modulo *nomina* i due helper per spiegare perché non li
        # usa, quindi si guardano le sole righe di codice.
        code = [
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        ]
        code_text = "\n".join(code).split('"""')
        body = "".join(code_text[::2])  # fuori dai docstring/blocchi tripli
        for banned in ("internal_run_should_commit", "dream_should_advance_cursor"):
            assert banned not in body, f"{banned} è entrato in dream_review.py"

    async def test_it_does_not_snapshot_the_workspace(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lo snapshot è del chiamante; qui arriva solo il flag che lo descrive.

        Due difetti in uno se scivolasse dentro: una copia del workspace a ogni
        review pass, e — peggio — un run che si crea da sé la reversibilità che
        il prompt promette, scollegando il ramo "puoi potare, è recuperabile"
        dalla configurazione che quella promessa la rende vera.
        """
        params = inspect.signature(run_dream_review).parameters
        # ``reporter`` e' la bolla di lavoro di ``/dream`` (05/10/2026): un
        # osservatore che riceve progresso e riprova, senza modo di fare un
        # checkpoint.
        assert set(params) == {
            "agent", "store", "report", "snapshotted", "write_size_guard", "reporter",
        }
        # ``snapshotted`` è un bool, non un gancio: non c'è modo di passargli
        # una funzione da chiamare per fare il checkpoint.
        assert params["snapshotted"].annotation == "bool"

        # Il neutralizzatore della contabilità che stava qui non serve più: dal
        # 25/08 ``run_dream_review`` non registra niente da sé (v.
        # ``TestTokenAccounting``), quindi non c'è nessuna scrittura sotto la dir
        # webui da spegnere.
        before = _tree(store.workspace)
        await _run(store, _FakeAgent(), snapshotted=True)

        assert _tree(store.workspace) == before


# ---------------------------------------------------------------------------
# Il prompt
# ---------------------------------------------------------------------------


class TestPromptRendering:
    async def test_the_migration_is_told_to_be_atomic(self, store: MemoryStore) -> None:
        """La prevenzione del passo 3, che senza questo test non è pinnata da niente.

        Spostare un fatto fra due file non è due modifiche indipendenti: la
        cancellazione dalla fonte rimpicciolisce e passa sempre, l'aggiunta alla
        destinazione può essere rifiutata perché quel file è già al tetto. In
        quell'ordine il fatto non è in nessuno dei due. ``apply_patch`` interpella
        il guard su tutti i bersagli prima di scrivere un byte e rolla indietro,
        quindi è la sola forma in cui l'esito è sempre uno dei due giusti.
        """
        agent = _FakeAgent()
        await _run(store, agent)

        assert "one `apply_patch` call carrying **both** halves" in agent.prompt
        assert "all-or-nothing" in agent.prompt
        # E la via di riserva, per quando il modello la spezza comunque.
        assert "write the destination first" in agent.prompt
        assert "leave the source exactly as it is" in agent.prompt

    async def test_snapshotted_true_promises_reversibility(self, store: MemoryStore) -> None:
        agent = _FakeAgent()
        await _run(store, agent, snapshotted=True)

        assert "## Your edits are reversible" in agent.prompt
        assert "## Your edits are not reversible" not in agent.prompt

    async def test_snapshotted_false_says_the_deletion_is_final(
        self, store: MemoryStore
    ) -> None:
        """Il ramo conservativo: senza checkpoint il prompt deve dirlo.

        Dire "puoi recuperare tutto" attaccato alla frase il cui scopo è far
        cancellare di più, quando non è vero, è il modo più diretto di perdere
        memoria che l'utente non ha altrove.
        """
        agent = _FakeAgent()
        await _run(store, agent, snapshotted=False)

        assert "## Your edits are not reversible" in agent.prompt
        assert "when a decision is close, keep" in agent.prompt

    async def test_the_gauge_is_the_review_variant(self, store: MemoryStore) -> None:
        """``for_review=True`` non è opzionale.

        La variante incrementale del gauge dice "consolidate before adding": in
        un run dove per definizione non si aggiunge niente è un'istruzione che
        non descrive nessuna azione disponibile, e in un prompt che deve far
        cancellare è rumore che spinge nella direzione opposta.
        """
        agent = _FakeAgent()
        await _run(store, agent)

        assert "consolidate before adding" not in agent.prompt
        assert "does not need to shrink further" in agent.prompt

    async def test_the_measures_in_the_prompt_are_the_ones_in_the_outcome(
        self, store: MemoryStore
    ) -> None:
        """Gauge e ``before`` vengono dallo stesso report, o raccontano due run diversi."""
        agent = _FakeAgent()
        report = _report(store)
        outcome = await _run(store, agent, report=report)

        assert outcome.before == {item.label: item.chars for item in report}
        chars = outcome.before["MEMORY.md"]
        assert f"MEMORY.md [{chars * 100 // 6000}% — {chars:,}/6,000 chars]" in agent.prompt
        # SOUL.md ha budget 0: misurato, non applicato.
        assert f"SOUL.md [{outcome.before['SOUL.md']:,} chars — no budget]" in agent.prompt

    async def test_before_comes_from_the_report_not_from_a_fresh_measure(
        self, store: MemoryStore
    ) -> None:
        """Il chiamante ha appena misurato: rimisurare qui è lavoro doppio e divergente.

        Con un report volutamente diverso dal disco, ``before`` deve seguire il
        report — è il numero che il modello ha visto nel gauge, e l'unico
        rispetto al quale il delta del run significhi qualcosa.
        """
        stale = [FileBudget(label="MEMORY.md", path=store.memory_file, chars=999, budget=6000)]

        outcome = await _run(store, _FakeAgent(), report=stale)

        assert outcome.before == {"MEMORY.md": 999}


# ---------------------------------------------------------------------------
# Esito
# ---------------------------------------------------------------------------


class TestStatus:
    async def test_a_file_that_shrank_is_a_completed_run(self, store: MemoryStore) -> None:
        outcome = await _run(store, _FakeAgent(effect=_shrink(store)))

        assert outcome.status == STATUS_COMPLETED
        assert outcome.after["MEMORY.md"] < outcome.before["MEMORY.md"]
        assert outcome.freed == outcome.before["MEMORY.md"] - outcome.after["MEMORY.md"]

    async def test_a_run_that_changes_nothing_is_not_a_failure(
        self, store: MemoryStore
    ) -> None:
        """Il prompt lo dice al modello ("a valid outcome, not a failed one").

        Se il codice dicesse il contrario, il chiamante conterebbe come guasto
        proprio il comportamento che il prompt chiede: non fabbricare un edit
        pur di giustificare il turno.
        """
        outcome = await _run(store, _FakeAgent())

        assert outcome.status == STATUS_NO_CHANGE
        assert outcome.before == outcome.after
        assert outcome.freed == 0

    async def test_an_unclean_turn_is_a_failure(self, store: MemoryStore) -> None:
        outcome = await _run(store, _FakeAgent(stop_reason="error"))

        assert outcome.status == STATUS_FAILED

    async def test_an_unclean_turn_stays_a_failure_even_if_a_file_shrank(
        self, store: MemoryStore
    ) -> None:
        """Lo status descrive la salute del run, non un suo effetto collaterale.

        Un turno interrotto a metà che per caso ha accorciato un file resta un
        turno interrotto: di quanto abbia ridotto lo dicono ``before``/``after``,
        che restano popolati.
        """
        outcome = await _run(
            store, _FakeAgent(stop_reason="error", effect=_shrink(store))
        )

        assert outcome.status == STATUS_FAILED
        assert outcome.after["MEMORY.md"] < outcome.before["MEMORY.md"]

    async def test_an_exception_is_reported_not_raised(self, store: MemoryStore) -> None:
        """Un review pass è manutenzione: non deve portarsi via il tick del cron."""
        outcome = await _run(store, _FakeAgent(error=RuntimeError("provider down")))

        assert outcome.status == STATUS_FAILED
        assert set(outcome.before) == {"MEMORY.md", "USER.md", "SOUL.md"}
        assert set(outcome.after) == set(outcome.before)


# ---------------------------------------------------------------------------
# Session key
# ---------------------------------------------------------------------------


class TestSessionKey:
    async def test_the_run_uses_a_dream_prefixed_review_key(self, store: MemoryStore) -> None:
        agent = _FakeAgent()
        await _run(store, agent)

        assert agent.session_key.startswith("dream:review-")
        assert agent.calls[-1]["ephemeral"] is True

    def test_the_key_is_recognised_as_internal(self) -> None:
        """Se non lo fosse, il review pass comparirebbe negli elenchi user-facing."""
        assert is_internal_session_key(review_session_key())

    def test_the_session_file_is_pruned_by_the_existing_dream_pruner(
        self, tmp_path: Path
    ) -> None:
        """Il prefisso ``dream:`` è la ragione per cui non serve un secondo pruner.

        ``prune_dream_sessions`` globba ``dream_*.jsonl``. Se la chiave fosse
        coniata come ``review:...`` il glob non matcherebbe, e ogni review pass
        lascerebbe dietro un file di sessione per sempre — un accumulo silenzioso
        su un telefono, senza nessun errore a segnalarlo.
        """
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        keys = [f"dream:review-2026081{i}-100000" for i in range(11)]
        for key in keys:
            (sessions_dir / f"{SessionManager.safe_key(key)}.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )

        assert len(list(sessions_dir.glob("dream_*.jsonl"))) == len(keys)

        removed = MemoryStore.prune_dream_sessions(sessions_dir, keep=10)

        # Il pruner restituisce la chiave originale, non il nome del file: se il
        # round-trip non tornasse, il chiamante non saprebbe quale cache svuotare.
        assert removed and removed[0] in keys
        assert len(list(sessions_dir.glob("dream_*.jsonl"))) == 10


# ---------------------------------------------------------------------------
# Tool del run
# ---------------------------------------------------------------------------


class TestTools:
    async def test_the_write_size_guard_reaches_the_tools(self, store: MemoryStore) -> None:
        """Il guard passato al review pass deve arrivare fino alla scrittura.

        Se si perdesse per strada il run girerebbe uguale e senza errori, solo
        con il budget disattivato: la sola prova che è arrivato è un rifiuto
        prodotto da un tool vero.
        """
        seen: list[Path] = []
        results: list[str] = []

        def guard(path: Path, _text: str) -> str | None:
            seen.append(path)
            return "Refused: over budget."

        async def effect(tools: Any) -> None:
            results.append(
                await tools.execute(
                    "edit_file",
                    {"path": "memory/MEMORY.md", "old_text": "fact number 0", "new_text": "x"},
                )
            )

        await _run(store, _FakeAgent(effect=effect), write_size_guard=guard)

        assert seen, "il guard non è mai stato invocato"
        assert results and "Refused: over budget." in results[0]
        # Rifiutata davvero: il file su disco è intatto.
        assert store.memory_file.read_text(encoding="utf-8") == _MEMORY_TEXT

    async def test_without_a_guard_the_tools_write_normally(self, store: MemoryStore) -> None:
        """Il default resta "misurato ma non applicato": nessuna scrittura rifiutata."""

        async def effect(tools: Any) -> None:
            await tools.execute(
                "edit_file",
                {"path": "memory/MEMORY.md", "old_text": _MEMORY_TEXT, "new_text": "# Memory\n"},
            )

        outcome = await _run(store, _FakeAgent(effect=effect))

        assert outcome.status == STATUS_COMPLETED
        assert store.memory_file.read_text(encoding="utf-8") == "# Memory\n"


class TestAMigrationThatDeletesButCannotLand:
    """Il passo 3 del route down può distruggere il fatto che doveva spostare.

    Riprodotto sulle misure del device il 2026-08-17. ``memory/MEMORY.md`` è oltre
    il suo tetto, e il route down ordina di spostarci il contesto di progetto
    *"deleted from here"* — da ``USER.md``. La cancellazione dalla fonte è una
    scrittura che rimpicciolisce, quindi sempre accettata; l'aggiunta alla
    destinazione è oltre budget e non rimpicciolisce, quindi rifiutata. Il fatto
    finisce in nessuno dei due file.

    Prima, l'esito era ``completed`` con ``freed > 0``, perché ``USER.md`` era
    diminuito: il log diceva *"freed N chars"* e nessun contatore veniva letto.
    L'ordine imposto nel prompt è la prevenzione; questa classe fissa il
    rilevamento, che serve perché su questo progetto è già stato misurato che il
    modello scavalca le istruzioni.
    """

    @staticmethod
    def _refuses_memory(path: Path, _text: str) -> str | None:
        if path.name == "MEMORY.md":
            return "Write refused: MEMORY.md would go over its char budget."
        return None

    async def _destructive_migration(self, store: MemoryStore) -> Any:
        """Cancella il fatto da ``USER.md``, poi non riesce a scriverlo altrove."""

        async def effect(tools: Any) -> None:
            await tools.execute(
                "edit_file",
                {
                    "path": "USER.md",
                    "old_text": "- Timezone: Europe/Rome\n",
                    "new_text": "",
                },
            )
            await tools.execute(
                "edit_file",
                {
                    "path": "memory/MEMORY.md",
                    "old_text": "# Memory\n",
                    "new_text": "# Memory\n- Timezone: Europe/Rome\n",
                },
            )

        return await _run(
            store,
            _FakeAgent(effect=effect),
            write_size_guard=self._refuses_memory,
        )

    async def test_the_pass_is_not_reported_as_completed(self, store: MemoryStore) -> None:
        outcome = await self._destructive_migration(store)

        assert outcome.status == STATUS_FAILED
        assert outcome.unresolved_refusals == 1

    async def test_and_it_would_have_looked_like_a_win(self, store: MemoryStore) -> None:
        """La prova che il solo delta non basta: i numeri dicono "riuscito"."""
        outcome = await self._destructive_migration(store)

        # ``USER.md`` è calato, la destinazione è intatta: su ``before``/``after``
        # questo run è indistinguibile da una potatura andata bene.
        assert outcome.after["USER.md"] < outcome.before["USER.md"]
        assert outcome.after["MEMORY.md"] == outcome.before["MEMORY.md"]
        assert outcome.freed > 0
        # E il fatto non è in nessuno dei due file.
        assert "Europe/Rome" not in store.user_file.read_text(encoding="utf-8")
        assert "Europe/Rome" not in store.memory_file.read_text(encoding="utf-8")

    async def test_a_refusal_the_model_recovers_from_is_not_flagged(
        self, store: MemoryStore
    ) -> None:
        """Il rovescio: obbedire al messaggio di rifiuto non è un fallimento.

        Il modello aggiunge un fatto, viene rifiutato, pota le voci vecchie e
        riscrive **portandosi dentro il fatto** — cioè fa alla lettera quel che il
        messaggio di rifiuto gli chiede. Va riconosciuto come riuscito.
        """
        refused_growth = _MEMORY_TEXT + "- fatto nuovo\n"

        def guard(path: Path, text: str) -> str | None:
            if path.name == "MEMORY.md" and len(text) > len(_MEMORY_TEXT):
                return "Write refused: over budget."
            return None

        async def effect(tools: Any) -> None:
            await tools.execute(
                "edit_file",
                {"path": "memory/MEMORY.md", "old_text": _MEMORY_TEXT, "new_text": refused_growth},
            )
            await tools.execute(
                "edit_file",
                {
                    "path": "memory/MEMORY.md",
                    "old_text": _MEMORY_TEXT,
                    "new_text": "# Memory\n- fatto nuovo\n",
                },
            )

        outcome = await _run(store, _FakeAgent(effect=effect), write_size_guard=guard)

        assert "fatto nuovo" in store.memory_file.read_text(encoding="utf-8")
        assert outcome.unresolved_refusals == 0
        assert outcome.status == STATUS_COMPLETED

    async def test_obeying_the_fallback_is_not_a_failure(self, store: MemoryStore) -> None:
        """La via di riserva che il prompt prescrive non deve risultare un guasto.

        *"Se la destinazione viene rifiutata, lascia la fonte esattamente dov'è e
        passa al passo successivo."* Un modello che obbedisce lascia un rifiuto
        aperto e non cancella niente: nessun fatto perso. Una prima versione
        segnalava proprio questo come ``failed``, in contraddizione con la fine
        dello stesso template — *"un run che non cambia niente è un esito
        valido"*. La firma distruttiva è la congiunzione: rifiuto aperto **e**
        qualcosa che è calato.
        """

        def guard(path: Path, _text: str) -> str | None:
            return "Write refused: over budget." if path.name == "MEMORY.md" else None

        async def effect(tools: Any) -> None:
            await tools.execute(
                "edit_file",
                {
                    "path": "memory/MEMORY.md",
                    "old_text": "# Memory\n",
                    "new_text": "# Memory\n- contesto spostato\n",
                },
            )

        outcome = await _run(store, _FakeAgent(effect=effect), write_size_guard=guard)

        assert outcome.unresolved_refusals == 1
        assert outcome.status == STATUS_NO_CHANGE
        # E la fonte è intatta: è tutto il punto della via di riserva.
        assert store.user_file.read_text(encoding="utf-8") == _USER_TEXT

    async def test_pruning_that_drops_the_refused_fact_stays_flagged(
        self, store: MemoryStore
    ) -> None:
        """E il caso che il solo percorso non distingueva: pota, ma perde il fatto.

        Stessa forma del test sopra, con una sola differenza — la riscrittura non
        contiene la riga che era stata rifiutata. Per un insieme di soli percorsi
        i due run erano identici; qui il rifiuto resta aperto, perché il fatto non
        è atterrato da nessuna parte.
        """
        refused_growth = _MEMORY_TEXT + "- fatto nuovo\n"

        def guard(path: Path, text: str) -> str | None:
            if path.name == "MEMORY.md" and len(text) > len(_MEMORY_TEXT):
                return "Write refused: over budget."
            return None

        async def effect(tools: Any) -> None:
            await tools.execute(
                "edit_file",
                {"path": "memory/MEMORY.md", "old_text": _MEMORY_TEXT, "new_text": refused_growth},
            )
            await tools.execute(
                "edit_file",
                {"path": "memory/MEMORY.md", "old_text": _MEMORY_TEXT, "new_text": "# Memory\n"},
            )

        outcome = await _run(store, _FakeAgent(effect=effect), write_size_guard=guard)

        assert "fatto nuovo" not in store.memory_file.read_text(encoding="utf-8")
        assert outcome.unresolved_refusals == 1


class TestTheCriteriaAreReachable:
    async def test_the_run_can_read_agent_dream_md(self, store: MemoryStore) -> None:
        """Il prompt rimanda a ``agent/dream.md`` invece di ricopiarne i criteri.

        Regge su un fatto sottile: ``build_dream_tools`` monta ``ReadFileTool``
        sull'intero workspace, e ``sync_workspace_templates`` ci estrae i prompt
        di sistema sotto ``agent/``. Se un domani quel tool set venisse ristretto
        ai soli file di memoria, le regole di potatura sparirebbero dal run in
        silenzio: resterebbe un modello a cui si chiede di cancellare senza
        dirgli in base a cosa, e nessun test se ne accorgerebbe.
        """
        read: list[str] = []

        async def effect(tools: Any) -> None:
            read.append(await tools.execute("read_file", {"path": "agent/dream.md"}))

        agent = _FakeAgent(effect=effect)
        await _run(store, agent)

        assert "Read `agent/dream.md` in the workspace" in agent.prompt
        assert read, "read_file non ha restituito nulla"
        assert "Delete-or-keep" in read[0], read[0][:200]


class TestTokenAccounting:
    """Il turno del review pass non deve sparire dalla contabilità — e spariva.

    **Questa classe provava il gesto, non l'effetto, ed è il motivo per cui il
    difetto è vissuto invisibile.** Monkeypatchava
    ``record_response_token_usage`` e asseriva che venisse *chiamata*; nessuna
    asserzione toccava mai il conteggio. Il secondo test arrivava a scrivere
    ``assert recorded == [None]`` — cioè documentava di aver passato ``None`` — e
    si chiamava «is still charged». Verde, e su niente.

    Misurato il 25/08 su ``token-usage.json`` del dispositivo: in **27 giorni** il
    bucket ``dream`` non era comparso una volta. ``resp`` è un
    ``OutboundMessage``, che un campo ``usage`` non ce l'ha e non l'ha mai avuto.

    La contabilità la fa ``TokenUsageHook.after_iteration`` sul turno, che è
    l'unico punto in cui l'``usage`` del provider esiste. La copertura sta dove sta
    il meccanismo: ``tests/webui/test_token_usage.py`` (mappa chiave→bucket, e
    l'opt-in del vero hook) e ``tests/agent/test_dream.py::TestEphemeralHooks``
    (il montaggio su un turno effimero).
    """

    def test_the_review_does_not_do_its_own_accounting(self) -> None:
        """Il funnel è uno solo, e questa funzione non è quello.

        Asserzione sull'**AST** e non sul comportamento, di proposito: qui il
        comportamento corretto è *l'assenza* di una chiamata, e un test che prova
        un'assenza monkeypatchando è esattamente l'errore che questa classe
        documenta. Un secondo funnel non darebbe un errore, darebbe un doppio
        conteggio silenzioso.

        E sull'AST e non sul testo: il commento che in quel file spiega **perché**
        la chiamata non c'è più nomina la funzione, quindi un `in source` fallirebbe
        sulla prosa che documenta la correzione.
        """
        import ast

        import jenny.agent.dream_review as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "record_response_token_usage" not in called
        assert not hasattr(module, "record_response_token_usage")

    async def test_a_turn_that_raises_still_returns_a_failed_outcome(
        self, store: MemoryStore
    ) -> None:
        """Quel che il vecchio test provava davvero, senza la finzione sopra."""

        class _Exploding(_FakeAgent):
            async def process_direct(self, prompt: str, **kwargs: Any) -> Any:
                raise RuntimeError("provider giù")

        outcome = await _run(store, _Exploding())

        assert outcome.status == STATUS_FAILED


class TestTheUserFileIsNotPrunedLikeTheOthers:
    """Il budget e i criteri si contraddicono su ``USER.md``, e il prompt deve
    dire chi vince.

    Il gauge chiede di far rientrare i file; le regole delete-or-keep di
    ``agent/dream.md`` — che questo run applica per riferimento — dicono
    *"Never delete: user preferences and personality traits"*. Su MEMORY.md non
    c'e' conflitto, perche' la sua ciccia sono note di implementazione che una
    regola marca gia' come cancellabili. Su USER.md le due istruzioni arrivano
    insieme senza una terza che rompa il pareggio, e un modello lo rompe da
    solo — in modo diverso a ogni run, su fatti che l'utente non puo' sapere di
    aver perso.
    """

    async def test_the_prompt_says_the_criteria_win(self, store: MemoryStore) -> None:
        agent = _FakeAgent()
        await _run(store, agent)

        assert "USER.md shrinks by moving, not by forgetting" in agent.prompt
        # Le due meta' della regola: come si scende, e quando ci si ferma.
        assert "the criteria win" in agent.prompt
        assert "finished job, not a failed one" in agent.prompt

    async def test_a_copied_down_location_goes_and_the_never_delete_rule_stays(
        self, store: MemoryStore
    ) -> None:
        """Le due istruzioni devono convivere, non mangiarsi a vicenda.

        La riga della posizione in ``USER.md`` e' un duplicato, ma di una copia
        canonica che non e' un altro file: e' una riga costruita a runtime
        (``Device location``, v. ``jenny/runtime/location.py``). La regola
        ``Always delete: same fact at multiple locations`` in teoria la copre,
        ma un modello che cerca il duplicato *fra i file* non lo trova mai —
        percio' il prompt la nomina esplicitamente.

        Il rischio del rimedio e' l'opposto: una frase che autorizzi a
        cancellare posizione e fuso puo' leggersi come un'eccezione al
        *Never delete* sugli attributi personali, e allora il pareggio che la
        sezione qui sopra esiste per rompere torna com'era. Non e' un'eccezione:
        il fatto non si perde, perche' il runtime lo rimanda nel prompt
        successivo, datato. Questo test guarda che ci siano tutte e due.
        """
        agent = _FakeAgent()
        await _run(store, agent)

        # Il duplicato nominato per quello che e': una riga di prompt, non un file.
        assert "`Device location`" in agent.prompt
        assert "`- **Location**: Rome, Italy (~41.89, 12.54)`" in agent.prompt
        # E la regola che protegge gli attributi personali, intatta.
        assert "USER.md shrinks by moving, not by forgetting" in agent.prompt
        assert "preferences and personality traits stay" in agent.prompt
        assert "not an exception" in agent.prompt

    async def test_the_gauge_does_not_order_an_unconditional_shrink(
        self, store: MemoryStore
    ) -> None:
        """Il gauge propone un bersaglio; i criteri restano l'autorita'.

        Se questa riga tornasse a essere un ordine senza eccezioni ("bring each
        file at or under its budget") contraddirebbe la sezione qui sopra dentro
        lo stesso prompt, ed e' esattamente il pareggio che quella sezione
        esiste per rompere.
        """
        agent = _FakeAgent()
        await _run(store, agent)

        assert "Shrink what the criteria allow" in agent.prompt
        assert "Bring each file at or under" not in agent.prompt


class TestTheRouteDownNamesWhatTheDeviceLeftBehind:
    """Le due lacune che un run vero ha rivelato, e che nessun test aveva visto.

    Sul Titan 2 il 2026-08-16 il review pass ha fatto **esattamente** quello che
    la lista gli diceva — task spec migrate, timezone rimossa — e ha lasciato
    quello che non c'era: il contesto di progetto (che ``agent/dream.md``
    instrada a MEMORY.md, ma la lista qui non nominava) e il boilerplate del
    template (lead-in, riga di chiusura, separatore), che "residuo di template"
    lasciava intendere significasse solo caselle e segnaposti vuoti.

    Non era un difetto del modello: era prosa mancante.
    """

    async def test_project_context_is_routed_to_memory(self, store: MemoryStore) -> None:
        agent = _FakeAgent()
        await _run(store, agent)

        assert "**Project context**" in agent.prompt
        assert "memory/MEMORY.md`, deleted from here" in agent.prompt
        # E la distinzione che rende la regola applicabile invece che vaga:
        # il tratto della persona resta, il progetto va.
        assert "Keep the trait, move the project" in agent.prompt

    async def test_shipped_boilerplate_counts_as_residue(
        self, store: MemoryStore
    ) -> None:
        agent = _FakeAgent()
        await _run(store, agent)

        assert "boilerplate the template shipped with" in agent.prompt
        # I tre pezzi concreti rimasti sul device, nominati uno per uno.
        for fragment in ("explanatory lead-in", "edit the file to customise", "horizontal rule"):
            assert fragment in agent.prompt, fragment
        # La ragione, che e' quella che rende la regola difendibile.
        assert "no reader at all" in agent.prompt


class TestTheReviewPassKeepsTheFileTools:
    """Il review pass ristruttura davvero, e per farlo gli servono i tool file.

    Ora che esiste un tool per voci c'è una ragione per pensare di togliere gli
    altri, e sarebbe sbagliata: il prompt del review chiede di spostare un fatto
    da un file all'altro con **una sola** ``apply_patch``, perché quel tool è
    tutto-o-niente. Due chiamate per voci su due file non sono atomiche, e il
    modo in cui falliscono è il peggiore possibile — il fatto tolto dall'origine
    e mai arrivato a destinazione, senza che niente lo dica.
    """

    def test_the_registry_still_carries_them(self, store):
        names = set(store.build_dream_tools().tool_names)

        assert {"apply_patch", "edit_file", "write_file", "read_file"} <= names

    def test_the_atomic_move_instruction_has_a_tool_behind_it(self, store):
        """Antideriva fra il prompt e il registry: se ``apply_patch`` sparisse, il
        paragrafo sullo spostamento atomico resterebbe a chiedere una cosa
        impossibile, e nessun test lo direbbe."""
        prompt = prompt_templates.render_template(
            "agent/dream_review.md", budget_gauge="", snapshotted=True,
        )

        assert "`apply_patch`" in prompt
        assert "apply_patch" in set(store.build_dream_tools().tool_names)

    def test_it_also_gets_the_entry_tool(self, store):
        """Non è una svista: rimuovere una voce è ciò che il review pass fa di
        mestiere, e nella fase 2 del piano ``remove`` diventerà la degradazione."""
        assert "memory" in store.build_dream_tools().tool_names


class TestTheTwoRegistriesDoNotShareCounters:
    """Il review pass e il turno incrementale costruiscono due registry distinti,
    e devono restare tali.

    ``batch_was_not_consolidated`` legge i contatori di voci del **turno
    incrementale** per decidere se il cursore avanza. Se quelli del review pass
    ci finissero dentro, un review che aggiunge una voce — cosa che fa
    legittimamente, spostando un fatto — farebbe passare per atterrato un batch
    che il turno dopo non ha salvato affatto. Sarebbe il difetto di partenza,
    reintrodotto da una porta nuova.
    """

    def test_each_build_gets_its_own_counters(self, store):
        review_tools = store.build_dream_tools()
        turn_tools = store.build_dream_tools()

        assert review_tools.memory_entries is not turn_tools.memory_entries

    async def test_a_write_through_one_does_not_show_in_the_other(self, store):
        review_tools = store.build_dream_tools()
        turn_tools = store.build_dream_tools()

        await review_tools.execute(
            "memory", {"action": "add", "file": "user", "text": "un fatto"},
        )

        assert review_tools.memory_entries.entries_added == 1
        assert turn_tools.memory_entries.entries_added == 0

    async def test_the_file_states_are_separate_too(self, store):
        """Stessa proprietà, sul contatore che c'era già: ``file_states`` è
        per-run e non condiviso fra Dream concorrenti."""
        review_tools = store.build_dream_tools()
        turn_tools = store.build_dream_tools()

        await review_tools.execute(
            "memory", {"action": "add", "file": "user", "text": "un fatto"},
        )

        assert review_tools.file_states.writes_ok == 1
        assert turn_tools.file_states.writes_ok == 0


class TestTheFloorIsNowNeverLose:
    """6.0: il pavimento smette di essere "non togliere" e diventa "non perdere".

    La riscrittura è arrivata dopo che la rete della fase 2 è stata verificata sul
    telefono — dieci voci passate dall'archivio, nessuna persa — perché prima
    sarebbe stata un permesso senza copertura. Misurato il 2026-08-19: il review
    sotto pressione ha riformulato sette voci per raschiare 109 caratteri e non ne
    ha tolta nessuna, obbedendo alla lettera a una regola scritta quando togliere
    significava perdere.
    """

    def _dream(self) -> str:
        return prompt_templates.render_template(
            "agent/dream.md", strip=True, skill_creator_path="skills/skill-creator/SKILL.md",
        )

    def _review(self) -> str:
        return prompt_templates.render_template(
            "agent/dream_review.md", budget_gauge="", snapshotted=True,
        )

    def test_the_floor_is_named_never_lose(self):
        assert "**Never lose**" in self._dream()

    def test_it_says_removal_from_those_two_files_is_not_deletion(self):
        prompt = self._dream()

        assert "does not delete it" in prompt
        assert "memory/archive/" in prompt

    def test_soul_keeps_the_hard_floor(self):
        """L'archivio copre i due file a voci. ``SOUL.md`` non è uno di quelli:
        niente archivia ciò che ne esce, e una riga tolta è persa davvero."""
        prompt = self._dream()

        assert "there *never delete* still means never delete" in prompt
        assert "nothing archives what leaves it" in prompt

    def test_the_permission_is_last_not_first(self):
        """Non è una licenza: le voci protette restano l'ultima cosa che si muove,
        dopo che le altre categorie sono esaurite."""
        prompt = self._dream()

        assert "the **last** things to move, never the first" in prompt
        assert "before you touch one" in prompt

    def test_the_review_prompt_puts_it_after_its_route_down(self):
        prompt = self._review()

        assert "a fifth step below the four" in prompt
        assert "when the four steps below are exhausted" in prompt

    def test_the_review_prompt_names_the_cost_of_over_pruning(self):
        """Una voce archiviata è fuori dal prompt: l'effetto osservabile non è "ho
        perso un fatto" ma "Jenny non se lo ricorda più"."""
        assert "made Jenny stop knowing things" in self._review()

    def test_the_permission_did_not_replace_the_route_down(self):
        """Il percorso di discesa resta il modo normale di far spazio: se sparisse,
        il permesso diventerebbe la prima mossa invece dell'ultima."""
        prompt = self._review()

        assert "route down" in prompt
        assert "Task specs and procedures" in prompt
        assert "Template residue" in prompt


class TestADestructivePassSaysSo:
    """6.2, e ha dovuto uscire *insieme* alla 6.0.

    Degradare non è gratis: una voce archiviata è recuperabile ma non è più nel
    prompt. Allargare il permesso senza la sua visibilità è l'unico ordine, fra i
    due, che potrebbe fare danno davvero.
    """

    def test_the_threshold_is_about_a_quarter_of_a_real_file(self):
        from jenny.agent.dream_review import DEMOTION_IS_NOTABLE

        assert DEMOTION_IS_NOTABLE == 5

    @staticmethod
    def _captured():
        """Sink loguru: ``caplog`` non lo vede, perché loguru non passa da
        ``logging`` a meno che qualcuno non ce lo instradi."""
        from loguru import logger

        lines: list[str] = []
        sink = logger.add(lines.append, level="WARNING", format="{message}")
        return lines, lambda: logger.remove(sink)

    def test_a_quiet_pass_says_nothing_loud(self, store):
        from jenny.agent.dream_review import _report_demotions

        lines, done = self._captured()
        try:
            moved = _report_demotions(store, set())
        finally:
            done()

        assert moved == ()
        assert not lines

    def test_it_names_what_moved_not_just_how_many(self, store):
        """Il numero dice quanto; chi legge un avviso deve sapere *cosa*, o non
        può decidere se andare a guardare."""
        from datetime import date

        from jenny.agent.dream_review import DEMOTION_IS_NOTABLE, _report_demotions
        from jenny.agent.memory_archive import ArchivedEntry, archive_entry

        for i in range(DEMOTION_IS_NOTABLE + 1):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"c{i}", text=f"- Un fatto numero {i}", source="USER.md"),
                when=date(2026, 8, 19),
            )

        lines, done = self._captured()
        try:
            moved = _report_demotions(store, set())
        finally:
            done()

        assert len(moved) == DEMOTION_IS_NOTABLE + 1
        assert "Un fatto numero 0" in "".join(lines)
        assert "6 entries" in "".join(lines)

    def test_only_what_this_pass_moved_is_reported(self, store, caplog):
        from datetime import date

        from jenny.agent.dream_review import _report_demotions
        from jenny.agent.memory_archive import ArchivedEntry, archive_entry, archived_ids

        archive_entry(
            store.memory_dir,
            ArchivedEntry(id="old", text="- Roba di ieri", source="USER.md"),
            when=date(2026, 8, 18),
        )
        before = archived_ids(store.memory_dir)
        archive_entry(
            store.memory_dir,
            ArchivedEntry(id="new", text="- Roba di oggi", source="USER.md"),
            when=date(2026, 8, 19),
        )

        moved = _report_demotions(store, before)

        assert len(moved) == 1 and "new" in moved[0]



class TestTheOutcomeSaysWhatItTookAway:
    """"Quanto ha liberato" e "quali fatti ha spostato" sono due domande diverse.

    ``demoted`` esisteva già ed era **morto**: valorizzato sui soli due rami
    ``failed``, letto da nessuno. I due esiti normali — "ha liberato spazio" e
    "non c'era niente da potare" — non lo portavano, cioè proprio quelli in cui
    una degradazione è più probabile; e ``/dream`` rispondeva "nothing was freed"
    a una passata che aveva spostato dei fatti personali dell'utente.

    Il caso peggiore ha un nome e sta qui sotto: una **riformulazione**. La voce
    vecchia parte per l'archivio, la nuova è più lunga, nessun file cala — e i
    soli ``before``/``after`` raccontano un run che non ha fatto niente.
    """

    _FACT = "- Timezone: Europe/Rome\n"

    @staticmethod
    def _rewords_a_fact_into_a_longer_one(store: MemoryStore) -> Any:
        """Effetto che riformula un fatto di ``USER.md`` allungandolo.

        La voce vecchia se ne va (l'archiviatore al confine del file la degrada,
        v. ``make_entry_archiver``) e il file **cresce**: nessun ``shrank``, quindi
        l'esito è ``no-change`` — e senza ``demoted`` la risposta all'utente
        sarebbe "nothing was freed" su un fatto che è uscito dal prompt.
        """

        async def effect(tools: Any) -> None:
            await tools.execute("edit_file", {
                "path": "USER.md",
                "old_text": TestTheOutcomeSaysWhatItTookAway._FACT,
                "new_text": "- Timezone: Europe/Rome (CEST in summer, verified 2026-08)\n",
            })

        return effect

    @staticmethod
    def _drops_a_fact(store: MemoryStore) -> Any:
        """Effetto che toglie un fatto da ``USER.md``: degrada **e** rimpicciolisce."""

        async def effect(tools: Any) -> None:
            await tools.execute("edit_file", {
                "path": "USER.md",
                "old_text": TestTheOutcomeSaysWhatItTookAway._FACT,
                "new_text": "",
            })

        return effect

    async def test_a_reword_is_reported_even_though_nothing_shrank(
        self, store: MemoryStore
    ) -> None:
        outcome = await _run(
            store, _FakeAgent(effect=self._rewords_a_fact_into_a_longer_one(store))
        )

        assert outcome.status == STATUS_NO_CHANGE
        # La prova che il solo delta non basta: non c'è niente da liberare, e un
        # fatto è comunque uscito dai file caldi.
        assert outcome.freed <= 0
        assert len(outcome.demoted) == 1

    async def test_a_pass_that_freed_space_also_says_what_it_moved(
        self, store: MemoryStore
    ) -> None:
        """Il ramo ``completed``: quello che gira più spesso, e non lo portava."""
        outcome = await _run(store, _FakeAgent(effect=self._drops_a_fact(store)))

        assert outcome.status == STATUS_COMPLETED
        assert len(outcome.demoted) == 1

    async def test_a_quiet_pass_reports_no_demotions(self, store: MemoryStore) -> None:
        """Il rovescio: senza degradazioni la nota non deve inventarne."""
        outcome = await _run(store, _FakeAgent(effect=_shrink(store)))

        assert outcome.status == STATUS_COMPLETED
        assert outcome.demoted == ()
        assert outcome.demoted_ids == ()

    async def test_a_run_that_died_still_reports_what_it_had_already_moved(
        self, store: MemoryStore
    ) -> None:
        """Il ramo per eccezione, l'unico dove nessun riepilogo a valle le nominerebbe.

        Il turno è finito male, quindi il modello non racconta niente e i soli
        ``before``/``after`` non distinguono "cancellato" da "spostato". Le voci
        già degradate prima dell'errore sono precisamente quelle di cui nessuno
        saprebbe.
        """

        async def effect(tools: Any) -> None:
            await tools.execute("edit_file", {
                "path": "USER.md", "old_text": self._FACT, "new_text": "",
            })
            raise RuntimeError("il provider è caduto a metà turno")

        agent = _FakeAgent(effect=effect)
        outcome = await _run(store, agent)

        assert outcome.status == STATUS_FAILED
        assert len(outcome.demoted) == 1

    async def test_a_run_that_did_not_complete_reports_them_too(
        self, store: MemoryStore
    ) -> None:
        outcome = await _run(
            store,
            _FakeAgent(stop_reason="max_iterations", effect=self._drops_a_fact(store)),
        )

        assert outcome.status == STATUS_FAILED
        assert len(outcome.demoted) == 1

    async def test_an_open_refusal_with_nothing_shrunk_reports_them_too(
        self, store: MemoryStore
    ) -> None:
        """Il ramo che il piano aveva individuato: rifiuto aperto, nulla è calato.

        Il modello riformula un fatto (degradazione, e il file cresce) e poi si
        vede rifiutare la scrittura sulla destinazione. È l'esito che il prompt
        del review chiede — lascia stare la fonte — e portava via con sé la
        notizia della riformulazione.
        """

        def guard(path: Path, _text: str) -> str | None:
            return "Write refused: over budget." if path.name == "MEMORY.md" else None

        async def effect(tools: Any) -> None:
            await self._rewords_a_fact_into_a_longer_one(store)(tools)
            await tools.execute("edit_file", {
                "path": "memory/MEMORY.md",
                "old_text": "# Memory\n",
                "new_text": "# Memory\n- contesto spostato\n",
            })

        outcome = await _run(store, _FakeAgent(effect=effect), write_size_guard=guard)

        assert outcome.status == STATUS_NO_CHANGE
        assert outcome.unresolved_refusals == 1
        assert len(outcome.demoted) == 1

    async def test_a_destructive_migration_reports_them_too(
        self, store: MemoryStore
    ) -> None:
        """Il quinto ramo: rifiuto aperto **e** qualcosa è calato.

        È il caso in cui il fatto potrebbe non essere in nessuno dei due file, e
        la sola risposta utile all'utente è dove ritrovarlo.
        """

        def guard(path: Path, _text: str) -> str | None:
            return "Write refused: over budget." if path.name == "MEMORY.md" else None

        async def effect(tools: Any) -> None:
            await self._drops_a_fact(store)(tools)
            await tools.execute("edit_file", {
                "path": "memory/MEMORY.md",
                "old_text": "# Memory\n",
                "new_text": f"# Memory\n{self._FACT}",
            })

        outcome = await _run(store, _FakeAgent(effect=effect), write_size_guard=guard)

        assert outcome.status == STATUS_FAILED
        assert outcome.unresolved_refusals == 1
        assert len(outcome.demoted) == 1

    async def test_no_return_path_forgets_them(self) -> None:
        """L'invariante, letta dal sorgente e non da un ramo alla volta.

        I sei rami qui sopra sono i sei di oggi; il difetto originale era che un
        settimo aggiunto senza pensarci nascesse muto, e la revisione non se ne
        accorgesse — ``demoted`` ha un default, quindi ometterlo non è un errore
        per nessuno. Questo test rende il default un'omissione visibile.
        """
        import inspect
        import re

        from jenny.agent import dream_review

        source = inspect.getsource(dream_review.run_dream_review)
        constructions = re.findall(r"ReviewOutcome\((?:[^()]|\([^()]*\))*\)", source)

        assert len(constructions) == 6, f"rami cambiati: {len(constructions)}"
        mute = [c for c in constructions if "demoted" not in c]
        assert not mute, f"uscite senza le degradazioni: {mute}"

    async def test_the_ids_are_what_recall_accepts(self, store: MemoryStore) -> None:
        """Un numero non è azionabile: ``recall`` prende id, non nomi di file."""
        from jenny.agent.tools.memory_recall import MemoryRecallTool

        outcome = await _run(store, _FakeAgent(effect=self._drops_a_fact(store)))

        assert outcome.demoted_ids and outcome.demoted_ids != outcome.demoted
        rendered = await MemoryRecallTool(store.workspace).execute(
            ids=list(outcome.demoted_ids)
        )
        assert "Europe/Rome" in rendered
        assert "No archived entry has id" not in rendered
