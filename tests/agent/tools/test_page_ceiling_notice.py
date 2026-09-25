"""Una scrittura che rende una pagina non iniettabile lo dice. **T9.12.**

Il difetto: ``edit_file`` tornava ``Successfully edited {path}`` e nient'altro, e
``apply_patch`` un delta di righe (``+40/-2``) — cioe' i due tool che lavorano
*per aggiunta* erano i due che non dicevano a cosa avevano portato il file. Il
tetto duro (``_check_write_size``) non e' il problema: quello rifiuta e lo dice.
Quello silenzioso e' la soglia di **iniezione**: oltre, ``_read_project_pages``
salta la pagina **intera** a ogni turno di ogni conversazione del progetto, e il
modello vede solo «N more page(s) are not here».

**Il perche' la forma e' "sulla transizione" e non "sullo stato"** sta nella
misura del task, sulle otto wiki vere del dispositivo (274 pagine, 24/08): 25
sono **gia'** oltre il tetto e 78 oltre i 4.000. Un avviso sullo stato darebbe
venticinque richiami alla prima passata su quel corpo. Da cui il test che vale
piu' di tutti gli altri:
:meth:`TestTheTransition.test_a_page_already_over_says_nothing`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent import context as context_module
from jenny.agent.context import ContextBuilder
from jenny.agent.gardener import GardenerStore
from jenny.agent.tools.apply_patch import ApplyPatchTool
from jenny.agent.tools.filesystem import EditFileTool, WriteFileTool, _page_over_ceiling_note

# La frase che il modello legge. Ne bastano tre parole per distinguere «avvisato»
# da «zitto», e sono quelle della regola SPLIT del prompt del giardiniere.
FIRED = "skipped whole in every conversation"

CEILING = 300


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Un workspace con un progetto, e il tetto abbassato a :data:`CEILING`.

    Il tetto si sposta **sulla costante dell'iniettore**, non su una del tool: e'
    la sola forma in cui questi test provano che il numero e' condiviso invece di
    dichiararlo (v. :class:`TestTheNumberIsTheInjectors`).
    """
    monkeypatch.setattr(context_module, "_PROJECT_PAGES_MAX_CHARS", CEILING)
    root = tmp_path / "workspace"
    project = root / "wikis" / "casa"
    (project / "wiki").mkdir(parents=True)
    (project / "wiki" / "index.md").write_text("# Casa\n\n## Pages\n", encoding="utf-8")
    (root / "memory").mkdir()
    return root, project


def _write_tool(root: Path) -> WriteFileTool:
    return WriteFileTool(workspace=root, allowed_dir=root)


def _edit_tool(root: Path) -> EditFileTool:
    return EditFileTool(workspace=root, allowed_dir=root)


def _patch_tool(root: Path) -> ApplyPatchTool:
    return ApplyPatchTool(workspace=root, allowed_dir=root)


def _page(project: Path, rel: str, body: str) -> Path:
    path = project / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


async def _append_with_edit(root: Path, path: Path, marker: str, added: str) -> str:
    """Un append fatto come lo fa il modello: ``old_text`` = la coda, ``new_text`` = coda + roba."""
    return await _edit_tool(root).execute(
        path=str(path), old_text=marker, new_text=marker + added
    )


async def _append_with_patch(root: Path, path: Path, added: str) -> str:
    return await _patch_tool(root).execute(
        edits=[{"path": str(path), "action": "add", "new_text": added}]
    )


