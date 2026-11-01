"""Tests for ContextBuilder — system prompt and message assembly."""

import hashlib
from pathlib import Path

import pytest

from jenny.agent.context import ContextBuilder
from jenny.session.goal_state import GOAL_STATE_KEY
from jenny.utils.android_assets import _RETIRED_TEMPLATE_DIGESTS, _USER_OWNED_TEMPLATES
from jenny.utils.helpers import load_bundled_template, merge_message_content

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _builder(tmp_path: Path, **kw) -> ContextBuilder:
    return ContextBuilder(workspace=tmp_path, **kw)


# ---------------------------------------------------------------------------
# _build_runtime_context (static)
# ---------------------------------------------------------------------------


class TestBuildRuntimeContext:
    def test_time_only(self):
        ctx = ContextBuilder._build_runtime_context(None, None)
        assert "[Runtime Context" in ctx
        assert "[/Runtime Context]" in ctx
        assert "Current Time:" in ctx
        assert "Channel:" not in ctx

    def test_with_channel_and_chat_id(self):
        ctx = ContextBuilder._build_runtime_context("websocket", "chat123")
        assert "Channel: websocket" in ctx
        assert "Chat ID: chat123" in ctx

    def test_with_sender_id(self):
        ctx = ContextBuilder._build_runtime_context("internal", "direct", sender_id="user1")
        assert "Sender ID: user1" in ctx

    def test_with_timezone(self):
        ctx = ContextBuilder._build_runtime_context(None, None, timezone="Asia/Shanghai")
        assert "Current Time:" in ctx

    def test_no_channel_no_chat_id_omits_both(self):
        ctx = ContextBuilder._build_runtime_context(None, None)
        assert "Channel:" not in ctx
        assert "Chat ID:" not in ctx

    def test_no_sender_id_omits(self):
        ctx = ContextBuilder._build_runtime_context("internal", "direct")
        assert "Sender ID:" not in ctx


# ---------------------------------------------------------------------------
# merge_message_content (helper condiviso in jenny.utils.helpers)
# ---------------------------------------------------------------------------


class TestMergeMessageContent:
    def test_str_plus_str(self):
        result = merge_message_content("hello", "world")
        assert result == "hello\n\nworld"

    def test_empty_left_plus_str(self):
        result = merge_message_content("", "world")
        assert result == "world"

    def test_list_plus_list(self):
        left = [{"type": "text", "text": "a"}]
        right = [{"type": "text", "text": "b"}]
        result = merge_message_content(left, right)
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "b"

    def test_str_plus_list(self):
        right = [{"type": "text", "text": "b"}]
        result = merge_message_content("hello", right)
        assert len(result) == 2
        assert result[0]["text"] == "hello"
        assert result[1]["text"] == "b"

    def test_list_plus_str(self):
        left = [{"type": "text", "text": "a"}]
        result = merge_message_content(left, "world")
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "world"

    def test_none_plus_str(self):
        result = merge_message_content(None, "hello")
        assert result == [{"type": "text", "text": "hello"}]

    def test_str_plus_none(self):
        result = merge_message_content("hello", None)
        assert result == [{"type": "text", "text": "hello"}]

    def test_none_plus_none(self):
        result = merge_message_content(None, None)
        assert result == []

    def test_list_items_not_dicts_wrapped(self):
        result = merge_message_content(["raw_item"], None)
        assert result == [{"type": "text", "text": "raw_item"}]


# ---------------------------------------------------------------------------
# _load_bootstrap_files
# ---------------------------------------------------------------------------


