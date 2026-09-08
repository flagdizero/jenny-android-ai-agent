"""Tests for Dream memory consolidation — build_dream_prompt and cursor management."""

from types import SimpleNamespace

import pytest

from jenny.agent.memory import MemoryStore
from jenny.agent.tools.file_state import FileStates
from jenny.providers.base import LLMResponse
from jenny.security.workspace_access import (
    bind_workspace_scope,
    default_workspace_scope,
    reset_workspace_scope,
)
from jenny.utils.prompt_templates import render_template


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path)
    s.soul_file.write_text("# Soul\n- Helpful", encoding="utf-8")
    s.memory_file.write_text("# Memory\n- Project X active", encoding="utf-8")
    return s


class TestBuildDreamPrompt:
    def test_returns_none_when_no_history(self, store):
        assert store.build_dream_prompt() is None

    def test_returns_prompt_with_history(self, store):
        store.append_history("hello")
        result = store.build_dream_prompt()
        assert result is not None
        prompt, cursor = result.prompt, result.cursor
        assert cursor > 0
        assert "## Conversation History" in prompt
        assert "hello" in prompt

    def test_cursor_advances_only_new_entries(self, store):
        store.append_history("first")
        r1 = store.build_dream_prompt()
        assert r1 is not None
        c1 = r1.cursor

        # Cursor not yet advanced — same entries are still available
        assert store.build_dream_prompt() is not None

        # Advance cursor
        store.set_last_dream_cursor(c1)
        # Now no new entries
        assert store.build_dream_prompt() is None

        # Add new entry
        store.append_history("second")
        r2 = store.build_dream_prompt()
        assert r2 is not None
        c2 = r2.cursor
        assert c2 > c1

    def test_prompt_includes_skill_creator_path(self, store):
        store.append_history("test")
        result = store.build_dream_prompt()
        assert result is not None
        prompt = result.prompt
        assert "skill-creator" in prompt

    def test_truncates_long_entries(self, store):
        long_content = "x" * 2000
        store.append_history(long_content)
        result = store.build_dream_prompt()
        assert result is not None
        prompt = result.prompt
        # The full 2000 chars should not appear — truncated to 500
        assert long_content not in prompt
        assert "x" * 500 in prompt

    def test_batches_oldest_unprocessed_entries_first(self, store):
        for i in range(25):
            store.append_history(f"entry-{i + 1:02d}")

        result = store.build_dream_prompt(max_entries=20)
        assert result is not None
        prompt, cursor = result.prompt, result.cursor

        assert cursor == 20
        assert "entry-01" in prompt
        assert "entry-20" in prompt
        assert "entry-21" not in prompt

        store.set_last_dream_cursor(cursor)
        next_result = store.build_dream_prompt(max_entries=20)
        assert next_result is not None
        next_prompt, next_cursor = next_result.prompt, next_result.cursor
        assert next_cursor == 25
        assert "entry-21" in next_prompt
        assert "entry-25" in next_prompt

    def test_skips_malformed_history_entries(self, store):
        """Dream prompt building should tolerate externally corrupted JSONL rows."""
        store.history_file.write_text(
            '{"cursor": 1, "timestamp": "2026-04-01 10:00"}\n'
            '{"cursor": 2, "timestamp": "2026-04-01 10:01", "content": "usable memory"}\n',
            encoding="utf-8",
        )

        result = store.build_dream_prompt()

        assert result is not None
        prompt, cursor = result.prompt, result.cursor
        assert cursor == 2
        assert "usable memory" in prompt

    def test_a_correction_to_the_dream_prompt_reaches_an_installation(self):
        """Cosa il template *dice* sta in ``_DREAM_MD_RULES``; qui sta il fatto che
        una correzione arrivi.

        ``agent/**`` si riscrive a ogni avvio, un file dell'utente una volta sola:
        fuori da questo elenco, ogni riga scritta in ``agent/dream.md`` per riparare
        un comportamento misurato sul telefono resterebbe nel repo e non sul
        telefono. È la sola metà di questo contratto che una riscrittura del prompt
        non può rompere — e la sola che nessun'altra asserzione di questo file
        copriva: il test che stava qui prima sincronizzava un workspace di prova
        senza reindirizzarci ``get_workspace_path``, quindi renderizzava il template
        dell'installazione come tutti gli altri e la sincronizzazione non
        partecipava a niente.
        """
        from jenny.utils.android_assets import _SYSTEM_PROMPT_TEMPLATES

        assert "agent/dream.md" in _SYSTEM_PROMPT_TEMPLATES


class TestDreamPromptBudgetGauge:
    def test_default_prompt_is_byte_identical_to_the_pre_budget_one(self, store):
        """Rete di sicurezza della wave: senza gauge il prompt non cambia di un byte.

        La sezione Budget è dietro un condizionale Jinja proprio per questo — se
        un giorno l'intestazione dovesse comparire anche con gauge vuoto, ogni
        run di Dream pagherebbe la modifica prima ancora che qualcuno decida di
        attivare il budget. Il confronto è contro il template renderizzato senza
        la variabile, cioè esattamente ciò che il codice produceva prima.
        """
        store.append_history("hello")
        result = store.build_dream_prompt()
        assert result is not None
        prompt = result.prompt

        legacy_template = render_template(
            "agent/dream.md",
            strip=True,
            skill_creator_path=str(
                store.workspace / "skills" / "skill-creator" / "SKILL.md"
            ),
        )
        # Nemmeno una riga vuota in più fra il template e la history.
        assert prompt.startswith(f"{legacy_template}\n\n## Conversation History\n")
        assert "## Budget" not in prompt

    def test_gauge_injects_budget_section(self, store):
        store.append_history("hello")
        result = store.build_dream_prompt(gauge="MEMORY.md [67% - 1474/2200 chars]")
        assert result is not None
        prompt = result.prompt

        assert "## Budget" in prompt
        assert "MEMORY.md [67% - 1474/2200 chars]" in prompt

    def test_budget_section_sits_after_the_call_frugality_rule(self, store):
        """La prosa più vicina alla fine è quella che vince la contraddizione.

        ``## Editing`` dice di battere insieme le modifiche in meno chiamate
        possibili, ed è la ragione per cui potare perde contro aggiungere. La
        regola del budget la contraddice di proposito, quindi deve stare dopo.
        """
        store.append_history("hello")
        result = store.build_dream_prompt(gauge="MEMORY.md [91%]")
        assert result is not None
        prompt = result.prompt

        assert prompt.index("## Editing") < prompt.index("## Budget")
        assert prompt.index("## Budget") < prompt.index("## Conversation History")

    def test_empty_gauge_behaves_like_no_gauge(self, store):
        store.append_history("hello")
        explicit = store.build_dream_prompt(gauge="")
        default = store.build_dream_prompt()
        assert explicit is not None and default is not None
        assert explicit[0] == default[0]