class TestTheTransition:
    """Scatta al passaggio, una volta, e mai sullo stato."""

    async def test_the_append_that_crosses_the_cap_says_it(self, ws) -> None:
        root, project = ws
        page = _page(project, "furgone.md", "# Furgone\n\nCODA")

        result = await _append_with_edit(root, page, "CODA", "x" * 400)

        assert "Successfully edited" in result
        assert FIRED in result
        # Il numero e' quello vero, non «oltre il tetto»: la frase intera si
        # ricostruisce dalla misura del file appena scritto.
        chars = len(page.read_text(encoding="utf-8").strip())
        assert _page_over_ceiling_note("furgone.md", chars, CEILING) in result

    async def test_a_page_already_over_says_nothing(self, ws) -> None:
        """**Il test che decide la forma del fix.** 25 pagine vere su 274 sono
        gia' oltre: un avviso sullo stato le nominerebbe tutte alla prima passata
        e la trasformerebbe da cattura in potatura."""
        root, project = ws
        page = _page(project, "grossa.md", "# Grossa\n\n" + "y" * 500 + "CODA")
        assert len(page.read_text(encoding="utf-8").strip()) > CEILING

        first = await _append_with_edit(root, page, "CODA", "z" * 100)
        second = await _append_with_patch(root, page, "ancora\n")
        terzo = await _write_tool(root).execute(path=str(page), content="w" * 800)

        assert FIRED not in first
        assert FIRED not in second
        assert FIRED not in terzo

    async def test_fires_only_once(self, ws) -> None:
        root, project = ws
        page = _page(project, "treno.md", "# Treno\n\nCODA")

        first = await _append_with_edit(root, page, "CODA", "x" * 400)
        second = await _append_with_edit(root, page, "CODA", "x" * 400)
        terzo = await _append_with_patch(root, page, "una riga in piu'\n")

        assert FIRED in first
        assert FIRED not in second
        assert FIRED not in terzo

    async def test_a_write_that_stays_under_is_silent(self, ws) -> None:
        root, project = ws
        page = _page(project, "corta.md", "# Corta\n\nCODA")

        result = await _append_with_edit(root, page, "CODA", "x" * 50)

        assert "Successfully edited" in result
        assert FIRED not in result

    async def test_a_new_page_already_over_says_so(self, ws) -> None:
        """Non c'era niente da rendere non iniettabile, ma non iniettabile lo e':
        e' una transizione, e sulla popolazione esistente non produce nessun
        richiamo (quelle pagine ci sono gia')."""
        root, project = ws

        result = await _write_tool(root).execute(
            path=str(project / "wiki" / "nuova.md"), content="# Nuova\n\n" + "x" * 400
        )

        assert "Successfully wrote" in result
        assert FIRED in result


    async def test_creation_with_edit_file_says_it_too(self, ws) -> None:
        """``edit_file`` con ``old_text=""`` crea, e ha un suo punto di ritorno
        (``Successfully created``): i punti di uscita di quel tool sono **tre**,
        non due, e un avviso che ne copre due su tre e' un avviso che tace a
        seconda di come il modello ha scritto la stessa pagina."""
        root, project = ws

        result = await _edit_tool(root).execute(
            path=str(project / "wiki" / "creata.md"), old_text="", new_text="x" * 400
        )

        assert "Successfully created" in result
        assert FIRED in result

    async def test_filling_an_empty_page_says_it_too(self, ws) -> None:
        """Il terzo punto di uscita: il file c'e' ma e' vuoto, e ``old_text=""``
        lo riempie. Una pagina appena creata dallo scaffolder sta esattamente
        cosi'."""
        root, project = ws
        page = _page(project, "vuota-poi-piena.md", "\n")

        result = await _edit_tool(root).execute(
            path=str(page), old_text="", new_text="x" * 400
        )

        assert "Successfully edited" in result
        assert FIRED in result