class TestLoadBootstrapFiles:
    def test_no_bootstrap_files(self, tmp_path):
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "Be helpful." in result

    def test_multiple_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        (tmp_path / "SOUL.md").write_text("Soul.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "## SOUL.md" in result
        assert "Rules." in result
        assert "Soul." in result

    def test_a_non_utf8_bootstrap_file_does_not_break_the_prompt(self, tmp_path):
        """Un byte latin-1 in ``USER.md`` non spegne ogni turno dell'installazione.

        I file di identita' si leggono per ogni specie di sessione: prima un
        ``UnicodeDecodeError`` risaliva fino a «Sorry, I encountered an error»
        su tutti i turni finche' qualcuno non ri-salvava il file.
        """
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        (tmp_path / "USER.md").write_bytes(b"Nome: Andr\xe9\n")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "## USER.md" in result
        assert "Nome: Andr\ufffd" in result

    def test_an_unreadable_bootstrap_file_is_skipped(self, tmp_path, monkeypatch):
        """Un ``OSError`` in lettura salta quel file e tiene gli altri."""
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        (tmp_path / "SOUL.md").write_text("Soul.", encoding="utf-8")
        original = Path.read_text

        def _read_text(self, *args, **kwargs):
            if self.name == "SOUL.md":
                raise PermissionError("denied")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _read_text)
        result = _builder(tmp_path)._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "## SOUL.md" not in result

    def test_all_bootstrap_files(self, tmp_path):
        for name in ContextBuilder.BOOTSTRAP_FILES:
            (tmp_path / name).write_text(f"Content of {name}", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        for name in ContextBuilder.BOOTSTRAP_FILES:
            assert f"## {name}" in result

    def test_legacy_tools_md_is_not_bootstrapped(self, tmp_path):
        (tmp_path / "TOOLS.md").write_text("workspace tool notes", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "TOOLS.md" not in result
        assert "workspace tool notes" not in result

    def test_utf8_content(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("用中文回复", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "用中文回复" in result

    def test_identity_files_are_not_capped(self, tmp_path):
        """``SOUL.md`` e ``USER.md`` entrano **interi**, e non per distrazione.

        Sono identità: ``SOUL.md`` non è riscritto in nessun altro punto del
        prompt, e la coda di ``USER.md`` non è archiviata da nessuna parte.
        Tagliare l'ultima riga di "chi sei" non è come tagliare l'ultima voce di
        una rubrica — là il tetto toglie un link e la pagina resta raggiungibile,
        qui toglierebbe la cosa stessa, a ogni turno, e l'avviso non avrebbe dove
        rimandare. Un tetto in lettura, poi, non sa distinguere una regola di
        comportamento da una nota di piattaforma stantia: è la stessa ragione per
        cui ``soul_budget_chars`` esce di fabbrica a 0 e lo strumento per quel
        file è la review pass, che legge prima di scegliere.

        L'argomento sta in ``docs/using/memory.md`` ("The budgets bound what
        Dream writes, not what a turn pays"). Cambiare questo test è una
        decisione, non un dettaglio di implementazione.
        """
        soul = "\n".join(f"- behaviour rule {i}" for i in range(1500))
        user = "\n".join(f"- preference {i}" for i in range(1500))
        (tmp_path / "SOUL.md").write_text(soul, encoding="utf-8")
        (tmp_path / "USER.md").write_text(user, encoding="utf-8")
        result = _builder(tmp_path)._load_bootstrap_files()
        assert f"## SOUL.md\n\n{soul}" in result
        assert f"## USER.md\n\n{user}" in result


# ---------------------------------------------------------------------------
# _load_bootstrap_files — guardia sul template intatto
# ---------------------------------------------------------------------------


def _bundled(name: str) -> str:
    from importlib.resources import files as pkg_files

    tpl = pkg_files("jenny") / "templates" / name
    if not tpl.is_file():
        pytest.skip(f"{name} template not bundled")
    return tpl.read_text(encoding="utf-8")


def _fixture(name: str) -> str:
    """Un template ritirato, letto da ``fixtures/`` e non da una stringa qui dentro.

    È la stessa ragione registrata da ``97d7b38``: alcune di quelle righe
    finiscono con uno spazio, trascritte in un sorgente Python le toglierebbe
    ``ruff`` (W291), il digest non combacerebbe più e il test proverebbe
    qualcosa di diverso da quello che c'è sui telefoni. I file sono estratti con
    ``git show <sha>:jenny/templates/<nome>``, non ricopiati a mano.
    """
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def _retired_user_template() -> str:
    """``jenny/templates/USER.md`` come spediva da 0.3.0 (8833b94) a 0.7.1."""
    return _fixture("user_md_retired_0.3.0.md")


# Le tre versioni ritirate di ``AGENTS.md``. Tutte e tre sono candidate vive: un
# telefono porta quella che era bundled al *suo* primo avvio, per sempre.
_RETIRED_AGENTS_FIXTURES = [
    "agents_md_retired_v0.3.0.md",
    "agents_md_retired_6c5dba8_unreleased.md",
    "agents_md_retired_v0.6.6.md",
]


class TestBootstrapTemplateGuard:
    """Un file di bootstrap mai toccato non è contenuto scritto dall'utente.

    ``USER.md`` e ``AGENTS.md`` intatti non dicono niente né sull'utente né sul
    workspace e vengono omessi (stessa risposta che ``MEMORY.md`` riceve in
    ``build_system_prompt``); ``SOUL.md`` intatto è l'identità di serie e resta,
    ma etichettato.

    Da 0.8.0 quei due template spediscono vuoti, quindi i loro casi qui li
    prende la guardia sul contenuto vuoto (``TestEmptiedBootstrapFile``) prima
    di arrivare al confronto col template: l'esito è lo stesso, la strada no.
    A coprire ``_BOOTSTRAP_SKIP_IF_TEMPLATE`` su del testo vero resta
    ``TestRetiredTemplates``, che lavora sulle versioni con la prosa — cioè su
    quello che c'è davvero sui telefoni già installati.
    """

    def test_untouched_user_md_is_skipped(self, tmp_path):
        (tmp_path / "USER.md").write_text(_bundled("USER.md"), encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert result == ""

    def test_edited_user_md_is_injected_without_notice(self, tmp_path):
        (tmp_path / "USER.md").write_text("- **Name**: Luca", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## USER.md" in result
        assert "Luca" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_untouched_agents_md_is_skipped(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(_bundled("AGENTS.md"), encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    @pytest.mark.parametrize("filename", ["SOUL.md"])
    def test_untouched_default_is_kept_but_labelled(self, tmp_path, filename):
        content = _bundled(filename)
        (tmp_path / filename).write_text(content, encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert f"## {filename}" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE in result
        # L'identità di serie non deve sparire: niente regressione di
        # personalità su un'installazione nuova.
        assert content.strip() in result

    @pytest.mark.parametrize("filename", ["AGENTS.md", "SOUL.md"])
    def test_edited_file_carries_no_notice(self, tmp_path, filename):
        # Un file *modificato* entra nel prompt in entrambi i casi: quello che
        # cambia con lo skip è solo il ramo del template intatto.
        (tmp_path / filename).write_text(_bundled(filename) + "\n\nExtra rule.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "Extra rule." in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_pristine_workspace_omits_both_placeholders(self, tmp_path):
        for name in ContextBuilder.BOOTSTRAP_FILES:
            (tmp_path / name).write_text(_bundled(name), encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## SOUL.md" in result
        assert "## AGENTS.md" not in result
        assert "## USER.md" not in result


class TestRetiredTemplates:
    """Riscrivere un template non deve promuovere i vecchi a scrittura dell'utente.

    Il riconoscimento confronta con la copia bundled *corrente*, quindi ogni
    riscrittura scollegherebbe le installazioni seedate con la precedente e mai
    toccate da Dream: il modulo a caselle tornerebbe nel prompt, per giunta senza
    etichetta perché prenderebbe il ramo "modificato dall'utente".
    """

    def test_retired_user_template_is_still_recognised(self, tmp_path):
        (tmp_path / "USER.md").write_text(_retired_user_template(), encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_retired_digest_does_not_swallow_real_user_content(self, tmp_path):
        (tmp_path / "USER.md").write_text(
            _retired_user_template() + "\n- **Name**: Luca\n", encoding="utf-8"
        )
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "Luca" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_a_bom_does_not_promote_a_template_to_user_prose(self, tmp_path):
        """Aprire e salvare il file con un editor Windows non lo rende tuo.

        ``"\\ufeff"`` non è whitespace per Python, quindi sopravviveva allo
        ``strip()`` che precede il confronto: da lì il file non combaciava più con
        nessun digest e rientrava in **ogni** prompt come prosa dell'utente, senza
        nemmeno l'etichetta "default intatto". Un modulo a caselle vuote presentato
        al modello come preferenze di una persona.
        """
        (tmp_path / "USER.md").write_text(
            "﻿" + _retired_user_template(), encoding="utf-8"
        )
        builder = _builder(tmp_path)

        assert builder._load_bootstrap_files() == ""

    def test_current_user_template_digest_is_pinned(self):
        """Chi riscrive ``USER.md`` deve ritirare esplicitamente la versione uscente.

        Senza questo, il prossimo rewrite reintroduce il difetto in silenzio: si
        accorgerebbe solo un'installazione vergine aggiornata mesi dopo. Se questo
        test fallisce, aggiungi il digest qui atteso a
        ``_RETIRED_TEMPLATE_DIGESTS["USER.md"]`` (in
        ``jenny/utils/android_assets.py``) con l'etichetta della sua finestra di
        release, e sostituiscilo con quello nuovo.

        **Il template oggi è vuoto**, quindi il digest atteso è ``sha256("")``.
        Due conseguenze che chi tocca questo test deve avere in testa:

        * ``sha256("")`` è il digest del template *corrente*, non di uno
          ritirato, e non va registrato come tale in nessun caso. Registrarlo
          armerebbe ``retire_withdrawn_templates`` contro **ogni** file vuoto o
          fatto di soli spazi che si trova sul disco di un utente.
        * il digest della prosa uscente (``e23a60be…``) è già nel registro: è
          quello che tiene fuori dal prompt gli ``USER.md`` mai toccati sui
          telefoni già installati, che altrimenti smetterebbero di combaciare col
          bundle e rientrerebbero come contenuto dell'utente.
        """
        current = hashlib.sha256(
            _bundled("USER.md").strip().encode("utf-8")
        ).hexdigest()
        assert current == hashlib.sha256(b"").hexdigest()
        assert current == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert current not in _RETIRED_TEMPLATE_DIGESTS["USER.md"]
        assert (
            "e23a60be0336c5220d3d0dbd256907f66b590156459422a244dcd24685eb49b7"
            in _RETIRED_TEMPLATE_DIGESTS["USER.md"]
        ), "la prosa uscente non è stata ritirata: rientra nel prompt di ogni telefono"

    @pytest.mark.parametrize("fixture", _RETIRED_AGENTS_FIXTURES)
    def test_every_retired_agents_digest_is_recognised(self, tmp_path, fixture):
        """Nessuna delle tre versioni di "# Agent Instructions" torna nel prompt.

        Il manuale di cron che spediva dentro ``AGENTS.md`` ora vive in
        ``agent/scheduling.md``; la copia rimasta sul disco di un telefono è
        testo nostro, ritirato e in due casi su tre pure contraddetto.
        """
        (tmp_path / "AGENTS.md").write_text(_fixture(fixture), encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_retired_agents_digest_does_not_swallow_real_user_content(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(
            _fixture("agents_md_retired_v0.3.0.md") + "\n- Deploy con `./gradlew`.\n",
            encoding="utf-8",
        )
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "./gradlew" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_current_agents_template_digest_is_pinned(self):
        """Chi riscrive ``AGENTS.md`` deve ritirare esplicitamente la versione uscente.

        Gemello di ``test_current_user_template_digest_is_pinned``: senza,
        il prossimo rewrite rimette il testo uscente nel prompt di ogni
        installazione vergine, per giunta senza etichetta. Se questo test
        fallisce, sposta il digest qui atteso dentro
        ``_RETIRED_TEMPLATE_DIGESTS["AGENTS.md"]`` (in
        ``jenny/utils/android_assets.py``) e mettine qui quello nuovo.

        Vale parola per parola l'avvertenza del gemello sul template vuoto:
        ``sha256("")`` non si registra mai come ritirato, e la prosa uscente
        (``f7168ac0…``) invece sì, o gli ``AGENTS.md`` intatti già sul campo
        rientrano nel prompt.
        """
        current = hashlib.sha256(
            _bundled("AGENTS.md").strip().encode("utf-8")
        ).hexdigest()
        assert current == hashlib.sha256(b"").hexdigest()
        assert current == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert current not in _RETIRED_TEMPLATE_DIGESTS["AGENTS.md"]
        assert (
            "f7168ac0aacf6424203c6173e46ed333981f57c3d491f055fe1358c9b9614569"
            in _RETIRED_TEMPLATE_DIGESTS["AGENTS.md"]
        ), "la prosa uscente non è stata ritirata: rientra nel prompt di ogni telefono"

    def test_a_template_with_retired_versions_is_never_labelled(self):
        """L'etichetta dice "questo file è ancora il template spedito con l'app",
        e su una versione ritirata sarebbe una bugia: spedita lo era, ma un'altra
        volta e da un'altra versione.

        Che oggi non possa succedere è vero e sta scritto in un commento in
        ``context.py``: chi ha digest ritirati sta in ``_BOOTSTRAP_SKIP_IF_TEMPLATE``,
        quindi non arriva mai al ramo che etichetta. Un commento però non è una
        guardia — basta ritirare una versione di ``SOUL.md`` (l'unico file che
        l'etichetta la riceve davvero) perché la frase inizi a mentire, in
        silenzio e su ogni installazione seedata in quella finestra. Vale qui la
        stessa regola dei digest un file più in là: non è un promemoria, il test
        fallisce finché non lo si fa.

        Le due uscite sono entrambe buone: aggiungere il nome a
        ``_BOOTSTRAP_SKIP_IF_TEMPLATE``, oppure dare a quel ramo una seconda
        etichetta che dica la verità su una versione ritirata.
        """
        for name in _RETIRED_TEMPLATE_DIGESTS:
            if name not in ContextBuilder.BOOTSTRAP_FILES:
                # Non è un file di bootstrap: quel ramo non lo vede proprio.
                # ``memory/MEMORY.md`` ha una guardia sua (``_is_template_content``
                # in ``build_system_prompt``), che omette e non etichetta.
                continue
            assert name in ContextBuilder._BOOTSTRAP_SKIP_IF_TEMPLATE, (
                f"{name} ha versioni ritirate e può ricevere "
                "_BOOTSTRAP_TEMPLATE_NOTICE, che su una versione ritirata è falsa: "
                "aggiungilo a _BOOTSTRAP_SKIP_IF_TEMPLATE o dai a quel ramo "
                "un'etichetta che dica la verità"
            )


class TestEmptiedBootstrapFile:
    """Un file di bootstrap svuotato non deve lasciare un heading vuoto nel prompt.

    Non è un caso di laboratorio: ``agent/dream_review.md`` istruisce la
    revisione a cancellare "l'introduzione che spiega a cosa serve il file", e
    il template di ``USER.md`` è fatto *solo* di quello. Svuotato, il file non
    combacia più con nessun digest, quindi esce da
    ``_BOOTSTRAP_SKIP_IF_TEMPLATE`` e rientrerebbe nel prompt come contenuto
    dell'utente — ``## USER.md`` seguito dal nulla, a ogni turno e per sempre.
    """

    def test_emptied_file_contributes_nothing(self, tmp_path):
        (tmp_path / "USER.md").write_text("", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## USER.md" not in result
        assert result == ""

    def test_whitespace_only_file_contributes_nothing(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("\n\n   \n\t\n", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## SOUL.md" not in result
        assert result == ""

    def test_empty_file_does_not_hide_a_sibling_with_content(self, tmp_path):
        # Pin di regressione: la guardia salta *quel* file, non il blocco.
        (tmp_path / "USER.md").write_text("   \n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("- Deploy con `./gradlew`.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "./gradlew" in result
        assert "## USER.md" not in result

    def test_empty_file_is_skipped_even_when_the_bundled_template_is_empty(
        self, tmp_path, monkeypatch
    ):
        """L'altra lettura di "vuoto", che arriva quando i template si svuotano.

        Oggi ``_is_template_content("", "SOUL.md")`` è ``False`` (vuoto legge
        come contenuto dell'utente); con un template bundled vuoto diventa
        ``True``, e ``SOUL.md`` — che non sta in
        ``_BOOTSTRAP_SKIP_IF_TEMPLATE`` — prenderebbe il ramo che etichetta,
        emettendo un avviso con sotto niente. Entrambe le letture devono
        restare innocue, quindi la guardia va prima del confronto col template.
        """
        monkeypatch.setattr("jenny.agent.context.load_bundled_template", lambda _p: "")
        assert ContextBuilder._is_template_content("", "SOUL.md") is True
        (tmp_path / "SOUL.md").write_text("", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert result == ""
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result


# ---------------------------------------------------------------------------
# agent/scheduling.md — dove va un lavoro per dopo
# ---------------------------------------------------------------------------

# Il titolo del template. Cercarlo nel prompt è come si distingue "il blocco c'è"
# da "il prompt nomina cron da qualche altra parte".
_SCHEDULING_HEADING = "# Work for Later"

# Il registry che ``build_dream_tools`` costruisce davvero (``memory.py``):
# lettura e scrittura, nient'altro. Nessun ``cron``.
_DREAM_TOOLS = ["read_file", "write_file", "edit_file", "apply_patch"]


class TestSchedulingBlock:
    """La guida sul cron va solo a chi il tool ce l'ha.

    Stava nel template di ``AGENTS.md``, cioè in un file che si crea al primo
    avvio e non si aggiorna mai più, e ci andava *sempre*: anche a Dream,
    che ``cron`` non l'ha mai avuto.
    """

    def test_scheduling_block_is_rendered_when_cron_is_available(self, tmp_path):
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt(available_tools=["cron", "read_file"])
        assert _SCHEDULING_HEADING in prompt
        assert "mode='monitor'" in prompt

    def test_scheduling_block_is_absent_without_the_cron_tool(self, tmp_path):
        """Il difetto che la guardia chiude: Dream riceveva l'istruzione di
        schedulare con un tool che il suo registry non contiene."""
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt(available_tools=_DREAM_TOOLS)
        assert _SCHEDULING_HEADING not in prompt

    def test_scheduling_block_renders_when_tools_are_unknown(self, tmp_path):
        """``None`` vuol dire "non lo so", non "il tool non c'è".

        Nessun registry per-turno e nessuna callable dal costruttore: si rende,
        come faceva ``AGENTS.md``. Senza questo, un refactor può trasformare la
        guardia in un silenzio senza che niente se ne accorga.
        """
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt(available_tools=None)
        assert _SCHEDULING_HEADING in prompt

    def test_cron_tool_name_constant_matches(self):
        """``_CRON_TOOL_NAME`` non importa ``CronTool``; questo test sì.

        L'import sta qui e non in ``context.py`` perché lì tirerebbe dentro
        tutto il package cron in un modulo che importa mezzo repo.
        """
        from jenny.agent.context import _CRON_TOOL_NAME
        from jenny.agent.tools.cron import CronTool

        # ``name`` è una property e non serve un servizio cron per leggerla:
        # ``None`` basta, il tool non viene usato.
        assert _CRON_TOOL_NAME == CronTool(cron_service=None).name  # type: ignore[arg-type]

    def test_scheduling_block_stays_small(self):
        """Un tetto, non un'abitudine.

        Questo blocco si paga a ogni turno in cui il tool esiste, compresi
        quelli in cui l'utente chiede che ore sono. È l'unico meccanismo che
        tiene fermo il confine fra i quattro posti in cui questa regola vive.
        """
        from jenny.utils.prompt_templates import render_template

        # 1450 e non 1600: il testo reso ne occupa ~1310, e un tetto che lascia il
        # 40% di margine non dice mai di no — cioè non fa il suo mestiere. Chi ha
        # bisogno di sforarlo ha quasi sempre bisogno della skill.
        #
        # Alzato da 1300 il 2026-08-17 per la riga sulle ``## Active Tasks``
        # dell'heartbeat, che non c'è più (31/10/2026); il tetto resta, e lo
        # spazio è andato al singolo con ``date`` e alla ``condition``.
        rendered = render_template("agent/scheduling.md")
        assert len(rendered) <= 1450, (
            f"agent/scheduling.md è {len(rendered)} caratteri: sta tornando un manuale. "
            "La profondità — sintassi, fusi orari, esempi, semantica di `list` — va in "
            "`skills/cron/SKILL.md`, che si legge su richiesta invece che a ogni turno."
        )

    def test_no_other_system_prompt_teaches_scheduling(self):
        """Un solo posto nel prompt di sistema, altrimenti la guardia non serve.

        Trovato da una rilettura: ``agent/tool_contract.md`` portava una sezione
        "Scheduling and Background Work" che diceva le stesse tre cose — usa
        ``cron``, aggiorna ``HEARTBEAT.md``, non scrivere il promemoria in
        memoria — e la portava **sempre**, perché quel template non è gated.
        Quindi Dream continuava a ricevere l'istruzione di schedulare con un
        tool che il suo registry non contiene: la guardia di ``scheduling.md``
        copriva una copia su due, che è come non averla.

        La regola sta in ``agent/scheduling.md`` (dietro il tool) e in
        ``skills/cron/SKILL.md`` (su richiesta). Nessun altro template di
        sistema la ripete.

        **Questo è il pin stretto su ``tool_contract.md``, non la guardia.** Le
        cinque frasi sono i letterali che erano stati cancellati da lì, cercati
        con l'apice singolo, e su un solo tipo di sorgente. La guardia vera —
        template *più* skill, description dei tool, e i letterali Python di
        ``jenny/cron`` e ``jenny/runtime`` letti con ``ast``, tutti con regex
        indifferenti alle virgolette — sta in
        ``tests/agent/test_prompt_corpus_scheduling.py``, che documenta anche
        perché questa qui da sola non bastava.
        """
        from jenny.utils.android_assets import _SYSTEM_PROMPT_TEMPLATES
        from jenny.utils.helpers import load_bundled_template

        # Frasi che *insegnano a schedulare*: si cercano le istruzioni, non i
        # nomi dei file.
        teaching = ("use the cron tool", "mode='reminder'", "mode='monitor'",
                    "for heartbeat tasks", "recurring jobs")
        for name in _SYSTEM_PROMPT_TEMPLATES:
            if name == "agent/scheduling.md":
                continue
            body = (load_bundled_template(name) or "").lower()
            for phrase in teaching:
                assert phrase not in body, (
                    f"{name} insegna a schedulare ({phrase!r}). Quella regola ha una casa "
                    "sola nel prompt di sistema, `agent/scheduling.md`, perché è l'unica "
                    "resa solo quando il tool `cron` esiste davvero nel turno."
                )


# ---------------------------------------------------------------------------
# I template dell'utente non contengono guida di sistema
# ---------------------------------------------------------------------------


class TestUserOwnedTemplatesCarryNoSystemGuidance:
    """Un file che si crea una volta sola non può contenere una regola che cambia.

    È il difetto di ``AGENTS.md`` reso controllabile: ``_USER_OWNED_TEMPLATES``
    si estrae con ``skip_existing=True``, quindi qualunque cosa ci si scriva
    dentro non raggiunge mai un'installazione esistente.
    """

    @pytest.mark.parametrize("name", _USER_OWNED_TEMPLATES)
    def test_the_stale_cron_parameters_are_gone(self, name):
        """``USER_ID``/``CHANNEL`` e ``web:default`` non esistono.

        Il tool ``cron`` non ha né ``user_id`` né ``channel``
        (``_CRON_PARAMETERS``) e ``jenny/session/keys.py`` conia
        ``unified:default``/``websocket:default``: quella riga diceva al modello
        di procurarsi valori per parametri che non ci sono.

        Guardava solo ``AGENTS.md``, che da 0.8.0 spedisce vuoto: puntato lì non
        poteva più fallire, e una guardia che non può fallire è peggio di
        nessuna guardia perché sembra ancora coprire qualcosa. Vale per tutti i
        template dell'utente — il difetto non era di quel file, era di *scrivere
        parametri di tool* in un file che nessun aggiornamento raggiunge.
        """
        text = _bundled(name)
        assert "USER_ID" not in text
        assert "web:default" not in text

    @pytest.mark.parametrize("name", _USER_OWNED_TEMPLATES)
    def test_no_user_owned_template_documents_the_tools(self, name):
        """Tre dei quattro template ora sono vuoti e passano per costruzione.

        La guardia resta viva su quello che del testo ce l'ha ancora —
        ``SOUL.md`` (la personalità di serie) — ed è lì che serve, perché è
        l'unico in cui si può ancora essere tentati di scrivere una
        istruzione. Sui tre vuoti il pin è
        ``test_the_user_owned_templates_ship_no_prose`` in
        ``tests/utils/test_template_refresh.py``: finché quello tiene, qui non
        c'è niente da coprire; se cede, questo torna a coprire quattro file.
        """
        text = _bundled(name).lower()
        for token in ("cron", "mode=", "every_seconds", "apply_patch"):
            assert token not in text, (
                f"{name} documenta `{token}`: è guida di sistema in un file che si "
                "crea al primo avvio e non si aggiorna mai più. Va sotto "
                "`jenny/templates/agent/` o in una skill."
            )


# ---------------------------------------------------------------------------
# _is_template_content (static)
# ---------------------------------------------------------------------------


class TestIsTemplateContent:
    def test_nonexistent_template_returns_false(self):
        assert ContextBuilder._is_template_content("anything", "nonexistent/path.md") is False

    def test_content_matching_template(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("jenny") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        original = tpl.read_text(encoding="utf-8")
        assert ContextBuilder._is_template_content(original, "memory/MEMORY.md") is True

    def test_content_matching_a_template_that_is_not_empty(self):
        """Lo stesso round-trip, ma su un template con del testo dentro.

        ``memory/MEMORY.md`` spedisce zero byte, quindi il test qui sopra
        confronta ``""`` con ``""``. Non è inerte — rompendo il confronto col
        bundle fallisce — ma è degenere: non esercita la normalizzazione, che è
        dove i difetti veri sono già stati (un BOM UTF-8 sopravvive a
        ``strip()`` e faceva smettere di combaciare un file che l'utente non
        aveva mai scritto). ``SOUL.md`` porta 2 kB di testo, intestazioni e
        righe vuote, quindi copre quella metà.
        """
        original = load_bundled_template("SOUL.md")
        assert original, "SOUL.md non è più un template bundled non vuoto"
        assert ContextBuilder._is_template_content(original, "SOUL.md") is True
        assert ContextBuilder._is_template_content("\ufeff" + original, "SOUL.md") is True
        assert ContextBuilder._is_template_content(original + "\nmia riga\n", "SOUL.md") is False

    def test_the_retired_memory_scaffold_is_still_recognised(self):
        """Il caso che conta non è il round-trip col bundle: è quello sui telefoni.

        ``memory/MEMORY.md`` spedisce zero byte, quindi il test qui sopra
        confronta ``""`` con ``""``. Misurato per mutazione il 31/08/2026: non
        passa comunque — rompendo il confronto col bundle fallisce — ma è
        degenere, e la copertura della normalizzazione sta nel test su
        ``SOUL.md``. Quello che nessuno dei due copre è lo scaffold ritirato —
        lo scaffold "# Long-term Memory" con ``## User Information`` e
        ``## Preferences``, che contraddicono il routing di ``agent/dream.md``.

        Su un'installazione esistente quel file combaciava col bundle e veniva
        soppresso; svuotare il template gli toglie quella copertura, e a
        rimetterla è il digest ritirato. Se questo test fallisce, quelle
        intestazioni sono tornate nel prompt di ogni telefono già installato, a
        insegnare il contrario delle regole di sistema.
        """
        retired = _fixture("memory_md_retired_v0.3.0.md")
        assert "## User Information" in retired, "la fixture non è più lo scaffold ritirato"
        assert ContextBuilder._is_template_content(retired, "memory/MEMORY.md") is True

    def test_modified_content_returns_false(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("jenny") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        assert ContextBuilder._is_template_content("totally different", "memory/MEMORY.md") is False


# ---------------------------------------------------------------------------
# Bundled bootstrap templates
# ---------------------------------------------------------------------------


class TestBundledToolContract:
    def test_tool_contract_balances_general_and_coding_workflows(self):
        from importlib.resources import files as pkg_files

        tpl = pkg_files("jenny") / "templates" / "agent" / "tool_contract.md"
        content = tpl.read_text(encoding="utf-8")

        assert "## General Tool Contract" in content
        assert "Use the narrowest structured tool" in content
        assert "## File and Coding Workflows" in content
        assert "apply_patch" in content
        assert "## Web and External Information" in content
        assert "## Messaging and Media" in content
        assert "pure coding" not in content.lower()
        # "Scheduling and Background Work" è uscita di qui: diceva le stesse tre
        # cose di ``agent/scheduling.md`` ma senza la sua guardia, quindi Dream
        # continuava a leggere di un tool che non ha. V.
        # ``test_no_other_system_prompt_teaches_scheduling``.
        assert "## Scheduling and Background Work" not in content

    def test_tool_contract_is_injected_without_workspace_file(self, tmp_path):
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt()

        assert "# Tool Usage Notes" in prompt
        assert "## General Tool Contract" in prompt


class TestOutputDestinationRule:
    """Dove finiscono i file prodotti — regola valida in *entrambi* i rami.

    Il doppio caso non è ridondanza: l'orchestratore non scrive file, ma è lui
    a scrivere i prompt dei subagenti con ``spawn``, quindi senza la regola
    detta loro la destinazione sbagliata. Una guardia
    ``{% if not orchestrator %}`` aggiunta per distrazione deve far fallire
    questo test.
    """

    @pytest.mark.parametrize("orchestrator", [True, False])
    def test_absolute_output_path_and_root_ban_in_prompt(self, tmp_path, orchestrator):
        prompt = _builder(tmp_path).build_system_prompt(orchestrator=orchestrator)
        expected = str(tmp_path.resolve() / "output")

        assert expected in prompt
        assert "Never create a new file in the workspace root" in prompt
        # Senza "you may edit but never add to" l'agente smette di aggiornare
        # AGENTS.md e USER.md, che invece deve continuare a fare.
        assert "you may edit but never add to" in prompt

    def test_rule_sits_at_the_end_of_the_tool_contract(self, tmp_path):
        """In fondo apposta: fra due istruzioni in conflitto vince l'ultima."""
        prompt = _builder(tmp_path).build_system_prompt()
        contract_start = prompt.index("# Tool Usage Notes")
        # Il contratto è l'ultima sezione quando non c'è altro dopo (workspace
        # nudo): niente separatore da cercare, si arriva in fondo.
        contract_end = prompt.find("\n\n---\n\n", contract_start)
        contract = prompt[contract_start:] if contract_end < 0 else prompt[contract_start:contract_end]

        # L'ancora era "## Scheduling and Background Work", uscita dal contratto
        # insieme alla regola che duplicava. "Messaging and Media" la sostituisce
        # nello stesso ruolo: una sezione che sta prima, così l'asserzione parla
        # ancora dell'ordine e non della presenza di un titolo.
        assert contract.index(str(tmp_path.resolve() / "output")) > contract.index(
            "## Messaging and Media"
        )

    def test_download_precedent_is_kept(self, tmp_path):
        """La regola nuova generalizza quella sui download, non la sostituisce."""
        prompt = _builder(tmp_path).build_system_prompt(orchestrator=False)
        assert "Save downloaded files under `downloads/` only, never in the workspace root" in prompt

    def test_output_dir_is_not_created_just_by_building_a_prompt(self, tmp_path):
        _builder(tmp_path).build_system_prompt()
        assert not (tmp_path / "output").exists()


# ---------------------------------------------------------------------------
# _build_user_content
# ---------------------------------------------------------------------------


class TestBuildUserContent:
    def test_no_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", None)
        assert result == "hello"

    def test_empty_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [])
        assert result == "hello"

    def test_nonexistent_media_file_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", ["/nonexistent/image.png"])
        assert result == "hello"

    def test_non_image_file_returns_string(self, tmp_path):
        txt = tmp_path / "doc.txt"
        txt.write_text("not an image", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(txt)])
        assert result == "hello"

    def test_valid_image_returns_list(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(png)])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert result[1]["type"] == "text"
        assert result[1]["text"] == "hello"

    def test_image_meta_includes_path(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(png)])
        assert "_meta" in result[0]
        assert "path" in result[0]["_meta"]


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_returns_nonempty_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_identity_section(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "Android" in result

    def test_identity_includes_platform_policy(self, tmp_path):
        builder = _builder(tmp_path)
        identity = builder._get_identity()
        assert "Android" in identity
        assert "Platform Policy (Android)" in identity
        assert "Chaquopy Python runtime" in identity

    def test_includes_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful and concise.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "Be helpful and concise." in result

    def test_includes_session_summary(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt(session_summary="Previous chat about Python.")
        assert "Previous chat about Python." in result
        assert "[Archived Context Summary]" in result

    def test_sections_separated_by_separator(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt(session_summary="Summary.")
        assert "\n\n---\n\n" in result

    def test_no_bootstrap_no_summary(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "## AGENTS.md" not in result
        assert "[Archived Context Summary]" not in result


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_basic_empty_history(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "hello" in str(messages[1]["content"])

    def test_runtime_context_injected(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello", channel="internal", chat_id="direct")
        user_msg = str(messages[-1]["content"])
        assert "[Runtime Context" in user_msg
        assert "hello" in user_msg

    def test_session_metadata_injects_active_goal_state(self, tmp_path):
        builder = _builder(tmp_path)
        meta = {
            GOAL_STATE_KEY: {"status": "active", "objective": "Finish docs migration."},
        }
        messages = builder.build_messages(
            [],
            "hi",
            channel="internal",
            chat_id="x",
            session_metadata=meta,
        )
        user_msg = str(messages[-1]["content"])
        assert "Goal (active):" in user_msg
        assert "Finish docs migration." in user_msg

    def test_goal_state_does_not_leak_without_session_metadata(self, tmp_path):
        builder = _builder(tmp_path)
        other_session_meta = {
            GOAL_STATE_KEY: {"status": "active", "objective": "Other chat goal."},
        }

        with_goal = builder.build_messages(
            [],
            "hi",
            channel="websocket",
            chat_id="chat-a",
            session_metadata=other_session_meta,
        )
        without_goal = builder.build_messages(
            [],
            "hi",
            channel="websocket",
            chat_id="chat-b",
            session_metadata={},
        )

        assert "Other chat goal." in str(with_goal[-1]["content"])
        assert "Other chat goal." not in str(without_goal[-1]["content"])
        assert "Goal (active):" not in str(without_goal[-1]["content"])

    def test_current_runtime_lines_are_injected(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages(
            [],
            "please use @zoom tonight",
            current_runtime_lines=[
                "MCP Preset Attachment: @zoom (Zoom; transport=mcp; tool_prefix=mcp_zoom_).",
            ],
        )
        user_msg = str(messages[-1]["content"])

        assert "MCP Preset Attachment: @zoom" in user_msg

    def test_consecutive_same_role_merged(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "user", "content": "previous user message"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 2  # system + merged user
        assert "previous user message" in str(messages[1]["content"])
        assert "new message" in str(messages[1]["content"])

    def test_different_role_appended(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "previous response"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 3  # system + assistant + user

    def test_media_with_history(self, tmp_path):
        png = tmp_path / "img.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "see this"}]
        messages = builder.build_messages(history, "check image", media=[str(png)])
        user_msg = messages[-1]["content"]
        assert isinstance(user_msg, list)
        assert any(b.get("type") == "image_url" for b in user_msg)


# ---------------------------------------------------------------------------
# Jenny Apps summary in the system prompt
# ---------------------------------------------------------------------------


class TestAppsSummarySection:
    def test_apps_section_present_when_apps_exist(self, tmp_path):
        import json

        app_dir = tmp_path / "apps" / "note"
        app_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text(json.dumps({
            "name": "Note", "description": "Note veloci",
            "actions": [
                {"name": "add_note", "description": "Aggiunge", "kind": "storage",
                 "op": "append", "collection": "notes",
                 "params": {"testo": {"type": "string"}}, "required": ["testo"]},
            ],
        }), encoding="utf-8")
        (app_dir / "AGENT.md").write_text("# Note\ncontesto", encoding="utf-8")

        prompt = _builder(tmp_path).build_system_prompt()
        assert "# Jenny Apps" in prompt
        assert "`note_add_note`" in prompt
        assert "apps/note/AGENT.md" in prompt

    def test_apps_section_absent_without_apps(self, tmp_path):
        prompt = _builder(tmp_path).build_system_prompt()
        assert "# Jenny Apps" not in prompt

    def test_broken_app_listed_with_error(self, tmp_path):
        app_dir = tmp_path / "apps" / "rotta"
        app_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text("{nope", encoding="utf-8")
        prompt = _builder(tmp_path).build_system_prompt()
        assert "BROKEN" in prompt
        assert "rotta" in prompt


class TestTheToolContractIsGatedByTool:
    """Il contratto non deve descrivere tool che in questo turno non esistono.

    Per tre versioni ``agent/tool_contract.md`` si rendeva intero, chiuso sul solo
    flag ``orchestrator``, che dice come si lavora e non quali tool ci sono. Chi lo
    pagava era Dream: ``orchestrator=False`` e quattro tool in tutto
    (``build_dream_tools``), quindi si prendeva le sezioni su ``python_exec``, la
    ricerca, i tool web e ``download_file``.

    Non è solo contesto pagato a vuoto. Fra quelle righe c'era *"deleting is the
    one file operation that needs ``python_exec``"*, detta all'unico agente a cui
    ``agent/dream_review.md`` chiede esplicitamente di cancellare e che
    ``python_exec`` non ha: un'istruzione che manda in un vicolo cieco proprio il
    run che deve fare spazio.
    """

    # Il registry di Dream, come lo costruisce ``MemoryStore.build_dream_tools``.
    DREAM_TOOLS = ["read_file", "write_file", "edit_file", "apply_patch"]

    def _contract(self, tmp_path, tools):
        from jenny.utils.prompt_templates import render_template

        return render_template(
            "agent/tool_contract.md",
            orchestrator=False,
            has=ContextBuilder._tool_predicate(tools),
            output_path=str(tmp_path / "output"),
        )

    @pytest.mark.parametrize(
        "absent", ["python_exec", "web_search", "web_fetch", "download_file", "grep", "get_source"]
    )
    def test_a_tool_dream_does_not_have_is_never_named(self, tmp_path, absent):
        assert absent not in self._contract(tmp_path, self.DREAM_TOOLS)

    def test_the_tools_it_does_have_are_still_explained(self, tmp_path):
        """Il gate toglie il rumore, non le istruzioni che servono."""
        contract = self._contract(tmp_path, self.DREAM_TOOLS)

        for present in self.DREAM_TOOLS:
            assert present in contract

    def test_deleting_does_not_send_dream_to_a_tool_it_lacks(self, tmp_path):
        """La riga peggiore del file, e il motivo per cui questo gate esiste."""
        contract = self._contract(tmp_path, self.DREAM_TOOLS)

        assert "needs `python_exec`" not in contract
        assert "cannot delete a file" in contract

    def test_an_unknown_registry_still_gets_everything(self, tmp_path):
        """``None`` vuol dire "non lo so", non "non c'è".

        È la stessa semantica del gate di ``agent/scheduling.md``, e ciò che
        garantisce che i percorsi senza registry per-turno vedano il prompt di
        prima invece di zero sezioni.
        """
        contract = self._contract(tmp_path, None)

        for name in ("python_exec", "web_search", "download_file", "grep"):
            assert name in contract

    def test_the_gate_is_a_callable_so_a_typo_fails_loudly(self):
        """Perché una funzione e non un insieme passato al template.

        Jinja2 valuta falso un ``in`` su una variabile assente, senza sollevare:
        con un insieme, un errore di battitura nel nome della variabile spegnerebbe
        **ogni** sezione del contratto in silenzio. Una callable mancante fa
        fallire il render, e un render fallito lo si vede.
        """
        import jinja2

        from jenny.utils.prompt_templates import render_template

        with pytest.raises(jinja2.UndefinedError):
            render_template("agent/tool_contract.md", orchestrator=False, output_path="/x")


class TestWhichFileAFactBelongsIn:
    """Il routing dei quattro quaderni deve raggiungere un modello.

    Esisteva soltanto nella scheda di aiuto della WebUI
    (``ui/assets/i18n/{it,en}.json``), cioè in un posto che nessun modello legge —
    e da 0.8.0 i template di quei file spediscono zero byte, quindi nel prompt non
    restava niente che dicesse a cosa serve ciascuno: a "ricordati questo" si
    scriveva dove capitava.
    """

    def _contract(self, tools=None):
        from jenny.utils.prompt_templates import render_template

        return render_template(
            "agent/tool_contract.md",
            orchestrator=False,
            has=ContextBuilder._tool_predicate(tools),
            output_path="/x/output",
        )

    @pytest.mark.parametrize(
        "name", ["USER.md", "SOUL.md", "AGENTS.md", "memory/MEMORY.md", "skills/<name>/SKILL.md"]
    )
    def test_every_destination_is_named(self, name):
        assert name in self._contract()

    def test_it_survives_the_tool_gate(self):
        """Sta nella coda non gated di proposito: è Dream che ne ha più bisogno."""
        assert "Which File a Fact Belongs In" in self._contract(
            TestTheToolContractIsGatedByTool.DREAM_TOOLS
        )

    def test_it_does_not_ask_for_facts_the_runtime_already_measures(self):
        """La stessa riga che la scheda della WebUI ha già: ora e posizione no.

        Una copia scritta in ``USER.md`` non la aggiorna nessuno ed è vecchia
        appena salvata, mentre il runtime le misura a ogni turno.
        """
        contract = self._contract()

        assert "timezone" in contract
        assert "stale the moment it is saved" in contract


# ---------------------------------------------------------------------------
# Il nome che l'utente ha dato all'agente
# ---------------------------------------------------------------------------


class TestTheBotNameReachesTheModel:
    """``agents.defaults.bot_name`` si scriveva da Impostazioni e nessuno lo leggeva:
    l'agente rinominato continuava a presentarsi come Jenny (SOUL.md lo dice)."""

    _TOOLS = ["read_file", "write_file", "message", "cron"]

    def _write_name(self, path: Path, name: str) -> None:
        from jenny.config.loader import save_config
        from jenny.config.schema import Config

        config = Config()
        config.agents.defaults.bot_name = name
        save_config(config, path)

    def test_a_renamed_agent_is_told_its_name_live(self, tmp_path, monkeypatch):
        from jenny.runtime.context import get_runtime_context

        path = tmp_path / "config.json"
        monkeypatch.setattr(get_runtime_context(), "config_path", path)
        builder = _builder(tmp_path / "ws")

        self._write_name(path, "Jenny")
        default_prompt = builder.build_system_prompt(available_tools=self._TOOLS)
        assert "## Name" not in default_prompt

        # Stesso builder, nessun riavvio: il nome nuovo vale dal prompt dopo.
        self._write_name(path, "Ada")
        renamed = builder.build_system_prompt(available_tools=self._TOOLS)
        assert "## Name\nYour name is Ada: the user renamed you." in renamed
        assert "introduce yourself as Ada" in renamed
        # Il resto del prompt non cambia: la riga in piu' e' tutta la differenza.
        assert renamed.replace(
            renamed[renamed.index("## Name"):renamed.index("## Environment")], ""
        ) == default_prompt