# **Le regole che ``agent/dream.md`` deve dire, e la frase con cui oggi le dice.**
#
# Ogni riga è (nome della regola, perché esiste, come è scritta oggi). La colonna
# di destra è l'unica che cambia quando si riscrive il template: prima stava
# sparsa in sei test e una riscrittura costava sei modifiche, con fallimenti che
# dicevano «la stringa non c'è» invece di «la regola non c'è» (T8.7, I13).
#
# Il perché non è decorazione. Quasi tutte queste frasi sono la **riparazione di
# un comportamento misurato sul Titan 2**, e senza la ragione accanto la riga
# successiva che «semplifica il prompt» le toglie una per una.
_DREAM_MD_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "proponi voci, non riscrivere file",
        "il difetto d'origine: finché il prompt dice «modifica il file», il modello "
        "modifica il file, e sotto pressione pota una riga e si ferma",
        "Propose entries, do not rewrite files",
    ),
    (
        "tutto il lotto in una chiamata",
        "rimedio a D10: il modello sceglie la chiamata che costa meno, quindi la "
        "chiamata che costa meno deve essere quella che produce l'evidenza. Con "
        "``add`` un fatto per volta faceva ``list`` e filtrava da sé, e un lotto di "
        "soli duplicati restava senza evidenza per voce — trattenuto pur essendo "
        "consolidato",
        "Propose the whole batch in one call",
    ),
    (
        "e il parametro che lo permette",
        "l'altra metà: «tutto in una chiamata» senza dire *come* lascia il modello "
        "a inventarsi la forma",
        "`add` takes `texts`",
    ),
    (
        "non leggere il file per decidere",
        "filtrare da sé è il comportamento da chiudere: il tool sa già cosa è nuovo",
        "Do not read the file first to work out what is new",
    ),
    (
        "a cosa serve davvero list",
        "togliergli ``list`` di mano senza dire a cosa serve lo lascerebbe senza "
        "modo di trovare un id da sostituire",
        "the id of an entry you intend to `replace` or `remove`",
    ),
    (
        "proporre un duplicato è gratis",
        "se proporre un duplicato sembrasse costoso, il modello tornerebbe a "
        "filtrare da sé",
        "costs nothing to propose",
    ),
    (
        "SOUL.md resta a scrittura-file",
        "prosa con una struttura, non un elenco: le voci non c'entrano, e un "
        "``add`` su SOUL.md non ha dove atterrare",
        "SOUL.md and `skills/<name>/SKILL.md` have no entry tool",
    ),
    (
        "le etichette del Consolidator si dichiarano",
        "il diario porta ``[skip]`` e ``[correction]``: se il prompt non le spiega, "
        "il modello le tratta come testo del fatto",
        "History attribute tags",
    ),
    (
        "e si dice cosa vuol dire skip",
        "«audit-only» è la ragione per cui una riga marcata così non deve entrare in "
        "memoria: senza, il modello la salva comunque",
        "[skip]: audit-only",
    ),
    (
        "e cosa vuol dire correction",
        "una correzione non è un fatto in più: è un fatto che ne **sostituisce** uno",
        "[correction]: replace the older conflicting fact",
    ),
    (
        "le etichette non finiscono nei file",
        "un'etichetta salvata dentro il fatto lo rende illeggibile per sempre, e "
        "nessuno la va a togliere a mano",
        "Always strip these bracketed tags from saved memory content",
    ),
)

# La frase **ritirata**, tenuta separata perché è l'unica che deve *non* esserci:
# metterla nella tabella sopra la trasformerebbe in una regola da rispettare.
_RETIRED_BLESSING = "a run that only prunes is a run well spent"