class TestWhereItMustNotFire:
    """La soglia e' delle pagine di un progetto, e di nient'altro."""

    async def test_under_memory_does_not_fire(self, ws) -> None:
        """``memory/`` ha un budget suo e un guard suo
        (``memory_budget.make_write_size_guard``): un avviso sul tetto delle
        pagine, la', sarebbe sbagliato due volte."""
        root, _project = ws

        result = await _write_tool(root).execute(
            path=str(root / "memory" / "MEMORY.md"), content="# Memoria\n\n" + "x" * 900
        )

        assert "Successfully wrote" in result
        assert FIRED not in result

    async def test_the_map_is_not_a_page(self, ws) -> None:
        """``wiki/index.md`` ha un tetto diverso (``_PROJECT_MAP_MAX_CHARS``) e un
        rimedio diverso — si pota, non si taglia in pagine."""
        root, project = ws

        result = await _write_tool(root).execute(
            path=str(project / "wiki" / "index.md"), content="# Casa\n\n" + "x" * 900
        )

        assert "Successfully wrote" in result
        assert FIRED not in result

    async def test_outside_wiki_does_not_fire(self, ws) -> None:
        """Il diario sta in ``raw/journal/``, che non e' sotto ``wiki/``: nessuna
        di quelle righe viene iniettata come pagina."""
        root, project = ws
        (project / "raw" / "journal").mkdir(parents=True)

        result = await _write_tool(root).execute(
            path=str(project / "raw" / "journal" / "20260824.md"),
            content="- 10:00 — " + "x" * 900,
        )

        assert "Successfully wrote" in result
        assert FIRED not in result

    async def test_a_page_written_together_with_its_wiki_does_not_fire(self, ws) -> None:
        """**Il buco, misurato e messo per iscritto.** «Pagina di un progetto» e'
        la definizione che ha il resto del codice (``is_wiki_root``: la cartella
        sopra contiene una ``wiki/``), e l'avviso si calcola **prima** della
        scrittura — che e' anche prima della ``mkdir``. Quindi la primissima
        pagina di una cartella-progetto che ancora non esiste passa zitta. Il
        prezzo e' noto e minuscolo (una wiki nasce dallo scaffolder, non da un
        ``write_file``); il verso opposto sarebbe avvisare per qualunque cartella
        chiamata ``wiki`` nel workspace."""
        root, _project = ws

        result = await _write_tool(root).execute(
            path=str(root / "wikis" / "nuovo" / "wiki" / "prima.md"), content="x" * 900
        )

        assert "Successfully wrote" in result
        assert FIRED not in result

    async def test_a_dry_run_has_rendered_nothing_non_injectable(self, ws) -> None:
        """Il ``dry_run`` di ``apply_patch`` non scrive, quindi non c'e' nessuna
        transizione da annunciare: l'avviso parla di quel che il file **e'
        diventato**, e qui non e' diventato niente."""
        root, project = ws
        page = _page(project, "prova.md", "# Prova\n\nCODA")

        result = await _patch_tool(root).execute(
            edits=[{"path": str(page), "action": "add", "new_text": "x" * 400}],
            dry_run=True,
        )

        assert "dry-run succeeded" in result
        assert FIRED not in result
        assert page.read_text(encoding="utf-8") == "# Prova\n\nCODA"

    async def test_a_file_that_is_not_markdown_is_not_a_page(self, ws) -> None:
        """L'iniettore cammina ``rglob("*.md")``: un ``.json`` sotto ``wiki/`` non
        entra in nessun turno, quindi non ha questo tetto."""
        root, project = ws

        result = await _write_tool(root).execute(
            path=str(project / "wiki" / "dati.json"), content='{"x": "' + "y" * 900 + '"}'
        )

        assert "Successfully wrote" in result
        assert FIRED not in result

    async def test_a_summary_is_not_a_page(self, ws) -> None:
        """``summaries/`` sta dentro ``wiki/`` ma fuori dalle pagine iniettate
        (``is_wiki_page_rel``), quindi non ha questo tetto."""
        root, project = ws

        result = await _write_tool(root).execute(
            path=str(project / "wiki" / "summaries" / "doc.md"), content="x" * 900
        )

        assert "Successfully wrote" in result
        assert FIRED not in result