class TestThePromptTeachesTheEntryTool:
    """Il tool da solo non basta: finché il prompt dice "modifica il file", il
    modello modifica il file.

    Misurato sul Titan 2 il 2026-08-18, sei run su sei sotto pressione: il
    modello pota una riga esistente e si ferma senza aggiungere il fatto nuovo.
    Il prompt chiedeva due passi e lui faceva il primo, quindi il testo che
    descrive i due passi è parte del difetto, non un contorno.
    """

    def _prompt(self) -> str:
        return render_template(
            "agent/dream.md", strip=True, skill_creator_path="skills/skill-creator/SKILL.md",
        )

    def test_it_names_the_tool_and_its_three_verbs(self):
        prompt = self._prompt()

        assert "`memory` tool" in prompt
        for verb in ("`add`", "`replace`", "`remove`"):
            assert verb in prompt

    def test_every_verb_it_names_exists_in_the_tool(self):
        """Antideriva: il prompt e lo schema non devono raccontare due tool
        diversi. Un verbo inventato qui diventa una chiamata rifiutata là."""
        from pathlib import Path

        from jenny.agent.tools.memory_entries import MemoryEntryTool

        actions = set(
            MemoryEntryTool(Path("/tmp")).parameters["properties"]["action"]["enum"]
        )
        prompt = self._prompt()

        named = {v for v in ("add", "replace", "remove", "list") if f"`{v}`" in prompt}
        assert named
        assert named <= actions

    @pytest.mark.parametrize(
        ("rule", "why", "phrase"), _DREAM_MD_RULES, ids=[r[0] for r in _DREAM_MD_RULES]
    )
    def test_it_states_every_rule_it_has_to_state(self, rule, why, phrase):
        assert phrase in self._prompt(), f"{rule}: {why}"

    def test_the_budget_rule_no_longer_blesses_stopping(self, store):
        """La riga vecchia — "a run that only prunes is a run well spent" — diceva
        esattamente quello che il modello poi faceva. Potare resta utile, ma è
        metà del lavoro, e la frase ora finisce sull'``add``.

        Resta un test suo e non una riga di ``_DREAM_MD_RULES`` perché **non è
        un'asserzione sul testo del template**: il prompt qui è quello costruito
        da ``build_dream_prompt`` *con il gauge acceso*, cioè il ramo in cui la
        regola di budget compare. La tabella sopra guarda il template renderizzato
        da solo, e lì questa regola non c'è.
        """
        store.append_history("hello")
        result = store.build_dream_prompt(gauge="USER.md [96%]")
        assert result is not None
        prompt = result.prompt

        assert _RETIRED_BLESSING not in prompt
        assert "prunes and then stops has saved nothing" in prompt
        assert "Finish with the `add`." in prompt


class TestDreamReviewState:
    def test_missing_file_reads_as_zero(self, store):
        assert not store._review_state_file.exists()
        assert store.get_review_state() == (0, 0)

    def test_round_trip(self, store):
        store.set_review_state(runs_since_review=7, stuck_runs=2)
        assert store.get_review_state() == (7, 2)

    def test_overwrite_replaces_previous_state(self, store):
        store.set_review_state(runs_since_review=7, stuck_runs=2)
        store.set_review_state(runs_since_review=0, stuck_runs=0)
        assert store.get_review_state() == (0, 0)

    @pytest.mark.parametrize(
        "payload",
        [
            "",                                    # troncato a zero byte da un kill
            '{"runs_since_review": 3',             # JSON tagliato a metà
            "not json at all",
            "[1, 2]",                              # JSON valido, radice non-dict
            '"3"',                                 # JSON valido, scalare
            '{"runs_since_review": "3", "stuck_runs": null}',   # tipi sbagliati
            '{"runs_since_review": true, "stuck_runs": 1.5}',   # bool e float
            "{}",                                  # chiavi assenti
        ],
    )
    def test_corrupted_state_reads_as_zero_without_raising(self, store, payload):
        store._review_state_file.write_text(payload, encoding="utf-8")
        assert store.get_review_state() == (0, 0)

    def test_partial_state_keeps_the_readable_half(self, store):
        store._review_state_file.write_text(
            '{"runs_since_review": 4, "stuck_runs": "x"}', encoding="utf-8"
        )
        assert store.get_review_state() == (4, 0)

    def test_negative_values_on_disk_are_normalized(self, store):
        store._review_state_file.write_text(
            '{"runs_since_review": -5, "stuck_runs": -1}', encoding="utf-8"
        )
        assert store.get_review_state() == (0, 0)

    def test_negative_values_never_reach_disk(self, store):
        store.set_review_state(runs_since_review=-5, stuck_runs=-1)
        assert store.get_review_state() == (0, 0)

    def test_state_lives_next_to_the_dream_cursor(self, store):
        store.set_review_state(runs_since_review=1, stuck_runs=0)
        assert store._review_state_file.parent == store._dream_cursor_file.parent
        assert store._review_state_file.name == ".dream_review"

    @pytest.mark.asyncio
    async def test_dream_cannot_edit_its_own_review_state(self, store):
        """Come ``.dream_cursor``: Dream non deve poter azzerare il contatore
        che decide quando gli tocca il review pass."""
        store.set_review_state(runs_since_review=9, stuck_runs=0)
        tools = store.build_dream_tools()

        result = await tools.execute(
            "write_file",
            {"path": "memory/.dream_review", "content": '{"runs_since_review": 0}'},
        )

        assert "outside allowed directory" in result
        assert store.get_review_state() == (9, 0)


class TestForcedAtStuckDoesNotOutliveItsClimb:
    """``forced_at_stuck`` indicizza una salita di ``stuck``, non l'installazione.

    Serve a non riforzare il review sullo stesso valore. Ma azzerato ``stuck`` —
    cioè quando il cursore è avanzato — quel valore diventa una mina: alla salita
    successiva ``dream_cycle`` ritrova ``stuck == forced_at`` e salta il review
    proprio al run in cui servirebbe. Misurato sul Titan 2 il 2026-08-18: il
    review è arrivato solo a ``stuck == 4``, sulla soglia d'allarme, due run oltre
    il suo scopo.
    """

    def test_advancing_the_cursor_clears_it(self, store):
        store.set_review_state(runs_since_review=0, stuck_runs=2, forced_at_stuck=2)

        store.set_review_state(runs_since_review=1, stuck_runs=0)

        assert store.get_review_forced_at_stuck() == 0

    def test_a_climb_that_continues_still_preserves_it(self, store):
        """L'omissione conserva ancora: è "la salita è finita", non "azzera sempre"."""
        store.set_review_state(runs_since_review=0, stuck_runs=2, forced_at_stuck=2)

        store.set_review_state(runs_since_review=1, stuck_runs=3)

        assert store.get_review_forced_at_stuck() == 2

    def test_a_negative_stuck_counts_as_a_reset(self, store):
        """Uno ``stuck`` negativo finisce a 0 su disco, e i due campi non devono
        raccontare stati diversi."""
        store.set_review_state(runs_since_review=0, stuck_runs=2, forced_at_stuck=2)

        store.set_review_state(runs_since_review=1, stuck_runs=-1)

        assert store.get_review_state() == (1, 0)
        assert store.get_review_forced_at_stuck() == 0