class TestTheNumberIsTheInjectors:
    """Non una terza copia del 6.000: la costante dell'iniettore, letta."""

    async def test_raising_the_injector_cap_silences_the_tool(
        self, ws, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, project = ws
        page = _page(project, "identica.md", "# Identica\n\nCODA")
        monkeypatch.setattr(context_module, "_PROJECT_PAGES_MAX_CHARS", 100_000)

        result = await _append_with_edit(root, page, "CODA", "x" * 400)

        assert FIRED not in result

    async def test_with_the_real_cap_it_fires_at_six_thousand(self, tmp_path: Path) -> None:
        """Senza monkeypatch: la soglia vera e' quella, e il conto e' in
        caratteri del testo *spogliato*."""
        root = tmp_path / "workspace"
        project = root / "wikis" / "casa"
        (project / "wiki").mkdir(parents=True)
        page = _page(project, "lunga.md", "# Lunga\n\n" + "x" * 5_900 + "CODA")

        under = await _append_with_edit(root, page, "CODA", "y" * 50)
        above = await _append_with_edit(root, page, "CODA", "y" * 200)

        assert FIRED not in under
        assert FIRED in above
        assert f"{len(page.read_text(encoding='utf-8').strip()):,}" in above


class TestAllSayTheSameThing:
    """I tre scrittori, l'iniettore e l'inventario della passata: un solo verdetto."""

    async def test_edit_file_and_apply_patch_say_the_same_sentence(self, ws) -> None:
        root, project = ws
        one = _page(project, "uno.md", "# Uno\n\nCODA")
        due = _page(project, "due.md", "# Due\n\nCODA")

        con_edit = await _append_with_edit(root, one, "CODA", "x" * 400)
        con_patch = await _append_with_patch(root, due, "x" * 400)

        # La stessa frase, ognuna col conto del file che quel tool ha scritto
        # davvero. **Non byte a byte fra le due**: i due append non producono lo
        # stesso file — ``apply_patch`` interpone un ``\n`` fra la coda e la roba
        # nuova (``_append_text``), quindi la sua pagina misura un carattere in
        # piu'. Cioe' ognuno misura il proprio esito, che e' il punto.
        for result, rel, page in ((con_edit, "uno.md", one), (con_patch, "due.md", due)):
            chars = len(page.read_text(encoding="utf-8").strip())
            assert _page_over_ceiling_note(rel, chars, CEILING) in result

    async def test_the_warned_page_is_the_one_the_injector_skips(self, ws) -> None:
        """La prova che l'avviso parla del guasto vero: la pagina di cui ha
        parlato **non arriva piu'** nel blocco di progetto, e quella di cui ha
        taciuto ci arriva."""
        root, project = ws
        grown = _page(project, "cresciuta.md", "# Cresciuta\n\nCODA")
        remaining = _page(project, "rimasta.md", "# Rimasta\n\nbreve")

        notice = await _append_with_edit(root, grown, "CODA", "x" * 400)
        injected = ContextBuilder(root)._read_project_pages(project).text

        assert FIRED in notice
        assert "`cresciuta.md`" not in injected
        assert "xxxx" not in injected  # ne' intera ne' troncata
        assert "`rimasta.md`" in injected
        assert remaining.read_text(encoding="utf-8").strip() in injected

    async def test_the_gardener_inventory_says_the_same_number(self, ws) -> None:
        """L'avviso in scrittura e l'annotazione della passata dopo (T3.14)
        contano con **la stessa regola** (``wiki_paths.page_chars``): se
        divergessero, uno dei due parlerebbe di pagine che entrano."""
        root, project = ws
        page = _page(project, "misura.md", "# Misura\n\nCODA")

        notice = await _append_with_edit(root, page, "CODA", "x" * 400)
        inventory = GardenerStore(project, root).build_inventory()

        chars = len(page.read_text(encoding="utf-8").strip())
        assert f"{chars:,}" in notice
        assert f"over the ceiling: {chars} characters" in inventory

    async def test_trailing_empty_lines_do_not_count(self, ws) -> None:
        """Il tetto guarda il testo **spogliato** ai bordi, come l'iniettore e
        come il lint: 200 caratteri e centocinquanta righe vuote sono una pagina
        da 200, e l'iniettore la inietta."""
        root, project = ws
        blank_tail = "x" * 200 + "\n" * 150
        assert len(blank_tail) > CEILING >= len(blank_tail.strip())

        result = await _write_tool(root).execute(
            path=str(project / "wiki" / "vuota.md"), content=blank_tail
        )
        injected = ContextBuilder(root)._read_project_pages(project).text

        assert FIRED not in result
        assert "`vuota.md`" in injected

    async def test_a_crlf_file_is_measured_as_the_injector_reads_it(self, ws) -> None:
        """**La misura e' quella di ``read_text``**, che traduce ``\\r\\n`` in
        ``\\n`` e quindi accorcia. Questa pagina pesa 330 byte e 219 caratteri:
        contarla coi ``\\r`` dentro la direbbe non iniettabile mentre l'iniettore
        la inietta, che e' il modo di avvisare su una pagina che entrava."""
        root, project = ws
        crlf = "a\r\n" * 110
        assert len(crlf.strip()) > CEILING >= len(crlf.replace("\r\n", "\n").strip())

        result = await _write_tool(root).execute(
            path=str(project / "wiki" / "crlf.md"), content=crlf
        )
        injected = ContextBuilder(root)._read_project_pages(project).text

        assert FIRED not in result
        assert "`crlf.md`" in injected