class TestDreamTools:
    def test_dream_tools_are_restricted_to_memory_writing(self, store):
        """L'elenco è chiuso di proposito: Dream non naviga, non cerca, non esegue.

        ``memory`` si è aggiunto il 2026-08-18 e non ha allargato il perimetro —
        scrive gli stessi due file che ``edit_file`` già poteva scrivere, per voce
        invece che per file. Se un giorno qui comparisse un tool di rete o di
        shell, questo test è il posto in cui accorgersene.
        """
        tools = store.build_dream_tools()

        assert set(tools.tool_names) == {
            "apply_patch",
            "edit_file",
            "memory",
            "read_file",
            "write_file",
        }

    @pytest.mark.asyncio
    async def test_dream_can_edit_canonical_memory_files(self, store):
        tools = store.build_dream_tools()

        memory_result = await tools.execute(
            "apply_patch",
            {
                "edits": [
                    {
                        "path": "memory/MEMORY.md",
                        "action": "replace",
                        "old_text": "Project X active",
                        "new_text": "Project Y active",
                    }
                ]
            },
        )
        soul_result = await tools.execute(
            "edit_file",
            {
                "path": "SOUL.md",
                "old_text": "Helpful",
                "new_text": "Precise",
            },
        )

        assert "Patch applied" in memory_result
        assert "Successfully edited" in soul_result
        assert "Project Y active" in store.memory_file.read_text(encoding="utf-8")
        assert "Precise" in store.soul_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_dream_can_write_workspace_skills(self, store):
        tools = store.build_dream_tools()
        target = store.workspace / "skills" / "demo" / "SKILL.md"

        result = await tools.execute(
            "write_file",
            {
                "path": "skills/demo/SKILL.md",
                "content": "---\nname: demo\ndescription: Demo skill.\n---\n\nUse when needed.\n",
            },
        )

        assert "Successfully wrote" in result
        assert target.read_text(encoding="utf-8").startswith("---\nname: demo")

    @pytest.mark.asyncio
    async def test_dream_tools_keep_internal_write_scope_under_full_access(self, store):
        tools = store.build_dream_tools()
        scope = default_workspace_scope(store.workspace, restrict_to_workspace=False)
        outside = store.workspace.parent / f"{store.workspace.name}-outside"
        outside.mkdir()
        outside_target = outside / "escape.txt"
        skill_target = store.workspace / "skills" / "scoped" / "SKILL.md"

        token = bind_workspace_scope(scope)
        try:
            outside_result = await tools.execute(
                "write_file",
                {"path": str(outside_target), "content": "owned"},
            )
            skill_result = await tools.execute(
                "apply_patch",
                {
                    "edits": [
                        {
                            "path": "skills/scoped/SKILL.md",
                            "action": "add",
                            "new_text": "---\nname: scoped\n---\n",
                        }
                    ]
                },
            )
        finally:
            reset_workspace_scope(token)

        assert "outside allowed directory" in outside_result
        assert not outside_target.exists()
        assert "Patch applied" in skill_result
        assert skill_target.read_text(encoding="utf-8").startswith("---\nname: scoped")

    @pytest.mark.asyncio
    async def test_dream_cannot_modify_memory_internal_files(self, store):
        tools = store.build_dream_tools()
        store.history_file.write_text("before\n", encoding="utf-8")
        store._dream_cursor_file.write_text("1", encoding="utf-8")

        history_result = await tools.execute(
            "apply_patch",
            {
                "edits": [
                    {
                        "path": "memory/history.jsonl",
                        "action": "replace",
                        "old_text": "before",
                        "new_text": "after",
                    }
                ]
            },
        )
        cursor_result = await tools.execute(
            "edit_file",
            {
                "path": "memory/.dream_cursor",
                "old_text": "1",
                "new_text": "2",
            },
        )

        assert "outside allowed directory" in history_result
        assert "outside allowed directory" in cursor_result
        assert store.history_file.read_text(encoding="utf-8") == "before\n"
        assert store._dream_cursor_file.read_text(encoding="utf-8") == "1"

    @pytest.mark.asyncio
    async def test_dream_cannot_create_children_under_canonical_files(self, store):
        tools = store.build_dream_tools()

        memory_child = store.memory_file / "evil.txt"
        user_child = store.user_file / "evil.txt"
        memory_result = await tools.execute(
            "apply_patch",
            {
                "edits": [
                    {
                        "path": "memory/MEMORY.md/evil.txt",
                        "action": "add",
                        "new_text": "owned",
                    }
                ]
            },
        )
        user_result = await tools.execute(
            "edit_file",
            {
                "path": "USER.md/evil.txt",
                "old_text": "",
                "new_text": "owned",
            },
        )

        assert "outside allowed directory" in memory_result
        assert "outside allowed directory" in user_result
        assert not memory_child.exists()
        assert not user_child.exists()

    @pytest.mark.asyncio
    async def test_dream_can_edit_memory_files_through_symlinked_root(self, tmp_path):
        """Regressione: workspace raggiunto via un symlink di parent (come Android
        ``/data/user/0/<pkg>`` -> ``/data/data/<pkg>``).

        ``.resolve()`` canonicalizza il link, quindi base di risoluzione e allowlist
        di file esatti devono restare allineate: altrimenti il guard anti-symlink di
        ``_is_path_exactly_allowed`` blocca ogni scrittura su MEMORY/SOUL/USER e Dream
        lascia i file di memoria vuoti. Prima del fix questo test fallisce con
        ``WorkspaceBoundaryError``; l'escape *interno* al workspace resta bloccato
        (coperto da ``test_dream_tools_keep_internal_write_scope_under_full_access``).
        """
        real_root = tmp_path / "real"
        real_root.mkdir()
        link_root = tmp_path / "link"
        try:
            link_root.symlink_to(real_root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")

        store = MemoryStore(link_root)  # workspace raggiunto attraverso il symlink
        store.soul_file.write_text("# Soul\n- Helpful", encoding="utf-8")
        store.memory_file.write_text("# Memory\n- Project X active", encoding="utf-8")
        store.user_file.write_text("# User\n- Name: (unset)", encoding="utf-8")

        tools = store.build_dream_tools()

        soul_result = await tools.execute(
            "edit_file",
            {"path": "SOUL.md", "old_text": "Helpful", "new_text": "Precise"},
        )
        memory_result = await tools.execute(
            "apply_patch",
            {
                "edits": [
                    {
                        "path": "memory/MEMORY.md",
                        "action": "replace",
                        "old_text": "Project X active",
                        "new_text": "Project Y active",
                    }
                ]
            },
        )
        user_result = await tools.execute(
            "edit_file",
            {"path": "USER.md", "old_text": "(unset)", "new_text": "Ludovico"},
        )

        assert "Successfully edited" in soul_result, soul_result
        assert "Patch applied" in memory_result, memory_result
        assert "Successfully edited" in user_result, user_result
        assert "Precise" in store.soul_file.read_text(encoding="utf-8")
        assert "Project Y active" in store.memory_file.read_text(encoding="utf-8")
        assert "Ludovico" in store.user_file.read_text(encoding="utf-8")


class TestWriteFileSaysWhatThePromptSays:
    """``write_file`` e ``dream.md`` devono raccontare lo stesso registry.

    ``dream.md`` dichiara al modello che il suo registry consente esattamente
    quattro percorsi. ``write_file`` ne accettava uno — la cartella delle skill —
    e sui tre file di memoria rispondeva ``WorkspaceBoundaryError`` con in coda
    "do not retry with alternative tools": un vicolo chiuso proprio per
    ``SOUL.md``, che non ha un tool per voci e che il review pass deve accorciare
    come prosa. Il tentativo rifiutato resta contato (``record_write_attempt``
    sta prima della risoluzione), quindi il cursore non avanza e ``stuck``
    sale — il "rifiuto di *path*" che il commento di ``format_stuck_alarm``
    dice di aver visto sul Titan 2.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        ["SOUL.md", "USER.md", "memory/MEMORY.md"],
    )
    async def test_write_file_lands_on_each_of_the_three(self, store, path):
        tools = store.build_dream_tools()

        result = await tools.execute(
            "write_file", {"path": path, "content": "# Rewritten\n- Un fatto\n"},
        )

        assert "Successfully wrote" in result, result
        # La trappola vera del messaggio di rifiuto: diceva al modello di non
        # riprovare con un altro tool, cioè di fermarsi.
        assert "do not retry with alternative tools" not in result
        assert (store.workspace / path).read_text(encoding="utf-8") == (
            "# Rewritten\n- Un fatto\n"
        )

    @pytest.mark.asyncio
    async def test_every_path_the_prompt_claims_is_writable_really_is(self, store):
        """Il test che legge tutti e due i lati.

        La frase del prompt non è decorativa: è ciò su cui il modello decide di
        chiamare o non chiamare. Se qui divergono, chi paga è un run notturno.
        """
        import re

        prompt = render_template(
            "agent/dream.md", strip=True, skill_creator_path="skills/skill-creator/SKILL.md",
        )
        claim = re.search(
            r"your registry allows exactly (.+?), so a write there is refused", prompt,
        )
        assert claim is not None, "la frase del registry è cambiata: riallinea il test"
        claimed = re.findall(r"`([^`]+)`", claim.group(1))
        assert claimed == ["SOUL.md", "USER.md", "memory/MEMORY.md", "skills/<name>/SKILL.md"]

        tools = store.build_dream_tools()
        for claimed_path in claimed:
            path = claimed_path.replace("<name>", "pinned")
            result = await tools.execute(
                "write_file", {"path": path, "content": f"# {path}\n"},
            )
            assert "Successfully wrote" in result, f"{path}: {result}"

    @pytest.mark.asyncio
    async def test_those_writes_are_atomic_like_the_other_two_tools(self, store, monkeypatch):
        """Conseguenza voluta dell'allowlist, non effetto collaterale.

        ``_commit_write`` decide su ``_is_exact_allowed_file``: dare l'allowlist a
        ``write_file`` manda anche le sue scritture su quei tre file da
        ``atomic_write``, come già ``edit_file`` e ``apply_patch``. È lo stato che
        Jenny rilegge da sé, e su Android un processo ucciso a metà lascerebbe un
        file troncato che si legge come integro.
        """
        from jenny.agent.tools import filesystem as fs_module

        seen: list[str] = []
        real = fs_module.atomic_write

        def spy(path, data, *args, **kwargs):
            seen.append(str(path))
            return real(path, data, *args, **kwargs)

        monkeypatch.setattr(fs_module, "atomic_write", spy)
        tools = store.build_dream_tools()

        await tools.execute("write_file", {"path": "USER.md", "content": "- Un fatto\n"})
        await tools.execute(
            "write_file", {"path": "skills/plain/SKILL.md", "content": "---\nname: plain\n---\n"},
        )

        assert seen == [str(store.user_file.resolve())]

    @pytest.mark.asyncio
    async def test_a_whole_file_rewrite_still_pays_the_entry_archiver(self, store):
        """L'argomento contrario all'allowlist, verificato e caduto.

        ``entry_archiver`` è per-tool ma ``build_dream_tools`` lo passa a tutti e
        quattro, e ``WriteFileTool.execute`` chiama ``_archive_departing`` prima di
        scrivere esattamente come ``edit_file``. Una riscrittura intera di
        ``MEMORY.md`` non scavalca la degradazione.
        """
        from jenny.agent.memory_archive import archive_dir

        store.memory_file.write_text(
            "# Memory\n- Fatto che sta per sparire\n", encoding="utf-8",
        )
        tools = store.build_dream_tools()

        await tools.execute(
            "write_file", {"path": "memory/MEMORY.md", "content": "# Memory\n- Altro\n"},
        )

        archived = [
            p.read_text(encoding="utf-8")
            for p in archive_dir(store.memory_dir).glob("*.md")
        ]
        assert any("Fatto che sta per sparire" in text for text in archived), archived

    @pytest.mark.asyncio
    async def test_the_allowlist_does_not_open_children_or_siblings(self, store):
        """L'allowlist è di file esatti: non diventa una directory scrivibile."""
        tools = store.build_dream_tools()

        child = await tools.execute(
            "write_file", {"path": "USER.md/evil.txt", "content": "owned"},
        )
        sibling = await tools.execute(
            "write_file", {"path": "memory/history.jsonl", "content": "owned"},
        )

        assert "outside allowed directory" in child
        assert "outside allowed directory" in sibling
        assert not (store.user_file / "evil.txt").exists()
        assert not store.history_file.exists()


def _completed_resp() -> SimpleNamespace:
    return SimpleNamespace(metadata={"_stop_reason": "completed"})


def _errored_resp() -> SimpleNamespace:
    return SimpleNamespace(metadata={"_stop_reason": "error"})


class TestFileStatesWriteCounters:
    def test_counters_start_at_zero(self):
        fs = FileStates()
        assert fs.writes_ok == 0
        assert fs.writes_attempted == 0

    def test_record_write_attempt_increments_only_attempts(self):
        fs = FileStates()
        fs.record_write_attempt()
        fs.record_write_attempt()
        assert fs.writes_attempted == 2
        assert fs.writes_ok == 0

    def test_record_write_increments_successes(self, tmp_path):
        fs = FileStates()
        target = tmp_path / "f.txt"
        target.write_text("x", encoding="utf-8")
        fs.record_write(target)
        assert fs.writes_ok == 1

    def test_record_write_counts_even_when_mtime_unavailable(self):
        """record_write is only called post-write; a missing file still counts."""
        fs = FileStates()
        fs.record_write("/nonexistent/does/not/exist.txt")
        assert fs.writes_ok == 1

    def test_clear_resets_counters(self):
        fs = FileStates()
        fs.record_write_attempt()
        fs.writes_ok = 3
        fs.clear()
        assert fs.writes_ok == 0
        assert fs.writes_attempted == 0


class TestDreamShouldAdvanceCursor:
    def test_not_completed_never_advances(self):
        fs = FileStates()
        fs.writes_ok = 5  # even with writes, a non-clean turn must not advance
        assert MemoryStore.dream_should_advance_cursor(_errored_resp(), fs) is False

    def test_none_resp_does_not_advance(self):
        assert MemoryStore.dream_should_advance_cursor(None, FileStates()) is False

    def test_completed_with_writes_advances(self):
        fs = FileStates()
        fs.record_write_attempt()
        fs.writes_ok = 1
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), fs) is True

    def test_completed_nothing_attempted_advances(self):
        """Legitimate 'nothing to consolidate' — no writes, no attempts."""
        fs = FileStates()
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), fs) is True

    def test_completed_but_all_writes_blocked_does_not_advance(self):
        """Wanted to write but every attempt was blocked/refused — hold the cursor."""
        fs = FileStates()
        fs.record_write_attempt()
        fs.record_write_attempt()
        assert fs.writes_ok == 0
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), fs) is False

    def test_completed_with_partial_success_advances(self):
        """At least one write landed — advancing avoids re-duplicating it."""
        fs = FileStates()
        fs.record_write_attempt()
        fs.record_write_attempt()
        fs.writes_ok = 1
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), fs) is True

    def test_missing_counters_is_conservative(self):
        """A registry without the counters must not silently advance."""
        bogus = SimpleNamespace()  # no writes_ok / writes_attempted
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), bogus) is False


class TestDreamToolsWriteTracking:
    def test_build_dream_tools_exposes_file_states(self, store):
        tools = store.build_dream_tools()
        assert isinstance(tools.file_states, FileStates)
        assert tools.file_states.writes_ok == 0
        assert tools.file_states.writes_attempted == 0

    def test_no_guard_keeps_todays_registry(self, store):
        """Senza guard il registry è quello di sempre: stessi tool, file_states esposto."""
        tools = store.build_dream_tools()
        assert set(tools.tool_names) == {
            "apply_patch",
            "edit_file",
            "memory",
            "read_file",
            "write_file",
        }
        assert isinstance(tools.file_states, FileStates)

    def test_guard_reaches_every_tool_including_read_file(self, store):
        """Il guard va a tutti e quattro, anche a ``read_file`` dove oggi non fa nulla.

        È il costruttore a decidere chi lo riceve: se un tool ne restasse fuori,
        basterebbe che domani diventi write-capable perché sfugga al budget
        senza che nessun test lo noti.
        """
        def guard(path, content):
            return None

        tools = store.build_dream_tools(write_size_guard=guard)
        for name in ("read_file", "edit_file", "apply_patch", "write_file"):
            tool = tools.get(name)
            assert tool is not None
            assert tool._write_size_guard is guard, name

    @pytest.mark.asyncio
    async def test_guard_refusal_blocks_the_write(self, store):
        """Un guard che rifiuta deve fermare la scrittura, non solo commentarla."""
        def guard(path, content):
            return "MEMORY.md is full; consolidate before adding."

        tools = store.build_dream_tools(write_size_guard=guard)
        result = await tools.execute(
            "edit_file",
            {"path": "SOUL.md", "old_text": "Helpful", "new_text": "Precise"},
        )

        assert "consolidate before adding" in result
        assert "Helpful" in store.soul_file.read_text(encoding="utf-8")

    def test_each_run_gets_its_own_file_states(self, store):
        """Per-run: due Dream concorrenti non devono condividere i contatori."""
        first = store.build_dream_tools()
        second = store.build_dream_tools()
        assert first.file_states is not second.file_states
        assert first.file_states is not None
        first.file_states.record_write_attempt()
        assert second.file_states is not None
        assert second.file_states.writes_attempted == 0

    @pytest.mark.asyncio
    async def test_successful_edit_records_write(self, store):
        tools = store.build_dream_tools()
        result = await tools.execute(
            "edit_file",
            {"path": "SOUL.md", "old_text": "Helpful", "new_text": "Precise"},
        )
        assert "Successfully edited" in result
        assert tools.file_states is not None
        assert tools.file_states.writes_ok >= 1
        assert tools.file_states.writes_attempted >= 1
        assert MemoryStore.dream_should_advance_cursor(
            _completed_resp(), tools.file_states
        ) is True

    @pytest.mark.asyncio
    async def test_blocked_write_records_attempt_but_no_success(self, store):
        tools = store.build_dream_tools()
        # history.jsonl lives under memory/ but is not in the editable allowlist.
        store.history_file.write_text("before\n", encoding="utf-8")
        result = await tools.execute(
            "apply_patch",
            {
                "edits": [
                    {
                        "path": "memory/history.jsonl",
                        "action": "replace",
                        "old_text": "before",
                        "new_text": "after",
                    }
                ]
            },
        )
        assert "outside allowed directory" in result
        assert tools.file_states is not None
        assert tools.file_states.writes_attempted >= 1
        assert tools.file_states.writes_ok == 0
        # Turn completed cleanly, but the only write attempt was blocked:
        # the cursor must NOT advance or those history entries are lost.
        assert MemoryStore.dream_should_advance_cursor(
            _completed_resp(), tools.file_states
        ) is False


class TestEphemeralDirect:
    """Tests for the ephemeral flag that skips history.jsonl writes for Dream."""

    @pytest.fixture
    def _make_loop(self, tmp_path):
        """Factory fixture that builds a minimal AgentLoop with mocked deps."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from jenny.agent.loop import AgentLoop
        from jenny.agent.memory import MemoryStore
        from jenny.bus.queue import MessageBus

        store = MemoryStore(tmp_path)
        store.soul_file.write_text("# Soul", encoding="utf-8")
        store.memory_file.write_text("# Memory", encoding="utf-8")

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.supports_tools = True
        provider.generation = MagicMock(max_tokens=4096)
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(content="done", tool_calls=[], finish_reason="stop", usage={})
        )

        with (
            patch("jenny.agent.loop.SessionManager"),
            patch("jenny.agent.loop.SubagentManager") as mock_sub,
            patch("jenny.agent.loop.Consolidator") as mock_consolidator_cls,
        ):
            mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
            mock_consolidator_cls.return_value.maybe_consolidate_by_tokens = AsyncMock()
            loop = AgentLoop(
                bus=bus,
                provider=provider,
                workspace=tmp_path,
                context_window_tokens=8000,
            )

        return loop, store

    async def test_ephemeral_skips_raw_archive(self, tmp_path, _make_loop):
        """When ephemeral=True, raw_archive must not be called."""
        from unittest.mock import patch

        loop, store = _make_loop

        with patch.object(loop.context.memory, "raw_archive") as mock_archive:
            await loop.process_direct(
                "test", session_key="dream:test", ephemeral=True,
            )
            mock_archive.assert_not_called()

    async def test_non_ephemeral_runs_normally(self, tmp_path, _make_loop):
        """Without ephemeral, the normal path returns the model response."""
        loop, store = _make_loop
        response = await loop.process_direct("test", session_key="internal:normal")

        assert response is not None
        assert response.content == "done"
        loop.provider.chat_with_retry.assert_awaited()

    async def test_ephemeral_sets_ctx_flag(self, tmp_path, _make_loop):
        """Verify that ephemeral=True is forwarded to TurnContext."""
        from unittest.mock import patch

        loop, store = _make_loop

        captured = {}

        original_save = loop._state_save

        async def patched_save(ctx):
            captured["ephemeral"] = ctx.ephemeral
            return await original_save(ctx)

        with patch.object(loop, "_state_save", side_effect=patched_save):
            await loop.process_direct(
                "test", session_key="dream:check", ephemeral=True,
            )

        assert captured.get("ephemeral") is True

    async def test_default_ephemeral_is_false(self, tmp_path, _make_loop):
        """By default ephemeral is False in TurnContext."""
        from unittest.mock import patch

        loop, store = _make_loop

        captured = {}

        original_save = loop._state_save

        async def patched_save(ctx):
            captured["ephemeral"] = ctx.ephemeral
            return await original_save(ctx)

        with patch.object(loop, "_state_save", side_effect=patched_save):
            await loop.process_direct("test", session_key="internal:normal")

        assert captured.get("ephemeral") is False

    async def test_ephemeral_skips_consolidator(self, tmp_path, _make_loop):
        """When ephemeral=True, consolidator.maybe_consolidate_by_tokens is not called."""
        from unittest.mock import patch

        loop, store = _make_loop

        with patch.object(
            loop.consolidator, "maybe_consolidate_by_tokens",
        ) as mock_consolidate:
            await loop.process_direct(
                "test", session_key="dream:consolidate-test", ephemeral=True,
            )
            mock_consolidate.assert_not_called()

    async def test_ephemeral_response_reports_stop_reason(self, tmp_path, _make_loop):
        loop, store = _make_loop
        loop.provider.chat_with_retry.return_value = LLMResponse(
            content="provider error",
            finish_reason="error",
        )

        resp = await loop.process_direct(
            "test", session_key="dream:error", ephemeral=True,
        )

        assert resp is not None
        assert resp.metadata["_stop_reason"] == "error"
        assert MemoryStore.dream_run_completed(resp) is False

    async def test_dream_turn_can_skip_unbatched_recent_history(self, tmp_path):
        """Dream must only see the batch selected by build_dream_prompt."""
        from unittest.mock import MagicMock

        from jenny.agent.loop import AgentLoop
        from jenny.bus.queue import MessageBus

        store = MemoryStore(tmp_path)
        for i in range(60):
            store.append_history(f"entry-{i + 1:02d}")

        result = store.build_dream_prompt(max_entries=20)
        assert result is not None
        prompt, cursor = result.prompt, result.cursor
        assert cursor == 20

        captured: dict[str, list[dict]] = {}
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.supports_tools = True
        provider.generation = MagicMock(max_tokens=4096)

        async def chat_with_retry(**kwargs):
            captured["messages"] = kwargs["messages"]
            return LLMResponse(content="done", finish_reason="stop")

        provider.chat_with_retry = chat_with_retry
        loop = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=tmp_path,
            context_window_tokens=8000,
        )

        await loop.process_direct(
            prompt,
            session_key="dream:test",
            ephemeral=True,
            tools=store.build_dream_tools(),
        )

        messages = captured["messages"]
        system_prompt = messages[0]["content"]
        request_text = "\n".join(str(message.get("content", "")) for message in messages)
        assert "# Recent History" not in system_prompt
        assert "entry-01" in request_text
        assert "entry-20" in request_text
        assert "entry-21" not in request_text
        assert "entry-60" not in request_text


class TestEphemeralHooks:
    """Su un turno effimero cadono gli hook che **parlano**, non tutti.

    Fino al 25/08 la condizione era ``not ephemeral`` secca, e l'unico hook extra
    che l'installazione monta è la contabilità dei token: «effimero» voleva quindi
    dire «non misurato». Misurato su ``token-usage.json``: in 27 giorni il bucket
    ``dream`` non era comparso **una volta**, con Dream che gira ogni
    due ore. Ora la scelta la dichiara l'hook (``runs_when_ephemeral()``), e il
    default resta ``False`` — cioè il contratto qui sotto non è cambiato per chi
    non dice niente.
    """

    @pytest.fixture
    def _make_loop_with_spy(self, tmp_path):
        """Build an AgentLoop with a spy hook to verify hook firing behavior."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from jenny.agent.hook import AgentHook
        from jenny.agent.loop import AgentLoop
        from jenny.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.supports_tools = True
        provider.generation = MagicMock(max_tokens=4096)
        provider.chat_with_retry = AsyncMock(
            return_value=MagicMock(
                content="done", finish_reason="stop", tool_calls=[], usage={},
            )
        )

        spy = MagicMock(spec=AgentHook)
        spy.wants_streaming.return_value = False
        # **Esplicito, e non per pignoleria**: con ``spec=AgentHook`` un metodo non
        # configurato torna un ``MagicMock``, che è *truthy* — quindi lo spy
        # dichiarerebbe di voler restare sui turni effimeri senza che nessuno
        # l'abbia deciso, e il test qui sotto proverebbe il contrario di quel che
        # dice il suo nome.
        spy.runs_when_ephemeral.return_value = False
        spy.before_iteration = AsyncMock()
        spy.after_iteration = AsyncMock()

        with (
            patch("jenny.agent.loop.SessionManager"),
            patch("jenny.agent.loop.SubagentManager") as mock_sub,
            patch("jenny.agent.loop.Consolidator") as mock_consolidator_cls,
        ):
            mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
            mock_consolidator_cls.return_value.maybe_consolidate_by_tokens = AsyncMock()
            loop = AgentLoop(
                bus=bus,
                provider=provider,
                workspace=tmp_path,
                context_window_tokens=8000,
                hooks=[spy],
            )

        return loop, spy

    async def test_extra_hooks_skipped_when_ephemeral(self, tmp_path, _make_loop_with_spy):
        """When ephemeral=True, extra hooks must not fire."""
        loop, spy = _make_loop_with_spy

        await loop.process_direct(
            "test", session_key="dream:hook-test", ephemeral=True,
        )
        spy.before_iteration.assert_not_called()
        spy.after_iteration.assert_not_called()

    async def test_a_hook_that_opts_in_fires_on_an_ephemeral_turn(
        self, tmp_path, _make_loop_with_spy
    ):
        """L'eccezione, ed è quella che vale il cambio: misurare non è parlare.

        Il lavoro effimero — Dream, la revisione, il giardiniere — è
        esattamente quello che l'utente non ha chiesto e non vede arrivare, cioè
        quello che conviene misurare di più. `TokenUsageHook` è l'unico che lo
        dichiara.
        """
        loop, spy = _make_loop_with_spy
        spy.runs_when_ephemeral.return_value = True

        await loop.process_direct("test", session_key="dream:hook-test", ephemeral=True)

        spy.after_iteration.assert_called()

    async def test_extra_hooks_fire_for_normal_sessions(self, tmp_path, _make_loop_with_spy):
        """Without ephemeral, extra hooks should fire normally."""
        loop, spy = _make_loop_with_spy

        await loop.process_direct("test", session_key="internal:normal")
        spy.before_iteration.assert_called()
