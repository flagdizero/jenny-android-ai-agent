"""La passata del giardiniere: la cassetta chiusa, il prompt, il cursore.

Passo **T4.2** di ``roadmap/taccuino-passi.md``.

Il gruppo che conta è ``TestTheToolbox``, e la ragione va scritta: **il
confinamento di un turno interno è il registry, non lo scope.** Un turno interno
gira su ``INTERNAL_CHANNEL``, e ``WorkspaceScopeResolver.for_turn`` per ogni
canale che non sia la WebUI restituisce lo scope *di default* — l'intera
installazione scrivibile. Quel che tiene il giardiniere dentro ``wiki/`` è la
cassetta dei tool, e nient'altro. Da cui due test e non uno: la allowlist di
scrittura, **e l'elenco esatto dei tool** — perché uno ``spawn_subagent`` nella
cassetta sarebbe una scrittura ovunque per interposta persona, e non farebbe
cadere nessun test sulla allowlist.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.gardener import (
    MAP_TARGET_CHARS,
    GardenerStore,
    page_ceiling,
    run_gardener,
)
from jenny.agent.gardener_state import (
    MAX_DELTA_LINES,
    GardenerState,
    read_journal_delta,
    read_state,
)
from jenny.agent.tools.file_state import FileStates
from jenny.security.workspace_access import (
    bind_workspace_scope,
    default_workspace_scope,
    reset_workspace_scope,
)

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

_DAY = date(2026, 8, 23)


def _project(workspace: Path, name: str = "viaggio", *, journal: bool = True) -> Path:
    root = workspace / "wikis" / name
    (root / "wiki").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# viaggio\n\n## Pages\n\n*(none yet)*\n", "utf-8")
    (root / "AGENTS.md").write_text("---\nid: abc123abc123\n---\n\n# viaggio\n", "utf-8")
    if journal:
        d = root / "raw" / "journal"
        d.mkdir(parents=True)
        (d / "20260822.md").write_text(
            "# 2026-08-22\n\n- 09:00 — il furgone ha le gomme da cambiare\n"
            "- 09:10 — si parte il 14\n",
            "utf-8",
        )
    return root


def _snapshot(root: Path) -> dict[Path, bytes]:
    """Percorsi **e contenuti** sotto *root*: l'istantanea con cui si prova un rifiuto.

    Il set dei soli percorsi non vede una sovrascrittura, e i file che una
    passata del giardiniere non deve toccare per nessuna ragione — il diario,
    ``AGENTS.md`` — esistono già quando il test comincia. Da cui i byte.
    """
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _fixture(workspace: Path) -> None:
    """Il banco dei test sulla allowlist: due progetti e la memoria personale.

    ``memory/MEMORY.md`` esiste con del testo dentro, e non è un dettaglio: un
    file che non esiste prova solo che non è stato **creato**, ed è la metà più
    facile del divieto.
    """
    _project(workspace)
    _project(workspace, "altro", journal=False)
    (workspace / "memory").mkdir(exist_ok=True)
    (workspace / "memory" / "MEMORY.md").write_text("- lei odia i cavoli\n", "utf-8")


#: I percorsi che una passata non deve poter scrivere, con la ragione.
_OFF_LIMITS = [
    ("wikis/viaggio/raw/journal/20260822.md", "il diario è l'input, ed è append-only"),
    ("wikis/viaggio/raw/research/nota.md", "raw/ è verbatim per definizione"),
    ("wikis/viaggio/AGENTS.md", "le premesse le cambia l'utente"),
    ("wikis/viaggio/log/20260823.md", "il log lo scrive il codice"),
    ("wikis/viaggio/audit/nota.md", "audit/ è il canale dell'umano"),
    ("wikis/altro/wiki/pagina.md", "un altro progetto"),
    ("memory/MEMORY.md", "la memoria personale"),
]

#: Il sottoinsieme che ``_fixture`` crea davvero, con una stringa che contiene:
#: sono i soli su cui ``edit_file`` può essere provato per la ragione giusta
#: (su un file inesistente cadrebbe per «non lo trovo», non per la allowlist).
_OFF_LIMITS_AND_EXISTING = [
    ("wikis/viaggio/raw/journal/20260822.md", "gomme da cambiare",
     "il diario è l'input, ed è append-only"),
    ("wikis/viaggio/AGENTS.md", "abc123abc123", "le premesse le cambia l'utente"),
    ("wikis/altro/wiki/index.md", "## Pages", "la mappa di un altro progetto"),
    ("memory/MEMORY.md", "cavoli", "la memoria personale"),
]


def _store(workspace: Path, name: str = "viaggio") -> GardenerStore:
    store = GardenerStore.for_project(workspace, name)
    assert store is not None
    store._today = lambda: _DAY  # type: ignore[method-assign]
    return store


class _FakeAgent:
    """Agente minimale: registra la chiamata e restituisce un esito pilotato."""

    def __init__(self, sessions_dir: Path, *, stop_reason: str = "completed") -> None:
        self.context = SimpleNamespace(memory=None, timezone="Europe/Rome")
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)
        self.calls: list[dict] = []
        self._stop_reason = stop_reason
        self.evicted: list[str] = []
        self.snapshots: list[str] = []
        self.reply = "NOTHING TO FLAG"

    async def take_snapshot(self, trigger: str) -> bool:
        self.snapshots.append(trigger)
        return True

    async def process_direct(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(
            metadata={"_stop_reason": self._stop_reason}, usage={}, content=self.reply,
        )

    def evict_pruned_sessions(self, keys) -> None:
        self.evicted.extend(keys)


class _ExplodingAgent(_FakeAgent):
    async def process_direct(self, prompt: str, **kwargs):
        raise RuntimeError("provider is down")


class _WritingAgent(_FakeAgent):
    """Un agente che lascia davvero una pagina su disco.

    Serve al solo test che deve distinguere «le pagine ci sono» da «il cursore è
    stato registrato»: con la cassetta finta di ``_with_states`` le scritture sono
    solo conteggi, e su un conteggio non si può dire che il lavoro è su disco.
    """

    def __init__(self, sessions_dir: Path, page: Path) -> None:
        super().__init__(sessions_dir)
        self._page = page

    async def process_direct(self, prompt: str, **kwargs):
        self._page.parent.mkdir(parents=True, exist_ok=True)
        self._page.write_text("# tappe\n", "utf-8")
        return await super().process_direct(prompt, **kwargs)


def _stale_sessions(sessions_dir: Path, *, count: int = 11) -> Path:
    """Undici sessioni finte del giardiniere; restituisce la più vecchia.

    ``prune_internal_sessions`` tiene le dieci più recenti, quindi undici file
    sono il minimo perché una potatura *possa* avvenire: senza di loro un test
    sulla potatura passa anche se ``_prune_sessions`` non viene chiamato mai.
    Le mtime si scrivono a mano perché l'ordinamento è per mtime e undici file
    creati di fila stanno nello stesso secondo.
    """
    import os

    oldest: Path | None = None
    for i in range(count):
        path = sessions_dir / f"gardener_viaggio-old{i:02d}.jsonl"
        path.write_text("{}\n", "utf-8")
        os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
        if oldest is None:
            oldest = path
    assert oldest is not None
    return oldest


def _states(*, attempted: int, ok: int) -> FileStates:
    states = FileStates()
    for _ in range(attempted):
        states.record_write_attempt()
    for _ in range(ok):
        states.record_write(Path("/tmp/whatever"))
    return states


def _with_states(store: GardenerStore, states: FileStates) -> GardenerStore:
    # ``**_kw`` e non una firma vuota: ``run_gardener`` passa ``write_guard`` (il
    # gancio che cede il passo all'utente), e una lambda a zero argomenti
    # trasformerebbe ogni test di questo file in un ``TypeError``.
    store.build_tools = lambda **_kw: SimpleNamespace(file_states=states)  # type: ignore[method-assign]
    return store


# ── Il bersaglio ─────────────────────────────────────────────────────────────


class TestTheTarget:
    def test_a_folder_that_is_not_a_project_has_no_gardener(self, tmp_path):
        (tmp_path / "wikis" / "appunti").mkdir(parents=True)
        assert GardenerStore.for_project(tmp_path, "appunti") is None

    def test_a_missing_folder_is_not_an_error(self, tmp_path):
        assert GardenerStore.for_project(tmp_path, "mai-esistito") is None

    @pytest.mark.parametrize("name", ["..", "../..", "../altro", "../segreto"])
    def test_a_name_cannot_climb_out_of_the_projects_folder(self, tmp_path, name):
        """Il nome arriva da una chiave di sessione o da un argomento di comando,
        cioè in ultima analisi da un client: un ``..`` non deve poter portare la
        passata fuori da ``wikis/``.

        **``../segreto`` è il solo parametro che prova la guardia** (aggiunto da
        T4.12, trovato per mutazione il 23/08). I primi tre risalgono verso
        cartelle che non sono wiki, quindi a rifiutarli basta il filtro più
        rozzo *dopo* — ``is_wiki_root`` — e il controllo su ``..`` si poteva
        togliere lasciando il test verde. Qui fuori da ``wikis/`` c'è una wiki
        vera: senza la guardia, ``for_project`` tornerebbe uno store con la
        radice là dentro, e il giardiniere scriverebbe fuori dai progetti.
        """
        _project(tmp_path)
        (tmp_path / "segreto" / "wiki").mkdir(parents=True)
        assert GardenerStore.for_project(tmp_path, name) is None

    def test_a_workspace_reachable_under_two_names_still_gives_one_path(self, tmp_path):
        """**Il difetto del 23/08, sul telefono.** Su Android la dir dati e'
        raggiungibile come ``/data/user/0/<pkg>`` e ``Path.resolve()`` la riscrive
        in ``/data/data/<pkg>``. Il progetto arrivava risolto e il workspace no,
        quindi ``relative_to`` alzava ``ValueError`` e il prompt diceva
        ``zz-t4/wiki/`` invece di ``wikis/zz-t4/wiki/``: il modello ha scritto
        quattro pagine perfette e sono state **rifiutate tutte**.

        Qui la doppia via e' un symlink, che e' la stessa cosa vista da macOS.
        """
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        _project(real, "zz-t4")

        store = GardenerStore.for_project(link, "zz-t4")

        assert store is not None
        assert store.rel_root == "wikis/zz-t4"

    def test_paths_in_the_prompt_are_relative_to_the_workspace(self, tmp_path):
        """Non è estetica: la base dei percorsi relativi è ``project_path`` dello
        scope legato, che per un turno interno è la radice dell'installazione. E
        un assoluto verrebbe rifiutato comunque — su Android la dir dati ha due
        nomi e la allowlist ne conosce uno (la trappola già pagata sul telefono)."""
        _project(tmp_path)
        assert _store(tmp_path).rel_root == "wikis/viaggio"


# ── La cassetta, cioè il confinamento ────────────────────────────────────────


class TestTheToolbox:
    @pytest.fixture
    def bound(self, tmp_path):
        """Lega lo scope di **default**, come in produzione.

        È il punto del test: un turno interno non ha lo scope del progetto, ha
        quello dell'installazione. Legarlo qui vuol dire provare la cassetta
        nelle condizioni in cui gira davvero, invece di provarla in una
        situazione più stretta di quella vera.
        """
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        yield
        reset_workspace_scope(token)

    def test_the_toolbox_holds_exactly_these_tools(self, tmp_path, bound):
        """**Una porta nella cassetta è una via d'uscita.** ``spawn_subagent``,
        ``python_exec``, ``message``: uno solo di questi e la superficie chiusa
        non esiste più, per interposta persona — e nessun test sulla allowlist
        di scrittura se ne accorgerebbe. Da cui l'elenco, per nome.

        ``journal_append`` è entrato in T6.3, ed è l'unica scrittura fuori da
        ``wiki/`` che si concede. È ammesso per una ragione precisa e non per
        comodità: **non può violare la regola che protegge**. Appende in coda per
        costruzione — un solo file possibile, nessun modo di riscrivere una riga —
        quindi non tocca la fonte da cui la passata sta promuovendo. Un tool che
        potesse riscrivere il diario non entrerebbe qui nemmeno se servisse.
        """
        _project(tmp_path)
        tools = _store(tmp_path).build_tools()

        assert set(tools.tool_names) == {
            "read_file", "list_dir", "find_files", "grep",
            "write_file", "edit_file", "apply_patch",
            "journal_append",
        }

    @pytest.mark.parametrize("door", ["spawn_subagent", "python_exec", "message", "cron"])
    def test_the_toolbox_has_no_door(self, tmp_path, bound, door):
        """Il test sopra cadrebbe comunque, ma dice «l'insieme è cambiato» e non
        «è entrata una via d'uscita». Questo nomina le porte, così il giorno che
        una compare il messaggio dice cosa è successo."""
        _project(tmp_path)

        assert door not in set(_store(tmp_path).build_tools().tool_names)

    async def test_it_writes_a_page(self, tmp_path, bound):
        _project(tmp_path)
        tools = _store(tmp_path).build_tools()

        out = await tools.get("write_file").execute(
            path="wikis/viaggio/wiki/furgone.md", content="---\nstate: open\n---\n\n# Furgone\n"
        )

        assert (tmp_path / "wikis" / "viaggio" / "wiki" / "furgone.md").is_file(), out

    @pytest.mark.parametrize("path,why", _OFF_LIMITS)
    async def test_everything_else_is_refused(self, tmp_path, bound, path, why):
        """La allowlist di scrittura, vista da ``write_file``.

        **Il confronto è sui contenuti, non sui percorsi** (T8.3). Con
        l'istantanea dei soli percorsi, i due parametri che valgono di più —
        il diario, che è la fonte append-only da cui la passata sta promuovendo,
        e ``AGENTS.md``, che sono le premesse dell'utente — non provavano niente:
        li crea la fixture, quindi sovrascriverli non fa comparire né sparire
        nessun percorso. Misurato: allargando ``allowed_dir`` da ``wiki/`` alla
        radice del progetto cadevano cinque parametri su sette e quei due
        passavano, con il diario troncato a ``"x"``.
        """
        _fixture(tmp_path)
        tools = _store(tmp_path).build_tools()
        before = _snapshot(tmp_path)

        out = await tools.get("write_file").execute(path=path, content="x")

        assert _snapshot(tmp_path) == before, (
            f"{path} è stato scritto, e non doveva ({why}): {out}"
        )

    @pytest.mark.parametrize("path,old,why", _OFF_LIMITS_AND_EXISTING)
    async def test_an_existing_file_outside_the_pages_is_not_edited(
        self, tmp_path, bound, path, old, why
    ):
        """``edit_file`` obbedisce alla stessa allowlist di ``write_file``.

        Il test sopra prova un tool su tre. Gli altri due sono quelli che fanno
        il danno peggiore, perché lavorano su un file che **esiste già**: non
        creano un percorso nuovo, riscrivono un contenuto. Verificato a mano che
        oggi sono rifiutati; qui la regressione fa cadere qualcosa. La mutazione
        che questo test uccide, e che senza di lui passava tutti i test di
        ``tests/agent``: registrare ``EditFileTool`` con ``allowed_dir=root``
        invece di ``pages``, lasciando gli altri due al loro posto.
        """
        _fixture(tmp_path)
        tools = _store(tmp_path).build_tools()
        before = _snapshot(tmp_path)

        out = await tools.get("edit_file").execute(
            path=path, old_text=old, new_text="RISCRITTO"
        )

        assert _snapshot(tmp_path) == before, (
            f"{path} è stato riscritto da edit_file, e non doveva ({why}): {out}"
        )

    @pytest.mark.parametrize("path,old,why", _OFF_LIMITS_AND_EXISTING)
    async def test_an_existing_file_outside_the_pages_is_not_patched(
        self, tmp_path, bound, path, old, why
    ):
        """Come sopra per ``apply_patch``, che è il terzo tool di scrittura.

        ``action="add"`` su un file che esiste **appende**, quindi è il caso
        peggiore in assoluto per un'istantanea dei percorsi: non c'è nemmeno una
        stringa da far combaciare, e il file cresce senza che nessun percorso si
        muova.
        """
        _fixture(tmp_path)
        tools = _store(tmp_path).build_tools()
        before = _snapshot(tmp_path)

        out = await tools.get("apply_patch").execute(
            edits=[{"path": path, "action": "add", "new_text": "RISCRITTO\n"}]
        )

        assert _snapshot(tmp_path) == before, (
            f"{path} è stato riscritto da apply_patch, e non doveva ({why}): {out}"
        )

    async def test_it_reads_inside_the_project_and_not_outside(self, tmp_path, bound):
        _project(tmp_path)
        _project(tmp_path, "altro", journal=False)
        read = _store(tmp_path).build_tools().get("read_file")

        mine = await read.execute(path="wikis/viaggio/raw/journal/20260822.md")
        theirs = await read.execute(path="wikis/altro/wiki/index.md")

        assert "gomme da cambiare" in mine
        assert "# viaggio" not in theirs

    @pytest.mark.parametrize("tool,call", [
        ("read_file", {"path": ".jenny/media/segreto.md"}),
        ("list_dir", {"path": ".jenny/media"}),
        ("grep", {"pattern": "personale", "path": ".jenny/media"}),
        ("find_files", {"pattern": "*.md", "path": ".jenny/media"}),
    ])
    async def test_the_shared_media_dir_is_not_inside_the_project(
        self, tmp_path, tool, call
    ):
        """La cartella dei media **non** è dentro il progetto, e fino al
        24/08/2026 la cassetta la leggeva.

        ``_FsTool._resolve_read`` metteva ``<workspace>/.jenny/media`` fra le
        radici ammesse per ogni tool di lettura, e quella cartella la condividono
        *tutte* le conversazioni. Non è una via d'uscita — la scrittura resta
        rifiutata — ma è il verso rovesciato di T7.8: un artefatto personale di
        un'altra conversazione a portata della passata che scrive le pagine di
        questo progetto.

        Il test gira nel workspace **configurato** e non sotto ``tmp_path``, e la
        differenza è tutto: ``get_media_dir()`` legge il workspace vero, quindi
        con un progetto sotto ``tmp_path`` il percorso relativo cadeva fuori per
        una ragione accidentale (due radici diverse) e il test passava anche col
        difetto in piedi. Misurato: nella forma fedele, tre tool su quattro
        aprivano quella cartella con la forma di percorso che il prompt stesso
        insegna.
        """
        import shutil

        from jenny.config.paths import get_media_dir, get_workspace_path

        workspace = get_workspace_path()
        root = workspace / "wikis" / "media-probe"
        secret = get_media_dir() / "segreto.md"
        (root / "wiki").mkdir(parents=True, exist_ok=True)
        (root / "wiki" / "index.md").write_text("# probe\n", "utf-8")
        secret.write_text("appunto personale\n", "utf-8")
        token = bind_workspace_scope(default_workspace_scope(workspace, True))
        try:
            store = GardenerStore.for_project(workspace, "media-probe")
            assert store is not None
            out = await store.build_tools().get(tool).execute(**call)
        finally:
            reset_workspace_scope(token)
            # Il workspace configurato è di **sessione**: quel che si scrive qui
            # lo trovano i test dopo.
            shutil.rmtree(root, ignore_errors=True)
            secret.unlink(missing_ok=True)

        # Il rifiuto ripete il percorso chiesto, quindi il nome del file compare
        # nell'errore: quel che non deve comparire è il *contenuto*, e quel che
        # deve comparire è il rifiuto — che per ``list_dir`` e ``find_files`` è
        # l'unica prova possibile.
        assert "appunto personale" not in out, out
        assert "outside allowed directory" in out, out

    async def test_a_page_cannot_be_removed_and_can_be_emptied(self, tmp_path, bound):
        """Quel che la cassetta garantisce, e quel che no.

        «Non cancellare mai una pagina» il prompt lo chiama assoluto, e la
        cassetta ne tiene **metà**: nessuna delle tre porte di scrittura sa
        rimuovere un file (``apply_patch`` conosce ``add`` e ``replace``, e
        l'``unlink`` che ha dentro è il suo rollback), quindi una pagina non può
        sparire. L'altra metà — il *contenuto* — resta del prompt: una scrittura
        vuota su una pagina che esiste riesce, e la rete è l'istantanea
        ``pre_gardener``.

        È un test di caratterizzazione e non la prova di una correzione: qui non
        è cambiato niente, e serve a fissare esattamente la frase che la
        docstring di ``build_tools`` adesso dice — non una più forte.
        """
        root = _project(tmp_path)
        page = root / "wiki" / "furgone.md"
        page.write_text("# Furgone\n\ngomme nuove il 14\n", "utf-8")
        tools = _store(tmp_path).build_tools()

        removed = await tools.get("apply_patch").execute(
            edits=[{"path": "wikis/viaggio/wiki/furgone.md", "action": "delete"}]
        )
        emptied = await tools.get("write_file").execute(
            path="wikis/viaggio/wiki/furgone.md", content=""
        )

        assert "unknown action" in removed and page.is_file()
        assert "Error" not in emptied, emptied
        assert page.read_text(encoding="utf-8") == ""


# ── Il prompt ────────────────────────────────────────────────────────────────


class TestThePrompt:
    def _prompt(self, tmp_path) -> str:
        _project(tmp_path)
        store = _store(tmp_path)
        return store.build_prompt(store.read_delta())

    def test_it_carries_the_journal_lines(self, tmp_path):
        prompt = self._prompt(tmp_path)
        assert "gomme da cambiare" in prompt
        assert "raw/journal/20260822.md" in prompt

    def test_it_names_the_only_writable_place(self, tmp_path):
        assert "`wikis/viaggio/wiki/`" in self._prompt(tmp_path)

    def test_it_never_hands_out_an_absolute_path(self, tmp_path):
        """Un assoluto nel prompt è un divieto travestito da istruzione: la
        allowlist tiene la forma *risolta*, e su Android le due divergono."""
        assert str(tmp_path) not in self._prompt(tmp_path)

    def test_the_map_is_fenced(self, tmp_path):
        """Le intestazioni di una pagina sbucherebbero nella struttura del
        prompt; e quel che sta in un file dell'utente è dato, non istruzione.
        Quattro backtick perché una pagina può contenere un blocco di codice."""
        assert "````markdown" in self._prompt(tmp_path)

    @pytest.mark.parametrize("rule", [
        "state: open",          # una riga promossa non nasce decisa
        "source:",              # la pista dalla pagina alla frase
        "Add and promote",      # non riscrivere
        "Never delete a page",
        "Follow the structure you find",
        "nothing to do",
    ])
    def test_the_rules_are_all_there(self, tmp_path, rule):
        assert rule in self._prompt(tmp_path)

    def test_a_page_that_outgrew_the_budget_is_split(self, tmp_path):
        """T3.3. «Aggiungi, non riscrivere» è **la ragione per cui** le pagine
        crescono, e una pagina oltre il budget del blocco non entra nel prompt
        affatto: viene saltata intera a ogni turno, e la selezione non guarda quel
        che l'utente chiede — quindi nessuna sua domanda la richiama. Senza questa
        uscita la regola argomenta contro l'unica manovra che rimette la pagina in
        circolo.

        Come per l'eccezione della mappa, la posizione è parte del test: il
        ritaglio va letto **dove sta la regola che ritaglia**. La finestra è il
        punto elenco e non un numero di caratteri: la prima stesura contava 900
        caratteri, e una frase in più dentro la stessa regola faceva cadere il
        test senza che niente si fosse spostato.
        """
        prompt = self._prompt(tmp_path)
        i_rule = prompt.index("Add and promote")
        i_split = prompt.index("is SPLIT")
        bullet = prompt[i_split:].split("\n- **")[0]

        assert i_split > i_rule
        assert "splitting is a promotion" in prompt
        # E non «riassumila»: la pagina non si accorcia, si taglia in pagine.
        assert "moved word for word" in bullet

    def test_the_selection_rule_the_split_argues_from_is_the_map_order(self, tmp_path):
        """**Una frase stale in un prompt è peggio di una mancante**, perché il
        modello la legge e ci ragiona sopra.

        La regola SPLIT diceva «l'ordine in cui le pagine sono offerte è
        alfabetico», e T3.7 quel criterio l'ha sostituito: l'ordine è quello di
        **prima apparizione** del wikilink in ``wiki/index.md``, con l'alfabeto
        come solo ripiego per le pagine che la mappa non nomina. La conclusione
        regge — una pagina oltre il tetto è irraggiungibile comunque — il
        meccanismo no, e il meccanismo è la parte su cui questa passata *può*
        agire: la mappa la mantiene lei.

        Questo test legge **prosa di template**, e va detto: non esegue niente. La
        parte che vale è l'assenza di «alphabetical», che è la parola con cui la
        frase vecchia tornerebbe.
        """
        prompt = self._prompt(tmp_path)

        assert "alphabetical" not in prompt
        assert "the order the map names them" in prompt

    def test_the_source_value_is_not_a_path_to_resolve(self, tmp_path):
        """L'unico percorso del prompt che **non** comincia con la radice del
        progetto, in una sezione che dice che tutti cominciano così.

        È un valore di frontmatter e non un percorso da risolvere, ma non detto
        invita a provare ad aprire ``raw/journal/…`` e a incassare un rifiuto.
        Prosa di template anche questo: non esegue niente.
        """
        prompt = self._prompt(tmp_path)

        assert "source: raw/journal/20260822.md" in prompt
        assert "not a path you resolve" in prompt

    def test_splitting_is_carved_out_of_never_delete_too(self, tmp_path):
        """Le due regole si incontrano: uno split fa **uscire una sezione** da una
        pagina, e «never delete a section of one» è assoluto. Senza il ritaglio
        detto lì, il modello ha davanti un'istruzione e il suo divieto."""
        prompt = self._prompt(tmp_path)
        i_never = prompt.index("Never delete a page")

        assert "a split is the single exception" in prompt[i_never:i_never + 300]

    def test_the_map_exception_is_stated_where_the_rule_is(self, tmp_path):
        """La mappa è in parte derivata, quindi «non riscrivere» le va ritagliata
        addosso — e il ritaglio va scritto **dove sta la regola**, che è la
        lezione della posizione pagata tre volte in un giorno su questo ramo."""
        prompt = self._prompt(tmp_path)
        i_rule = prompt.index("Add and promote")
        i_carve = prompt.index('The map is the exception')
        assert i_carve > i_rule
        assert "amend" in prompt[i_carve:i_carve + 700]

    def test_an_empty_project_says_so_instead_of_listing_nothing(self, tmp_path):
        """Una rubrica che tace si legge come «non c'è niente da sapere», che è
        il silenzio che T3 ha già pagato una volta su un inventario."""
        _project(tmp_path)
        store = _store(tmp_path)
        assert "no pages yet" in store.build_prompt(store.read_delta())

    def test_the_pages_that_exist_are_listed(self, tmp_path):
        root = _project(tmp_path)
        (root / "wiki" / "furgone.md").write_text("# Furgone\n", "utf-8")
        store = _store(tmp_path)

        prompt = store.build_prompt(store.read_delta())

        assert "`furgone.md` — Furgone" in prompt
        assert "index.md`" not in prompt.split("## Pages that already exist")[1]

    def test_a_capped_delta_says_what_it_left(self, tmp_path):
        """Mai troncare zitti — e in più: il modello non deve mettersi a cercare
        le righe che mancano, quindi il prompt gli dice che arriveranno."""
        root = _project(tmp_path)
        (root / "raw" / "journal" / "20260823.md").write_text(
            "# 2026-08-23\n\n" + "".join(f"- 09:00 — fatto {i}\n" for i in range(20)), "utf-8"
        )
        store = GardenerStore(root, tmp_path, max_delta_lines=3)

        prompt = store.build_prompt(store.read_delta())

        assert "left for the next pass" in prompt
        assert "19 further journal lines" in prompt


# ── Le pagine troppo lunghe ──────────────────────────────────────────────────
#
# **Il secondo numero dell'audit del 23/08: 23 pagine su 188 oltre il tetto** dei
# 6000 caratteri con cui una pagina entra nel blocco di progetto — 9 in ``main``,
# 9 in ``allergie``, 5 in ``patreon-creator``, le altre cinque wiki pulite
# (mediana 3.216, massimo 16.384). Oltre quel tetto la pagina non entra
# **affatto**: non tronca, si salta intera, a ogni turno di ogni conversazione, e
# l'ordine è alfabetico — nessuna domanda dell'utente può richiamarla.
#
# T3.3 ha insegnato al prompt la regola (una pagina che sfonda si **taglia**), e
# l'elenco delle pagine diceva percorso e titolo: la passata aveva la regola e non
# i suoi soggetti, cioè su ``main`` non poteva sapere quali nove su cinquantadue
# senza aprirle tutte. E nessun segnale in scrittura le nominerà mai — quelle 23
# le hanno scritte le conversazioni, non il giardiniere. Da cui l'annotazione.


def _page_of(root: Path, name: str, chars: int) -> Path:
    """Una pagina di *chars* caratteri esatti, misurati come li misura il tetto."""
    head = "# Titolo\n\n"
    page = root / "wiki" / name
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(head + "x" * (chars - len(head)), "utf-8")
    assert len(page.read_text(encoding="utf-8").strip()) == chars
    return page


class TestThePagesThatAreTooLong:
    """L'elenco delle pagine dice **quali** sfondano il tetto di iniezione."""

    def test_a_page_over_the_ceiling_is_named_as_such(self, tmp_path):
        """La regola SPLIT senza i suoi soggetti è una regola che non si applica:
        la passata non può aprire cinquantadue pagine per scoprire quali nove."""
        root = _project(tmp_path)
        ceiling = page_ceiling()
        _page_of(root, "nakasendo.md", ceiling + 384)
        store = _store(tmp_path)

        inventory = store.build_prompt(store.read_delta()).split(
            "## Pages that already exist"
        )[1]

        assert f"`nakasendo.md` — Titolo — **over the ceiling: {ceiling + 384} characters" \
            in inventory
        assert "cannot be injected at all; split it" in inventory
        # E il perché, una volta sola in fondo: la regola sta nel prompt sopra.
        assert "what the SPLIT rule above is about" in inventory
        assert f"past the {ceiling} characters" in inventory

    def test_a_page_that_fits_is_not_annotated(self, tmp_path):
        """Il contro-limite, e non è cerimonia: annotare tutto vuol dire un elenco
        di numeri in cui i nove che contano non si vedono. Il confine è **esatto**
        — una pagina larga quanto il tetto entra, e mandarla a spezzare sarebbe
        lavoro distruttivo su una pagina che il modello legge già."""
        root = _project(tmp_path)
        ceiling = page_ceiling()
        _page_of(root, "corta.md", 300)
        _page_of(root, "esatta.md", ceiling)
        store = _store(tmp_path)

        inventory = store.build_prompt(store.read_delta()).split(
            "## Pages that already exist"
        )[1]

        assert "`corta.md` — Titolo\n" in inventory
        assert "`esatta.md` — Titolo\n" in inventory
        assert "over the ceiling" not in inventory
        assert "SPLIT rule above is about" not in inventory

    def test_the_annotation_costs_no_entry_in_the_list(self, tmp_path):
        """Il tetto dell'elenco conta le **voci**, non i caratteri: l'annotazione
        allunga una riga e ne aggiunge una sola in fondo, quindi non può spingere
        una pagina fuori dall'elenco. Se lo facesse, il rimedio a «non so quali
        pagine sono lunghe» sarebbe «non so quali pagine esistono»."""
        from jenny.agent.gardener import _MAX_INVENTORY_ENTRIES

        root = _project(tmp_path)
        ceiling = page_ceiling()
        extra = 5
        _page_of(root, "aaa-lunga.md", ceiling + 1)
        for i in range(_MAX_INVENTORY_ENTRIES + extra - 1):
            _page_of(root, f"p{i:04d}.md", 200)
        store = _store(tmp_path)

        inventory = store.build_inventory()
        entries = [line for line in inventory.splitlines() if line.startswith("- `")]

        assert len(entries) == _MAX_INVENTORY_ENTRIES
        assert (
            f"list truncated: {_MAX_INVENTORY_ENTRIES + extra} pages in all, "
            f"{_MAX_INVENTORY_ENTRIES} shown"
        ) in inventory
        assert "over the ceiling" in entries[0]

    def test_the_ceiling_is_read_from_where_the_turn_pays_it(self, tmp_path, monkeypatch):
        """**Condivisa, non copiata.** Il tetto vive in un posto solo (il blocco di
        progetto che lo paga) e qui si legge da lì a ogni chiamata: spostarlo lì
        sposta l'annotazione, che è la sola prova che non è una quarta copia in
        attesa di divergere. Con una copia, questo test resterebbe verde mentre la
        passata segnala a una soglia che il turno non usa."""
        from jenny.agent import context

        root = _project(tmp_path)
        _page_of(root, "media.md", 3000)
        store = _store(tmp_path)

        assert "over the ceiling" not in store.build_inventory()

        monkeypatch.setattr(context, "_PROJECT_PAGES_MAX_CHARS", 2000)

        assert "over the ceiling: 3000 characters" in store.build_inventory()
        # E la regola nel prompt nomina lo **stesso** numero: una regola che dice
        # 6000 accanto a un elenco che segnala a 2000 non è applicabile.
        assert "past 2000 characters a page" in store.build_prompt(store.read_delta())

    def test_all_three_copies_of_the_ceiling_are_the_same_number(self):
        """Il terzo lettore è il lint della wiki, che vive in una skill copiabile
        nel workspace e non può importare il package. I due numeri li tiene uguali
        un test suo; questo chiude il triangolo, perché un lint che avvisa a una
        soglia e una passata che spezza a un'altra è lavoro fatto e pagina ancora
        invisibile."""
        from jenny.agent.context import _PROJECT_PAGES_MAX_CHARS

        source = (
            Path(__file__).resolve().parents[2]
            / "jenny" / "skills" / "llm-wiki" / "scripts" / "lint_wiki.py"
        ).read_text(encoding="utf-8")

        assert page_ceiling() == _PROJECT_PAGES_MAX_CHARS
        assert f"PAGE_MAX_CHARS = {_PROJECT_PAGES_MAX_CHARS}" in source


# ── La potatura della mappa ──────────────────────────────────────────────────
#
# **Il numero di testa dell'audit del 23/08: 7 mappe su 8 oltre il tetto** dei
# 2000 caratteri con cui la mappa entra in ogni turno (12.298 / 7.132 / 5.235 /
# 3.480 / 3.229 / 3.089 / 3.016, contro 292 dell'ottava). Quindi su quasi ogni
# progetto il modello vede la **testa** della mappa e nient'altro: su
# ``patreon-creator`` — la peggiore, 12.298 caratteri e 262 righe — il
# troncamento lascia visibili **5 delle 51 pagine** che la mappa nomina.
#
# La causa non è il numero di pagine — l'elenco nudo delle otto wiki costa da
# 478 a 1.972 caratteri, e ci starebbero tutte; per quelle 51 pagine, 1.495 — è
# che la mappa porta **prosa**.
# E la prosa non esce mai da lì, perché niente la toglie: «aggiungi e promuovi,
# non riscrivere» è ritagliata proprio sulla mappa, quindi la mappa cresce e non
# cala. Da cui questa passata, e da cui il verso in cui è scritta: potare la
# mappa **è** una promozione, e l'elenco delle pagine non perde una voce.


_PAGE_NAMES = tuple(f"pagina-{i:02d}" for i in range(33))


def _oversized_map(root: Path) -> int:
    """Una mappa fatta come quelle vere, e restituisce la sua misura.

    Prosa sopra l'elenco, e l'elenco lungo: sono le proporzioni di
    ``patreon-creator`` misurate sul telefono il 23/08 (12.298 caratteri, 262
    righe, 51 pagine nominate su 52 esistenti — la mappa era l'artefatto più
    grosso di quella wiki, più grosso della sua pagina più grossa). Le voci qui
    sono trentatré e non cinquantuno solo per tenere leggibile il fixture: quel
    che conta è che siano molte e che stiano **dopo** la prosa.

    L'elenco sta **in fondo**, e non è un dettaglio di comodo: è dove sta nelle
    mappe vere, ed è la ragione per cui il troncamento a 2000 caratteri toglie
    proprio le voci — cioè l'unica cosa per cui la mappa esiste.
    """
    prose = "\n\n".join(
        f"### Nota {i}\n\n" + "Prosa che spetterebbe a una pagina. " * 12
        for i in range(10)
    )
    listing = "\n".join(f"- [[{name}]] — a cosa serve" for name in _PAGE_NAMES)
    text = f"# progetto\n\n## Decided\n\n{prose}\n\n## Pages\n\n{listing}\n"
    (root / "wiki" / "index.md").write_text(text, "utf-8")
    return len(text.strip())


class _PruningAgent(_FakeAgent):
    """Un agente che pota la mappa **come l'istruzione chiede**: via la prosa, l'elenco intero.

    Non è un LLM e non finge di esserlo. L'invariante da provare qui non è che
    il modello obbedisca — quello lo dice il prompt e lo misura il telefono — ma
    che una potatura corretta esca dal codice per quel che è: la mappa sotto il
    tetto, l'elenco integro, e la riga di log che dice di quanto è calata.
    """

    def __init__(self, sessions_dir: Path, map_path: Path) -> None:
        super().__init__(sessions_dir)
        self._map = map_path

    async def process_direct(self, prompt: str, **kwargs):
        kept = [
            line for line in self._map.read_text(encoding="utf-8").splitlines()
            if line.startswith(("- [[", "#"))
        ]
        self._map.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return await super().process_direct(prompt, **kwargs)


class TestPruningTheMap:
    def _prompt(self, tmp_path, *, oversized: bool) -> tuple[str, int]:
        root = _project(tmp_path)
        chars = _oversized_map(root) if oversized else len(
            (root / "wiki" / "index.md").read_text(encoding="utf-8").strip()
        )
        store = _store(tmp_path)
        return store.build_prompt(store.read_delta()), chars

    def test_the_size_and_the_target_both_reach_the_pass(self, tmp_path):
        """Senza le due misure la passata non può sapere se potare è dovuto, e
        una potatura decisa a occhio è la stessa riscrittura che la regola
        vieta. Stanno accanto al file di cui parlano: una misura letta staccata
        dal suo oggetto è un numero."""
        prompt, chars = self._prompt(tmp_path, oversized=True)

        assert f"— {chars} characters, against a ceiling of {MAP_TARGET_CHARS}" in prompt

    def test_an_oversized_map_is_told_to_prune_where_the_ban_is(self, tmp_path):
        """Il ritaglio va letto **dove sta la regola che ritaglia** — la lezione
        della posizione già pagata dallo split: è «aggiungi e promuovi, non
        riscrivere» a vietare oggi la sola manovra che rimette la mappa dentro
        il tetto."""
        prompt, chars = self._prompt(tmp_path, oversized=True)
        i_rule = prompt.index("Add and promote")
        i_prune = prompt.index("pruning it is a promotion")

        assert i_prune > i_rule
        assert str(chars) in prompt[i_prune:i_prune + 400]
        assert str(MAP_TARGET_CHARS) in prompt[i_prune:i_prune + 400]
        # E non «riassumi la mappa»: la prosa si sposta, non si accorcia.
        assert "moved" in prompt[i_prune:i_prune + 900]

    def test_the_page_list_is_named_untouchable(self, tmp_path):
        """L'elenco è quel che la mappa **è**: il posto da cui si sa quali pagine
        esistono. Una voce persa in una potatura è una pagina che smette di
        esistere per ogni conversazione futura, che è un danno peggiore del
        troncamento che la potatura ripara."""
        prompt, _ = self._prompt(tmp_path, oversized=True)

        assert "Prune prose, never entries" in prompt

    def test_a_map_that_fits_is_not_told_to_prune(self, tmp_path):
        """Il contro-limite, e non è cerimonia: potare costa — sposta prosa
        dentro le pagine — e una passata su una mappa che sta nel suo tetto che
        pota comunque sta riscrivendo per il gusto di farlo."""
        prompt, _ = self._prompt(tmp_path, oversized=False)

        assert "pruning it is a promotion" not in prompt
        assert "Prune prose, never entries" not in prompt
        # E il ritaglio di «never delete» torna a essere quello del solo split.
        assert "a split is the single exception, and it is not a deletion" in prompt

    def test_the_prune_is_carved_out_of_never_delete_too(self, tmp_path):
        """La stessa collisione dello split, per la stessa ragione: potare fa
        **uscire della prosa** da un file, e «never delete a section of one» è
        assoluto. Detto solo altrove, il modello ha davanti un'istruzione e il
        suo divieto — e il divieto è quello scritto in grassetto."""
        prompt, _ = self._prompt(tmp_path, oversized=True)
        i_never = prompt.index("Never delete a page")

        assert "the prune of this map above is the other one" in prompt[i_never:i_never + 400]

    def test_the_target_is_the_number_the_turn_actually_pays(self):
        """Due numeri che devono restare uguali vivono in due file — qui il
        confronto, come già fa il lint della wiki. Se divergono, la passata pota
        verso una soglia che il turno non usa: lavoro fatto e mappa ancora
        tagliata."""
        from jenny.agent.context import _PROJECT_MAP_MAX_CHARS

        assert MAP_TARGET_CHARS == _PROJECT_MAP_MAX_CHARS

    async def test_the_map_arrives_whole_even_when_it_is_the_biggest_thing_there(
        self, tmp_path
    ):
        """La mappa più grossa delle otto vere è 12.298 caratteri: col tetto dei
        diari (8000) sarebbe arrivata **tagliata** proprio alla passata che deve
        accorciarla, col troncamento a nascondere la prosa da promuovere."""
        root = _project(tmp_path)
        (root / "wiki" / "index.md").write_text(
            "# progetto\n\n" + "riga di prosa. " * 820 + "\n- [[in-fondo]]\n", "utf-8"
        )
        store = _store(tmp_path)

        prompt = store.build_prompt(store.read_delta())

        assert "[[in-fondo]]" in prompt
        assert "The map continues" not in prompt

    async def test_the_page_list_survives_a_prune(self, tmp_path):
        """**L'invariante.** La potatura è simulata (v. ``_PruningAgent``) perché
        qui non gira un modello: quel che si prova è che una potatura corretta
        lascia la mappa sotto il tetto **con tutte le sue voci**, e che il
        registro dice di quanto è calata — la potatura è la sola manovra di
        questa passata che *toglie* testo da un file dell'utente."""
        root = _project(tmp_path)
        before = _oversized_map(root)
        assert before > MAP_TARGET_CHARS
        page = root / "wiki" / "index.md"
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        outcome = await run_gardener(_PruningAgent(tmp_path, page), store)

        assert outcome.status == "written"
        pruned = page.read_text(encoding="utf-8").strip()
        missing = [name for name in _PAGE_NAMES if f"[[{name}]]" not in pruned]
        assert missing == [], f"la potatura ha perso {len(missing)} voci: {missing}"
        assert len(pruned) < MAP_TARGET_CHARS
        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert f"map pruned: {before} → {len(pruned)} characters" in log
        assert f"(-{before - len(pruned)})" in log

    async def test_a_pass_that_left_the_map_alone_says_nothing_about_it(self, tmp_path):
        """Una mappa **cresciuta** non si annota: crescere è il caso normale di
        una promozione, e sta già nel conto delle scritture. Il registro è una
        riga per operazione, e una nota che c'è sempre non si legge più."""
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        await run_gardener(_FakeAgent(tmp_path), store)

        assert "map pruned" not in (root / "log" / "20260823.md").read_text(encoding="utf-8")


# ── La passata ───────────────────────────────────────────────────────────────


class TestTheRun:
    async def test_no_new_lines_means_no_provider_call(self, tmp_path):
        _project(tmp_path, journal=False)
        agent = _FakeAgent(tmp_path)

        outcome = await run_gardener(agent, _store(tmp_path))

        assert outcome.status == "skipped_no_delta"
        assert agent.calls == []
        assert not outcome.ran

    async def test_a_pass_that_wrote_advances_the_cursor_and_logs(self, tmp_path):
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=2, ok=2))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "written"
        assert read_state(root).cursor == {"raw/journal/20260822.md": 4}
        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "gardener | 2 journal lines (20260822) → 2 writes" in log
        # Il contro-limite della regola sui rifiuti: tutte atterrate, quindi la
        # riga resta quella di prima e non parla di rifiuti.
        assert "refused" not in log

    async def test_nothing_to_promote_is_an_outcome_and_says_so_in_the_log(self, tmp_path):
        """Il cursore avanza — riproporre le stesse righe darebbe la stessa
        risposta a un costo nuovo — e **da oggi la riga di log si scrive**.

        Questo test diceva l'opposto, e la sua ragione era che «una riga per ogni
        giro a vuoto renderebbe illeggibile l'unico registro che c'è». Difendeva
        da un caso che qui non arriva: senza delta la passata esce a
        ``skipped_no_delta``, prima del modello. Chi arriva a scrivere il log ha
        letto righe vere, e se non promuove **il cursore le brucia comunque** —
        su un diario append-only, cioè per sempre.

        Il caso di campo (25/08, ``viaggio-pazzo``): tre passate in un giorno,
        **una** riga di log. Dal registro non si distingueva «non è mai passato»
        da «è passato e ha deciso di no», che è il dubbio con cui l'utente ha
        aperto l'indagine.
        """
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=0, ok=0))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "nothing_to_promote"
        assert read_state(root).cursor == {"raw/journal/20260822.md": 4}
        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        # Il conto delle righe lette è la metà che conta: dice **su cosa** il
        # cursore è passato. «0 writes» direbbe il numero e non il fatto.
        assert "gardener | 2 journal lines (20260822) → nothing promoted" in log
        assert "0 writes" not in log

    async def test_blocked_writes_leave_the_journal_unread(self, tmp_path):
        """La proprietà per cui il predicato di commit di Dream è condiviso: se
        il cursore avanzasse dopo una passata che ha provato a scrivere e non ci
        è riuscita, quelle righe risulterebbero digerite da un lavoro che non è
        avvenuto — e nessun giro successivo le rivedrebbe mai."""
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=2, ok=0))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "no_write"
        assert read_state(root).cursor == {}

    async def test_a_partly_refused_pass_leaves_the_journal_unread(self, tmp_path):
        """Il caso che passava per riuscito: **due scritture su tre**.

        ``internal_run_should_commit`` dice sì a ``writes_ok > 0``, quindi il
        cursore avanzava — e il diario è append-only e nessuno lo rilegge, perciò
        le righe dietro la scrittura rifiutata erano perse per sempre. Si
        trattiene invece, e la ripromozione al giro dopo è il prezzo che
        l'inventario nel prompt e la regola «aggiungi e promuovi, non riscrivere»
        sono fatti per assorbire.
        """
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=3, ok=2))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "partial_write"
        assert read_state(root).cursor == {}
        # L'esito **nomina** i rifiuti: un chiamante che vede solo "ha scritto 2"
        # non ha modo di sapere che il cursore è fermo.
        assert "2 of 3 writes landed" in outcome.detail
        assert "1 refused" in outcome.detail
        assert outcome.writes == 2

    async def test_the_log_line_of_a_partly_refused_pass_says_so(self, tmp_path):
        """La riga diceva «2 writes» come se fossero tutte, e questo log è
        l'unico registro che c'è: da nessun altro posto il difetto sarebbe
        emerso."""
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=3, ok=2))

        await run_gardener(_FakeAgent(tmp_path), store)

        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "2 of 3 writes (1 refused, journal left unread)" in log
        assert "→ 2 writes in" not in log

    async def test_a_flag_survives_a_partly_refused_pass(self, tmp_path):
        """La segnalazione è la cosa più importante che una passata possa dire, e
        non si perde perché una delle scritture è stata rifiutata."""
        root = _project(tmp_path)
        agent = _FakeAgent(tmp_path)
        agent.reply = "fatto a metà.\n\nFLAG: treno.md e tappe.md non concordano"

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=3, ok=2)))

        assert "- flagged: treno.md e tappe.md non concordano" in (
            root / "log" / "20260823.md"
        ).read_text(encoding="utf-8")

    async def test_an_unfinished_turn_leaves_the_journal_unread(self, tmp_path):
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        outcome = await run_gardener(_FakeAgent(tmp_path, stop_reason="max_iterations"), store)

        assert outcome.status == "incomplete"
        assert read_state(root).cursor == {}

    async def test_an_unfinished_turn_with_refusals_is_still_unfinished(self, tmp_path):
        """L'ordine dei rami: «non ha finito» viene prima di «ha finito a metà»,
        altrimenti un turno troncato con una scrittura rifiutata si racconterebbe
        come una passata conclusa."""
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=3, ok=2))

        outcome = await run_gardener(_FakeAgent(tmp_path, stop_reason="max_iterations"), store)

        assert outcome.status == "incomplete"
        assert read_state(root).cursor == {}

    async def test_a_dead_provider_is_an_outcome_not_a_crash(self, tmp_path):
        root = _project(tmp_path)

        outcome = await run_gardener(_ExplodingAgent(tmp_path), _store(tmp_path))

        assert outcome.status == "failed" and "provider is down" in outcome.detail
        assert read_state(root).cursor == {}

    async def test_a_cursor_that_cannot_be_written_is_an_outcome_not_a_crash(
        self, tmp_path, monkeypatch
    ):
        """Il disco che rifiuta il cursore **dopo** che le pagine sono scritte.

        Il ``commit`` stava fuori dalla regione protetta, quindi un ``OSError``
        usciva da ``run_gardener`` intero: nessun log, nessuna potatura, e
        un'eccezione al posto di un esito per i due chiamanti — a lavoro già
        fatto. Adesso è un esito che dice le due cose vere e distinte: le pagine
        ci sono, il cursore è fermo.
        """
        from loguru import logger as loguru_logger

        root = _project(tmp_path)
        page = root / "wiki" / "tappe.md"
        oldest = _stale_sessions(tmp_path)
        agent = _WritingAgent(tmp_path, page)
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        def _full_disk(*_args, **_kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr("jenny.agent.gardener.write_state", _full_disk)
        errors: list[str] = []
        handler_id = loguru_logger.add(errors.append, level="ERROR", format="{message}")
        try:
            outcome = await run_gardener(agent, store)
        finally:
            loguru_logger.remove(handler_id)

        assert outcome.status == "commit_failed"
        assert "no space left on device" in outcome.detail
        assert outcome.writes == 1
        # Le pagine sono su disco; il cursore no.
        assert page.is_file()
        assert read_state(root).cursor == {}
        assert any("cursor was not recorded" in line for line in errors)
        # E anche questa uscita ripulisce la propria traccia.
        assert not oldest.exists()

    @pytest.mark.parametrize(
        "status, stop_reason, attempted, ok",
        [
            ("written", "completed", 1, 1),
            ("nothing_to_promote", "completed", 0, 0),
            ("partial_write", "completed", 3, 2),
            ("no_write", "completed", 2, 0),
            ("incomplete", "max_iterations", 1, 1),
            ("failed", "_explode", 1, 1),
        ],
    )
    async def test_every_exit_path_prunes_its_session(
        self, tmp_path, status, stop_reason, attempted, ok
    ):
        """La potatura su **ogni** uscita, elencate una per una.

        La chiave della passata porta il timestamp, quindi ogni giro crea una
        sessione nuova e una voce in ``AgentLoop._session_locks``: un ramo che
        torna prima della potatura non lascia un file di troppo, ne lascia uno per
        passata *per sempre*. Era il caso di ``failed`` — cioè del ramo che con un
        provider giù si prende a ogni tick del cron.
        """
        _project(tmp_path)
        agent = (
            _ExplodingAgent(tmp_path)
            if stop_reason == "_explode"
            else _FakeAgent(tmp_path, stop_reason=stop_reason)
        )
        oldest = _stale_sessions(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=attempted, ok=ok))

        outcome = await run_gardener(agent, store)

        assert outcome.status == status
        assert not oldest.exists()
        # Il file da solo non basta: senza l'eviction la voce in
        # ``_session_locks`` resta, ed è la perdita che non si vede.
        assert agent.evicted == ["gardener:viaggio-old00"]

    async def test_the_second_pass_has_nothing_left_to_read(self, tmp_path):
        """L'idempotenza vista da fuori: una passata riuscita chiude il suo
        materiale, e la successiva non riparte da capo."""
        _project(tmp_path)
        agent = _FakeAgent(tmp_path)
        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        outcome = await run_gardener(agent, _store(tmp_path))

        assert outcome.status == "skipped_no_delta"
        assert len(agent.calls) == 1

    async def test_each_pass_starts_from_a_fresh_session(self, tmp_path):
        """Il giardiniere non ha memoria dei propri giri: la sua memoria sono le
        pagine e il cursore. Una chiave con il timestamp lo rende vero invece di
        raccomandarlo."""
        _project(tmp_path)
        agent = _FakeAgent(tmp_path)

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        key = agent.calls[0]["session_key"]
        assert key.startswith("gardener:viaggio-")
        assert agent.calls[0]["ephemeral"] is True

    async def test_the_state_file_is_not_a_wiki_page(self, tmp_path):
        """Il cursore sta sotto ``.jenny/``: fuori dalle viste, fuori dal grafo,
        fuori dal prompt — e senza che nessuno dei tre lo impari."""
        root = _project(tmp_path)

        await run_gardener(_FakeAgent(tmp_path), _with_states(
            _store(tmp_path), _states(attempted=1, ok=1)
        ))

        assert (root / ".jenny" / "gardener.json").is_file()
        assert not list((root / "wiki").glob(".*"))


def test_the_state_survives_a_restart(tmp_path):
    """Il cursore è su disco, non in memoria: un riavvio non fa ricominciare."""
    root = _project(tmp_path)
    store = _store(tmp_path)
    store.commit(store.read_delta())

    assert read_state(root) != GardenerState()
    assert _store(tmp_path).read_delta().is_empty


# ── Una passata per progetto, e l'utente vince ───────────────────────────────


class _BlockingAgent(_FakeAgent):
    """Un agente che si ferma dentro ``process_direct`` finché non lo si libera.

    Serve a tenere una passata *in volo* mentre il test ne lancia una seconda:
    senza un punto in cui la prima aspetta, due ``await run_gardener`` sono due
    passate in fila, e la concorrenza da provare non esiste.
    """

    def __init__(self, sessions_dir: Path) -> None:
        super().__init__(sessions_dir)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def process_direct(self, prompt: str, **kwargs):
        self.started.set()
        await self.release.wait()
        return await super().process_direct(prompt, **kwargs)


class _ReturningUserAgent(_FakeAgent):
    """Scrive una pagina, poi l'utente torna su quel progetto, poi ci riprova.

    Usa la cassetta **vera** — il gancio che cede il passo vive nei tool di
    scrittura, quindi con la cassetta finta di ``_with_states`` questo test
    passerebbe senza provare niente.
    """

    def __init__(
        self, sessions_dir: Path, *, project: str = "viaggio", returns: bool = True
    ) -> None:
        super().__init__(sessions_dir)
        self._project = project
        self._returns = returns
        self.active: tuple[str, ...] = ()
        self.results: list[str] = []

    def active_session_keys(self) -> tuple[str, ...]:
        return self.active

    async def process_direct(self, prompt: str, **kwargs):
        write = kwargs["tools"].get("write_file")
        base = f"wikis/{self._project}/wiki"
        self.results.append(await write.execute(path=f"{base}/tappe.md", content="# tappe\n"))
        if self._returns:
            # L'utente scrive nella conversazione del progetto: un turno in volo.
            self.active = (f"project:{self._project}",)
        self.results.append(await write.execute(path=f"{base}/date.md", content="# date\n"))
        return await super().process_direct(prompt, **kwargs)


@pytest.fixture(autouse=True)
def _no_passes_left_in_flight():
    """Il registro delle passate è un globale di processo: va lasciato vuoto.

    Autouse su tutto il modulo di proposito: una voce dimenticata da un test non
    fa cadere *quel* test, fa cadere il prossimo che tocca lo stesso nome — cioè
    il modo peggiore di rompersi.
    """
    from jenny.agent.gardener import _PASSES_IN_FLIGHT

    _PASSES_IN_FLIGHT.clear()
    yield
    _PASSES_IN_FLIGHT.clear()


class TestOnePassAtATime:
    """Due passate sullo stesso progetto non devono esistere.

    Il difetto: ``store.session_key()`` porta il timestamp, quindi è **diversa a
    ogni giro**, e il registro dei lock è per chiave di sessione — un ``/gardener
    viaggio`` lanciato durante un tick del cron dava due passate concorrenti che
    riscrivevano lo stesso ``wiki/index.md`` da due ``FileStates`` separati, e
    ``write_file`` sovrascrive senza chiedere.
    """

    async def test_a_second_pass_on_the_same_project_is_refused(self, tmp_path):
        """Il secondo arrivato usa un agente **suo**, e non è un dettaglio di
        comodità: se le due passate condividessero l'agente bloccato, una guardia
        rotta non farebbe cadere il test — lo farebbe *appendere*, perché la
        seconda passata aspetterebbe un rilascio che arriva solo dopo di lei. Un
        test che si blocca invece di rompersi è un test che il giorno del difetto
        non dice niente.
        """
        _project(tmp_path)
        holder = _BlockingAgent(tmp_path)
        first = asyncio.create_task(
            run_gardener(holder, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))
        )
        await holder.started.wait()
        latecomer = _FakeAgent(tmp_path)

        second = await run_gardener(
            latecomer, _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        holder.release.set()
        assert second.status == "already_running"
        # Il conto delle chiamate è l'asserzione che conta: uno stato giusto con
        # due turni LLM avvenuti sarebbe il difetto, raccontato bene.
        assert latecomer.calls == []
        assert (await first).status == "written"
        assert len(holder.calls) == 1

    async def test_a_refused_pass_costs_nothing_and_leaves_the_cursor_alone(self, tmp_path):
        """Rifiutata vuol dire *non partita*: nessun timbro, nessun cursore, e
        nemmeno lo snapshot del workspace — che è una scansione, non un dettaglio."""
        root = _project(tmp_path)
        holder = _BlockingAgent(tmp_path)
        first = asyncio.create_task(
            run_gardener(holder, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))
        )
        await holder.started.wait()
        agent = _FakeAgent(tmp_path)
        snapshots_before = len(agent.snapshots)

        second = await run_gardener(agent, _store(tmp_path))
        # Letto **prima** di liberare la prima passata: dopo, quel che si vede sul
        # disco è il suo commit, e questo test non parla di lei.
        state_after_the_refusal = read_state(root)

        holder.release.set()
        await first
        assert second.ran is False
        assert second.failures == 0
        assert state_after_the_refusal == GardenerState()
        # Nessuno snapshot in più: il rifiuto arriva prima della rete, che è una
        # scansione del workspace e non un dettaglio.
        assert len(agent.snapshots) == snapshots_before

    async def test_another_project_is_not_held_up(self, tmp_path):
        """Il registro è per **nome di progetto**: due progetti diversi sono due
        lavori diversi, e serializzarli sarebbe un tetto inventato."""
        _project(tmp_path)
        _project(tmp_path, "orto")
        holder = _BlockingAgent(tmp_path)
        first = asyncio.create_task(
            run_gardener(holder, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))
        )
        await holder.started.wait()

        outcome = await run_gardener(
            _FakeAgent(tmp_path),
            _with_states(_store(tmp_path, "orto"), _states(attempted=1, ok=1)),
        )

        holder.release.set()
        await first
        assert outcome.status == "written"

    @pytest.mark.parametrize(
        "status, stop_reason, attempted, ok",
        [
            ("written", "completed", 1, 1),
            ("nothing_to_promote", "completed", 0, 0),
            ("partial_write", "completed", 3, 2),
            ("no_write", "completed", 2, 0),
            ("incomplete", "max_iterations", 1, 1),
            ("failed", "_explode", 1, 1),
        ],
    )
    async def test_every_exit_path_releases_the_project(
        self, tmp_path, status, stop_reason, attempted, ok
    ):
        """La presa si molla su **ogni** uscita, elencate una per una.

        Gemello di ``test_every_exit_path_prunes_its_session``, e la ragione è la
        stessa: una voce che resta non fa cadere questa passata, rende quel
        progetto non giardinabile **fino al riavvio del processo**.
        """
        from jenny.agent.gardener import _PASSES_IN_FLIGHT

        _project(tmp_path)
        agent = (
            _ExplodingAgent(tmp_path)
            if stop_reason == "_explode"
            else _FakeAgent(tmp_path, stop_reason=stop_reason)
        )
        store = _with_states(_store(tmp_path), _states(attempted=attempted, ok=ok))

        outcome = await run_gardener(agent, store)

        assert outcome.status == status
        assert _PASSES_IN_FLIGHT == set()

    async def test_an_exception_out_of_the_pass_still_releases_the_project(self, tmp_path):
        """L'uscita che il ``try``/``finally`` esiste per coprire.

        ``run_gardener`` trasforma in esito quel che sa (provider giù, cursore non
        scritto), ma non tutto: un errore che esce dalla costruzione della cassetta
        propaga. Senza il ``finally`` quel nome resta preso per sempre, e il
        sintomo non è un'eccezione — è un giardiniere che da lì in poi risponde
        «già in volo» a ogni richiesta su quel progetto.
        """
        from jenny.agent.gardener import _PASSES_IN_FLIGHT

        _project(tmp_path)
        broken = _store(tmp_path)

        def _boom(**_kwargs):
            raise RuntimeError("cassetta rotta")

        broken.build_tools = _boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await run_gardener(_FakeAgent(tmp_path), broken)

        assert _PASSES_IN_FLIGHT == set()
        # E la prova che serve davvero: la passata dopo parte.
        agent = _FakeAgent(tmp_path)
        outcome = await run_gardener(
            agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert outcome.status == "written"


class TestYieldingToTheUser:
    """Il cancello del fermo si valuta alla **selezione**, e basta quello.

    ``gardener_schedule.py`` dice in prima persona che quel cancello è l'unica
    cosa che tiene utente e giardiniere lontani dalla stessa mappa — ma un
    messaggio che arriva un secondo dopo l'inizio trova una passata lunga fra i 14
    e i 26 secondi e nessuno a fermarla. Da qui il controllo prima di *ogni*
    scrittura.
    """

    @pytest.fixture
    def bound(self, tmp_path):
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        yield
        reset_workspace_scope(token)

    async def test_a_user_turn_mid_pass_aborts_it(self, tmp_path, bound):
        from loguru import logger as loguru_logger

        root = _project(tmp_path)
        agent = _ReturningUserAgent(tmp_path)
        warnings: list[str] = []
        handler_id = loguru_logger.add(warnings.append, level="WARNING", format="{message}")
        try:
            outcome = await run_gardener(agent, _store(tmp_path))
        finally:
            loguru_logger.remove(handler_id)

        assert outcome.status == "aborted_user_active"
        # La prima pagina è atterrata, la seconda no: il gancio ha morso in mezzo.
        assert (root / "wiki" / "tappe.md").is_file()
        assert not (root / "wiki" / "date.md").exists()
        # Il cursore è tenuto: quelle righe devono tornare.
        assert read_state(root).cursor == {}
        # E il motivo è detto, non dedotto.
        assert "the user's own turn on viaggio started" in outcome.detail
        assert any("yielded to the user" in line for line in warnings)

    async def test_the_model_is_told_why_it_cannot_write(self, tmp_path, bound):
        """Il rifiuto arriva al modello come frase, non come silenzio: è l'unico
        modo che ha di chiudere il turno invece di riprovare in cerchio."""
        _project(tmp_path)
        agent = _ReturningUserAgent(tmp_path)

        await run_gardener(agent, _store(tmp_path))

        assert "Successfully wrote" in agent.results[0]
        assert "the user is back" in agent.results[1]

    async def test_a_quiet_project_is_not_disturbed(self, tmp_path, bound):
        """Il controllo non deve poter rifiutare una passata normale: il gancio
        legge chi è in volo, e su un progetto zitto non c'è nessuno."""
        root = _project(tmp_path)
        agent = _ReturningUserAgent(tmp_path, returns=False)
        # Un turno in volo c'è, ma su **un altro** progetto: non deve contare.
        agent.active = ("project:orto",)

        outcome = await run_gardener(agent, _store(tmp_path))

        assert outcome.status == "written"
        assert (root / "wiki" / "tappe.md").is_file()
        assert (root / "wiki" / "date.md").is_file()
        assert read_state(root).cursor != {}

    async def test_an_agent_that_cannot_say_who_is_in_flight_is_not_blocked(
        self, tmp_path, bound
    ):
        """Fuori dal gateway ``active_session_keys`` non c'è. Un gancio che in quel
        caso rifiutasse spegnerebbe il giardiniere in ogni test e in ogni
        ispezione; uno che c'è ma non sa niente è peggio di uno assente."""
        _project(tmp_path)

        class _NoSignal(_ReturningUserAgent):
            active_session_keys = None  # type: ignore[assignment]

        outcome = await run_gardener(_NoSignal(tmp_path), _store(tmp_path))

        assert outcome.status == "written"


# ── Il checkpoint ────────────────────────────────────────────────────────────


class TestTheCheckpoint:
    """Il giardiniere è il primo lavoro periodico che scrive dentro le cartelle
    *dell'utente*, non in un file derivato: un file derivato si ricostruisce
    al run dopo, una pagina scritta a mano e sovrascritta non si ricostruisce da
    niente — il diario copre solo quel che dal diario è nato."""

    async def test_a_pass_checkpoints_the_workspace_first(self, tmp_path):
        _project(tmp_path)
        agent = _FakeAgent(tmp_path)

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        assert agent.snapshots == ["pre_gardener"]

    async def test_a_tick_with_nothing_to_read_does_not_scan_the_workspace(self, tmp_path):
        """Uno snapshot per tick a vuoto sarebbe una scansione del workspace ogni
        mezz'ora per niente: il checkpoint sta **dopo** il cancello del delta."""
        _project(tmp_path, journal=False)
        agent = _FakeAgent(tmp_path)

        await run_gardener(agent, _store(tmp_path))

        assert agent.snapshots == []

    async def test_a_failing_checkpoint_does_not_stop_the_pass(self, tmp_path):
        """**Fail-open, e non è pigrizia.** Il verso opposto — «nessuna passata
        senza checkpoint» — trasformerebbe uno store di snapshot pieno in un
        taccuino che smette di lavorare in silenzio: un guasto peggiore di quello
        che previene. Il checkpoint è una rete, non un permesso."""
        _project(tmp_path)

        class _Broken(_FakeAgent):
            async def take_snapshot(self, trigger: str) -> bool:
                raise RuntimeError("snapshot store full")

        outcome = await run_gardener(
            _Broken(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert outcome.status == "written"

    async def test_no_hook_at_all_is_not_an_error(self, tmp_path):
        """Fuori dal gateway la rete non c'è (test, ispezione): la passata gira."""
        _project(tmp_path)

        class _Bare(_FakeAgent):
            take_snapshot = None

        outcome = await run_gardener(
            _Bare(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert outcome.status == "written"

    def test_the_model_is_not_told_it_is_protected(self, tmp_path):
        """Dream ha un ramo di prompt che promette la reversibilità, e serve a
        fargli potare di più. Qui non c'è e non ci va: aggiungere-e-promuovere
        vale *anche* con la rete, e prometterla sposterebbe il giudizio nella
        direzione sbagliata — verso il rimpasto."""
        _project(tmp_path)
        store = _store(tmp_path)

        prompt = store.build_prompt(store.read_delta()).lower()

        for promise in ("snapshot", "reversible", "checkpoint", "undo", "restore"):
            assert promise not in prompt, f"il prompt promette una rete: {promise}"

# ── Quel che la passata trova ────────────────────────────────────────────────


class TestTheFlag:
    """Il canale nasce da un buco: la risposta della passata serviva solo al
    predicato di commit e alla contabilità token, e il **testo** veniva buttato —
    quindi il prompt diceva «se due pagine si contraddicono, dillo» e quel report
    non arrivava a nessuno.

    Due destinazioni per due pubblici: la sezione aperta della **mappa**, che il
    modello scrive da sé e che entra nel prompt di ogni turno (quindi raggiunge la
    conversazione), e una riga nel **log**, che è la storia che una persona
    rilegge. Qui si prova la seconda.
    """

    @pytest.mark.parametrize("reply,expected", [
        ("ho fatto le pagine.\n\nNOTHING TO FLAG", None),
        ("fatto.\n\nFLAG: treno.md e tappe.md dicono due orari diversi",
         "treno.md e tappe.md dicono due orari diversi"),
        ("solo prosa senza formula", None),
        ("", None),
        ("FLAG:", None),
    ])
    def test_the_marker_is_read_and_prose_is_not(self, reply, expected):
        from jenny.agent.gardener import extract_flag

        assert extract_flag(SimpleNamespace(content=reply)) == expected

    @pytest.mark.parametrize("reply", [
        "**FLAG:** due pagine litigano",
        "> FLAG: due pagine litigano",
        "- **FLAG:** due pagine litigano",
        "`FLAG:` due pagine litigano",
        "flag: due pagine litigano",
        "## FLAG: due pagine litigano",
        "   FLAG: due pagine litigano",
    ])
    def test_the_marker_is_recognised_with_its_markdown_on(self, reply):
        """Il caso ordinario, e costava il canale intero.

        Il marcatore va cercato e non interpretato — su questo il disegno non si
        tocca — ma cercarlo **nudo e a inizio riga** perdeva la forma che un
        modello scrive per bella copia dopo un prompt fatto di grassetti e di
        elenchi. E lo perdeva **senza traccia**: «nessun marcatore» vuol dire di
        proposito «niente da segnalare», quindi il report di una contraddizione
        non arrivava a nessuno e nessun log diceva che era esistito.
        """
        from jenny.agent.gardener import extract_flag

        assert extract_flag(SimpleNamespace(content=reply)) == "due pagine litigano"

    def test_the_no_flag_line_is_recognised_with_its_markdown_on_too(self):
        """L'altra metà, e non è simmetria per bellezza: un ``**NOTHING TO
        FLAG**`` non riconosciuto lascia la scansione dal fondo a proseguire
        verso l'alto, dove può incontrare la riga in cui il modello *cita* il
        contratto — cioè inventare una segnalazione che non c'è."""
        from jenny.agent.gardener import extract_flag

        cited = "FLAG: is for what I cannot settle.\nQui le pagine concordano.\n**NOTHING TO FLAG**"

        assert extract_flag(SimpleNamespace(content=cited)) is None

    def test_only_the_decoration_of_the_marker_is_eaten(self):
        """Gli asterischi attaccati ai due punti sono la chiusura del grassetto
        del marcatore; quelli dopo uno spazio sono del messaggio, e mangiarli
        vorrebbe dire riscrivere quel che una persona deve leggere."""
        from jenny.agent.gardener import extract_flag

        flag = extract_flag(SimpleNamespace(content="FLAG: **treno.md** e tappe.md"))

        assert flag == "**treno.md** e tappe.md"

    def test_it_reads_the_marker_from_the_end(self):
        """Dal fondo, perché **il marcatore chiude** la risposta: cercandolo
        dall'inizio si prende la riga in cui il modello *cita* il contratto
        mentre ragiona, e si inventa una segnalazione che non c'è.

        La forma del test è quella che discrimina, e ci è voluta una mutazione
        per trovarla: la citazione deve stare a **inizio riga**, perché è così
        che un modello che ragiona sul proprio contratto scrive. Con la citazione
        in mezzo alla riga le due direzioni danno lo stesso risultato e il test
        non prova niente — era la prima stesura.
        """
        from jenny.agent.gardener import extract_flag

        quoting_then_clearing = (
            "FLAG: is for things I cannot settle on my own.\n"
            "Here the two pages agree, so nothing applies.\n"
            "NOTHING TO FLAG"
        )
        quoting_then_flagging = (
            "NOTHING TO FLAG would be wrong here: le pagine si contraddicono.\n"
            "FLAG: budget.md e tappe.md non concordano"
        )

        assert extract_flag(SimpleNamespace(content=quoting_then_clearing)) is None
        assert extract_flag(SimpleNamespace(content=quoting_then_flagging)) == (
            "budget.md e tappe.md non concordano"
        )

    def test_a_long_flag_is_cut_not_dropped(self):
        """Il log è «una riga per operazione»: un paragrafo lo rende illeggibile,
        ed è l'unico registro che c'è. Ma tagliare è meglio che perdere."""
        from jenny.agent.gardener import extract_flag

        flag = extract_flag(SimpleNamespace(content="FLAG: " + "x" * 500))

        assert flag is not None and len(flag) == 300

    def test_a_reply_with_no_content_is_not_an_error(self):
        from jenny.agent.gardener import extract_flag

        assert extract_flag(None) is None
        assert extract_flag(SimpleNamespace(metadata={})) is None

    async def test_a_flag_reaches_the_log(self, tmp_path):
        root = _project(tmp_path)
        agent = _FakeAgent(tmp_path)
        agent.reply = "fatto.\n\nFLAG: due pagine dicono orari diversi"

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "- flagged: due pagine dicono orari diversi" in log

    async def test_no_flag_leaves_the_log_line_alone(self, tmp_path):
        root = _project(tmp_path)

        await run_gardener(
            _FakeAgent(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert "flagged" not in (root / "log" / "20260823.md").read_text(encoding="utf-8")

    async def test_a_flag_is_kept_even_when_the_pass_promoted_nothing(self, tmp_path):
        """Una passata a vuoto non lascia traccia — era la regola e resta — ma una
        segnalazione è la cosa più importante che una passata possa dire, e non si
        perde per non aver promosso niente."""
        root = _project(tmp_path)
        agent = _FakeAgent(tmp_path)
        agent.reply = "niente da promuovere.\n\nFLAG: la pagina treno cita una fonte assente"

        outcome = await run_gardener(
            agent, _with_states(_store(tmp_path), _states(attempted=0, ok=0))
        )

        assert outcome.status == "nothing_to_promote"
        assert "la pagina treno cita una fonte assente" in (
            root / "log" / "20260823.md"
        ).read_text(encoding="utf-8")


class TestTheClosingContract:
    def _prompt(self, tmp_path) -> str:
        _project(tmp_path)
        store = _store(tmp_path)
        return store.build_prompt(store.read_delta())

    @pytest.mark.parametrize("piece", ["NOTHING TO FLAG", "FLAG:"])
    def test_the_two_closing_lines_are_specified(self, tmp_path, piece):
        assert piece in self._prompt(tmp_path)

    def test_a_contradiction_goes_into_the_map_not_into_prose(self, tmp_path):
        """La destinazione scartata era ``audit/``: vuole ``anchor_before`` e
        ``target_lines``, cioè è ancorata a un intervallo di righe perché nasce
        per correggere un testo. «Queste due pagine si contraddicono» non ha
        un'ancora, e inventarne una vuol dire scrivere un'ancora finta in un
        canale che il lint verifica."""
        prompt = self._prompt(tmp_path)

        assert "write the question" in prompt and "open section" in prompt
        assert "say so in your reply" not in prompt

    def test_deciding_it_alone_is_the_forbidden_thing(self, tmp_path):
        assert "Deciding it yourself is the one thing you must not do" in self._prompt(tmp_path)

# ── Il controllo incrociato ──────────────────────────────────────────────────


def _transcript_path(name: str) -> Path:
    from jenny.config.paths import get_webui_dir

    return get_webui_dir() / f"websocket_project_{name}.jsonl"


@pytest.fixture(autouse=True)
def _no_leftover_transcript():
    """La dir della WebUI è **condivisa** dalla fixture di workspace, che è di
    sessione: un transcript scritto da un test resta lì per il successivo. Visto
    subito (un test che verificava l'assenza della sezione la trovava), e vale la
    pena tenerlo pulito qui invece che ricordarselo in ogni test."""
    import shutil

    yield
    for name in ("orto", "viaggio"):
        _transcript_path(name).unlink(missing_ok=True)
        # I segmenti di una rotazione stanno in una **cartella** accanto al file
        # attivo, e la loro sola esistenza dice «ci sono messaggi più vecchi»
        # (v. ``read_recent_user_messages``): lasciarla in piedi cambierebbe
        # l'esito del test dopo.
        shutil.rmtree(_transcript_path(name).with_suffix(".segments"), ignore_errors=True)


def _transcript(tmp_path: Path, name: str, *messages: str) -> Path:
    """Il transcript di un progetto, nella forma che scrive la WebUI."""
    path = _transcript_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, message in enumerate(messages):
        rows.append(json.dumps({
            "event": "user", "chat_id": f"project:{name}", "text": message,
            "turn_id": f"t{i}", "turn_phase": "user", "turn_seq": i,
        }))
        # Il ragionamento **nomina l'utente**, ed è la forma vera: sul telefono
        # una riga di reasoning dice letteralmente «The user is telling me facts
        # about…». Un testo di prova che non contiene la parola veniva scartato
        # dal filtro grezzo, quindi il filtro vero risultava non provato —
        # trovato per mutazione, ed è il terzo dato di prova irrealistico di
        # oggi.
        # Due righe di rumore, e la seconda è quella che conta. Il codice ha un
        # pre-filtro grezzo (``'"user"' not in raw``) che scarta la gran parte dei
        # delta senza parsarli, e un controllo vero su ``event``/``role``. Una
        # riga di ragionamento normale è scartata dal pre-filtro, quindi da sola
        # **non prova** il controllo vero: ci vuole una riga che il pre-filtro
        # ammette — e un modello che ragiona scrive davvero `the "user" wants`,
        # col termine fra virgolette. Trovato per mutazione, due volte di fila.
        rows.append(json.dumps({
            "event": "reasoning_delta", "chat_id": f"project:{name}",
            "text": "The user is telling me facts; pensiero che non deve entrare nel confronto",
        }))
        rows.append(json.dumps({
            "event": "reasoning_delta", "chat_id": f"project:{name}",
            "text": 'quoting the "user" verbatim: ragionamento che non è una cosa detta',
        }))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class TestReadingWhatWasSaid:
    """Il lato «detto» del controllo incrociato.

    Nessun cursore sul transcript, ed è una decisione: le righe non portano un
    timestamp e il file attivo **ruota** in segmenti, quindi un conteggio di righe
    si azzererebbe senza dirlo. Le ultime N invece sono sempre leggibili, e lo
    stato del confronto è **il diario stesso** — quel che è stato recuperato ci
    sta dentro, quindi il giro dopo non si recupera due volte.
    """

    def test_it_reads_only_the_user_turns(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages

        _transcript(tmp_path, "orto", "primo messaggio", "secondo messaggio")

        said, truncated = read_recent_user_messages("orto")

        assert said == ["primo messaggio", "secondo messaggio"]
        assert truncated is False

    def test_a_project_that_never_talked_gives_nothing(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages

        assert read_recent_user_messages("mai-parlato") == ([], False)

    def test_it_keeps_the_tail_and_says_it_cut(self, tmp_path):
        """Taglia **dalla testa**: i messaggi più recenti sono quelli che la
        cattura può aver mancato adesso."""
        from jenny.agent.gardener import read_recent_user_messages

        _transcript(tmp_path, "orto", *[f"messaggio {i}" for i in range(10)])

        said, truncated = read_recent_user_messages("orto", limit=3)

        assert said == ["messaggio 7", "messaggio 8", "messaggio 9"]
        assert truncated is True

    def test_the_char_ceiling_also_cuts_from_the_head(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages

        _transcript(tmp_path, "orto", "x" * 100, "y" * 100, "z" * 100)

        said, truncated = read_recent_user_messages("orto", max_chars=150)

        assert said == ["z" * 100]
        assert truncated is True

    def test_a_rotated_transcript_says_the_window_is_partial(self, tmp_path):
        """Il taglio non è solo il tetto: è anche il file che si è spezzato.

        Superati gli 8 MB, ``transcript_store`` sposta i turni vecchi in
        ``<key>.segments/`` e lascia sul posto la coda. La finestra risultava
        allora **intera** — tre messaggi, ``truncated=False`` — mentre metà
        conversazione stava in un altro file: la rete del controllo incrociato
        era lossy *e* muta.
        """
        from jenny.agent.gardener import read_recent_user_messages

        _transcript(tmp_path, "orto", "primo", "secondo", "terzo")
        segments = _transcript_path("orto").with_suffix(".segments")
        segments.mkdir(parents=True, exist_ok=True)
        (segments / "000001.jsonl").write_text(
            json.dumps({"event": "user", "text": "detto mesi fa"}) + "\n", "utf-8"
        )

        said, truncated = read_recent_user_messages("orto")

        assert said == ["primo", "secondo", "terzo"]
        assert truncated is True

    def test_the_tail_is_read_without_parsing_the_whole_file(self, tmp_path, monkeypatch):
        """Il file attivo arriva a 8 MB e di quel file serve **la coda**.

        Prima si leggeva da riga uno accumulando ogni messaggio dell'utente della
        storia del progetto, per poi tenerne quaranta: un costo per passata e per
        progetto, pagato sul lato più grosso del prompt.

        Si conta il numero di ``json.loads``, che è l'unità di costo vera qui (il
        pre-filtro grezzo scarta senza parsare). Con la lettura in avanti sono
        tutte le righe che nominano l'utente; dal fondo sono quelle della coda più
        una.
        """
        import jenny.agent.gardener as gmod

        _transcript(tmp_path, "orto", *[f"messaggio {i}" for i in range(200)])
        parsed = []
        real = gmod.json.loads
        monkeypatch.setattr(
            gmod.json, "loads", lambda raw, *a, **k: (parsed.append(1), real(raw, *a, **k))[1]
        )

        said, truncated = gmod.read_recent_user_messages("orto", limit=5)

        assert said == [f"messaggio {i}" for i in range(195, 200)]
        assert truncated is True
        # Sei messaggi dell'utente più le due righe di rumore che il pre-filtro
        # ammette per ciascuno: la lettura in avanti ne parserebbe seicento.
        assert len(parsed) < 30, len(parsed)

    def test_a_broken_line_does_not_stop_the_rest(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages
        from jenny.config.paths import get_webui_dir

        path = _transcript(tmp_path, "orto", "buono")
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"event":"user","text":"rotto\n')
        assert get_webui_dir().is_dir()

        said, _ = read_recent_user_messages("orto")

        assert said == ["buono"]


class TestTheCrossCheck:
    def _prompt(self, tmp_path, *messages: str):
        _project(tmp_path, "orto")
        if messages:
            _transcript(tmp_path, "orto", *messages)
        store = _store(tmp_path, "orto")
        return store.build_prompt(store.read_delta())

    def test_what_was_said_reaches_the_prompt(self, tmp_path):
        prompt = self._prompt(tmp_path, "il furgone ha le gomme da cambiare")

        assert "What the user actually said" in prompt
        assert "gomme da cambiare" in prompt

    def test_the_journal_of_those_days_comes_with_it(self, tmp_path):
        """Il delta contiene solo le righe **nuove**, e un fatto già catturato sta
        spesso *sotto* il cursore: confrontare il detto col solo delta
        segnalerebbe come mancante tutto quel che una passata precedente aveva già
        letto — cioè, sulla seconda passata di una giornata, quasi tutto."""
        prompt = self._prompt(tmp_path, "una cosa detta")

        assert "What the journal already holds, over the same recent stretch" in prompt

    def test_without_a_transcript_the_section_is_absent(self, tmp_path):
        """Nessun transcript vuol dire nessun confronto: una sezione vuota
        chiederebbe al modello di cercare in un posto che non c'è."""
        prompt = self._prompt(tmp_path)

        assert "What the user actually said" not in prompt

    def test_the_thinking_of_past_turns_is_not_offered_as_something_said(self, tmp_path):
        """Un transcript è fatto in gran parte di ragionamento e delta. Prenderli
        per «cose dette dall'utente» farebbe recuperare nel diario i pensieri del
        modello — cioè trasformerebbe l'anticorpo nella malattia."""
        prompt = self._prompt(tmp_path, "cosa vera")

        assert "pensiero che non deve entrare" not in prompt
        assert "ragionamento che non è una cosa detta" not in prompt

    @pytest.mark.parametrize("rule", [
        "a stable fact they said that the journal never recorded",
        "recorded as a reason, not as a fact",
        "When in doubt, leave it",
        "still be true next week",
        "cannot change the journal, only add to it",
    ])
    def test_the_task_and_its_four_limits_are_stated(self, tmp_path, rule):
        assert rule in self._prompt(tmp_path, "una cosa")

    def test_the_number_of_limits_matches_the_limits(self, tmp_path):
        """Il numero scritto e i punti elencati, confrontati invece che asseriti.

        Sopravvissuto del giro di mutazioni del 25/08 — lo stesso giorno in cui il
        quarto limite è stato aggiunto: riportare «Four» a «Three» non faceva
        cadere niente. Il difetto che ne segue è silenzioso e del tipo peggiore,
        perché agisce su chi legge: si contano tre punti e si smette, e quello
        saltato è l'ultimo — qui la deroga appena scritta.

        Contato dal testo e non fissato a quattro, così il prossimo limite non
        deve ricordarsi di aggiornare anche un numero in un test.
        """
        prompt = self._prompt(tmp_path, "una cosa")
        words = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6}

        header = re.search(r"(\w+) limits, and they matter more than the task:", prompt)
        assert header, "la sezione dei limiti non è più riconoscibile"
        declared = words.get(header.group(1))
        assert declared, f"«{header.group(1)} limits»: numero non riconosciuto"
        section = prompt.split(header.group(0), 1)[1].split("\n## ", 1)[0]
        listed = len(re.findall(r"^- \*\*", section, re.M))

        assert declared == listed, f"ne dichiara {declared} e ne elenca {listed}"

    def test_the_buried_fact_carve_out_carries_both_of_its_conditions(self, tmp_path):
        """La deroga del 25/08, e le due condizioni che la tengono stretta.

        Recuperare un fatto **già presente in altre parole** è esattamente la
        manovra che il limite accanto vieta, e per una ragione misurata: una riga
        doppia diventa una seconda pagina, o una pagina che litiga con se stessa.
        La deroga esiste perché un fatto sepolto come subordinata non è stato
        registrato *come fatto* — è stato registrato come la ragione di un altro
        — ma senza le due condizioni si allarga fino a coprire qualunque
        riformulazione, cioè fino a essere il difetto che voleva curare.

        Le due condizioni si asseriscono **qui e insieme**: tolta una, la deroga
        resta scritta e smette di essere stretta, e nessun altro test se ne
        accorgerebbe. La forma «only ... subordinate clause» è la terza gamba —
        senza quella la deroga non dice nemmeno di cosa parla.
        """
        prompt = self._prompt(tmp_path, "una cosa")

        assert "**only** as a\n  subordinate clause" in prompt, "manca il caso a cui si applica"
        # **La frase intera, non il test di durata da solo.** «still be true if
        # this project ended» compare *già* nel prompt, nella sezione «The work»
        # che decide quali righe meritano una pagina: asserirla nuda passava
        # anche con la condizione tolta di qui. L'ha scoperto la mutazione, non
        # la lettura.
        assert "it would still be true if this project ended, and no page" in prompt, (
            "manca la condizione di durata *dentro la deroga*"
        )
        assert "already\n  named after it" in prompt, "manca la condizione della pagina esistente"
        assert "Both, or leave it" in prompt, (
            "senza «entrambe» le due condizioni si leggono come alternative, e una sola "
            "basta a riaprire la porta ai duplicati"
        )

    def test_the_model_is_told_the_journal_it_sees_is_a_window(self, tmp_path):
        """Il blocco registrato è una finestra, non il diario intero (T2.4 lo
        taglia, T2.10 lo sposta sui giorni recenti). Un modello a cui non si dice
        legge «non c'è qui» come «non c'è», e recupera un fatto già registrato su
        un giorno che non gli è stato dato."""
        prompt = self._prompt(tmp_path, "una cosa")

        assert "the journal you are shown is a **window**" in prompt
        assert "recorded on an older day" in prompt

    async def test_a_truncated_window_is_written_into_the_project_log(self, tmp_path):
        """La rete lossy diventa una rete che lo dice.

        La finestra del confronto è le ultime 40 cose dette, 6.000 caratteri, e
        **nessun cursore** — deliberatamente, perché il transcript ruota. Fra due
        passate passano sei ore: sei ore chiacchierone spingono la parte iniziale
        fuori portata *per sempre*, e un fatto può cadere da entrambe le reti (la
        cattura non l'ha visto, il confronto non lo vede più) senza che nessuna
        persona lo sappia. Il modello lo sapeva già; il registro no, ed è il
        registro che una persona rilegge.
        """
        root = _project(tmp_path)
        _transcript(tmp_path, "viaggio", *[f"detto {i}" for i in range(41)])

        await run_gardener(
            _FakeAgent(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "- cross-check window truncated" in log

    async def test_a_window_that_fits_leaves_the_log_line_alone(self, tmp_path):
        """Il gemello negativo, e serve: la sottoriga si annota su una passata
        sola quando c'è qualcosa da annotare. Un progetto tranquillo non deve
        leggere a ogni giro che la sua finestra è tagliata."""
        root = _project(tmp_path)
        _transcript(tmp_path, "viaggio", "detto una cosa sola")

        await run_gardener(
            _FakeAgent(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert "cross-check" not in (root / "log" / "20260823.md").read_text(encoding="utf-8")


def _journal_day(root: Path, day: str, body: str) -> Path:
    """Un giorno di diario in più, nella forma che la cattura lascia sul disco."""
    path = root / "raw" / "journal" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {day}\n\n- 09:00 — {body}\n", "utf-8")
    return path


class TestTheRecordedSideOfTheCrossCheck:
    """Il blocco di diario del confronto: il suo tetto (T2.4) e la sua finestra (T2.10).

    Due difetti che stavano nello stesso posto. Il tetto: ogni giorno era tagliato
    a ``_MAX_JOURNAL_CHARS`` e il **blocco** a niente, e il numero di giorni lo
    limitava solo ``MAX_DELTA_LINES`` — duecento righe non lette possono stare in
    duecento file giornalieri. La finestra: il lato «detto» è sempre la coda dei
    messaggi, cioè adesso, mentre il lato «registrato» erano i giorni del delta,
    cioè dove sta il cursore. Le due cose coincidono solo quando il delta *è* la
    coda.
    """

    def test_the_whole_block_has_a_ceiling_and_it_drops_the_older_days(self, tmp_path):
        from jenny.agent.gardener import _MAX_JOURNAL_BLOCK_CHARS

        root = _project(tmp_path, "orto")
        for day in range(1, 11):
            _journal_day(root, f"202608{day:02d}", "x" * 5000)

        recorded = _store(tmp_path, "orto").read_journal_days()

        # I giorni recenti ci sono, i vecchi no: il budget si spende **dal più
        # recente**, che è la parte su cui il confronto può agire.
        assert "20260822" in recorded
        assert "20260810" in recorded
        assert "20260801" not in recorded
        # Il margine copre la nota del taglio e i separatori fra i blocchi, non
        # un altro giorno da 5.000 caratteri.
        assert len(recorded) <= _MAX_JOURNAL_BLOCK_CHARS + 500

    def test_the_days_left_out_are_reported(self, tmp_path):
        """Mai troncare zitti: è la stessa regola di ``_read_capped`` e del delta."""
        root = _project(tmp_path, "orto")
        for day in range(1, 11):
            _journal_day(root, f"202608{day:02d}", "x" * 5000)

        recorded = _store(tmp_path, "orto").read_journal_days()

        assert "8 earlier journal day(s) are not shown" in recorded

    def test_the_newest_day_arrives_even_when_it_fills_the_budget_alone(self, tmp_path):
        """Un blocco che annuncia «tre giorni non mostrati» e non mostra niente è
        peggio di nessun blocco: il giorno più recente entra comunque."""
        root = _project(tmp_path, "orto")
        _journal_day(root, "20260823", "il vicino si chiama Enzo")

        recorded = _store(tmp_path, "orto").read_journal_days(max_chars=10)

        assert "il vicino si chiama Enzo" in recorded
        assert "20260822" not in recorded

    def test_the_block_budget_is_wider_than_both_the_day_and_the_messages(self):
        """I due numeri su cui poggia l'argomento di T2.10, e nessuno dei due è
        decorativo.

        Più largo del **giorno**: così il giorno più recente — quello che il
        confronto può davvero usare — ci sta sempre dentro anche quando arriva al
        proprio tetto. Più largo della **coda dei messaggi**: la coda non si può
        datare (le righe del transcript non portano un timestamp), quindi la
        finestra la definisce il diario e deve arrivare almeno tanto indietro
        quanto la coda si estende. Il diario è una distillazione della
        conversazione, quindi a parità di budget copre già più giorni: il doppio è
        il margine.
        """
        from jenny.agent.gardener import (
            _MAX_JOURNAL_BLOCK_CHARS,
            _MAX_JOURNAL_CHARS,
            _MAX_TRANSCRIPT_CHARS,
        )

        assert _MAX_JOURNAL_BLOCK_CHARS > _MAX_JOURNAL_CHARS
        assert _MAX_JOURNAL_BLOCK_CHARS >= 2 * _MAX_TRANSCRIPT_CHARS

    def test_a_cursor_stuck_in_the_past_still_shows_the_recent_journal(self, tmp_path):
        """Il primo caso di T2.10, e il più caro.

        Cursore perso su un progetto con mesi di diario: il delta è il **fondo**,
        cioè i giorni più vecchi, messi accanto ai messaggi di questo mese. Col
        lato registrato preso dal delta ogni fatto recente si leggeva come «detto e
        mai registrato», e la passata appendeva una dozzina di righe
        ``[recovered]`` per fatti che stavano già nel diario e già sulle pagine.
        """
        root = _project(tmp_path, "orto")
        store = _store(tmp_path, "orto")
        # Il giorno recente è letto fino in fondo (cursore e testimone veri,
        # scritti dal macchinario e non a mano), poi arriva un giorno **vecchio**:
        # da qui il delta tocca solo quello.
        store.commit(store.read_delta())
        _journal_day(root, "20260601", "una cosa di giugno")

        delta = store.read_delta()
        recorded = store.read_journal_days()

        assert [f.path for f in delta.files] == ["raw/journal/20260601.md"]
        assert "si parte il 14" in recorded

    def test_a_recovered_line_stays_visible_once_the_cursor_has_passed_it(self, tmp_path):
        """Il secondo caso di T2.10: il regime, su un progetto tranquillo.

        Una riga recuperata viene promossa e il cursore le passa sopra. Col lato
        registrato preso dal delta, il giro dopo mostrava solo il file di domani —
        la riga non si vedeva più, mentre la coda dei messaggi (invariata, perché
        il progetto è tranquillo) conteneva ancora il fatto. **Si recuperava di
        nuovo.** La rivendicazione di idempotenza («lo stato del confronto è il
        diario stesso») vale solo se il diario che si vede copre la coda che si
        vede.
        """
        from jenny.agent.gardener import RECOVERED_MARKER

        root = _project(tmp_path, "orto")
        with (root / "raw" / "journal" / "20260822.md").open("a", encoding="utf-8") as fh:
            fh.write(f"- 09:20 {RECOVERED_MARKER} — il vicino si chiama Enzo\n")
        store = _store(tmp_path, "orto")
        store.commit(store.read_delta())
        _journal_day(root, "20260823", "si parte alle sette")

        delta = store.read_delta()
        recorded = store.read_journal_days()

        assert [f.path for f in delta.files] == ["raw/journal/20260823.md"]
        assert "il vicino si chiama Enzo" in recorded
        assert RECOVERED_MARKER in recorded


class TestRecovering:
    async def test_a_recovered_line_is_marked_and_appended(self, tmp_path):
        """Il marcatore sta nel **codice** e non nel prompt, perché è l'unico modo
        che ha di non essere dimenticato: una riga di diario senza origine è
        indistinguibile da una detta a voce quel giorno."""
        from jenny.agent.gardener import RECOVERED_MARKER

        root = _project(tmp_path, "orto")
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        try:
            tool = _store(tmp_path, "orto").build_tools().get("journal_append")
            out = await tool.execute(text="il vicino si chiama Enzo")
        finally:
            reset_workspace_scope(token)

        from datetime import date

        # Il recupero va nella pagina di **oggi**, non nel giorno che stava
        # confrontando: quella riga la scrive adesso, e datarla ieri la
        # nasconderebbe sotto il cursore di una passata già fatta.
        today = root / "raw" / "journal" / f"{date.today():%Y%m%d}.md"
        yesterday = root / "raw" / "journal" / "20260822.md"

        text = today.read_text(encoding="utf-8")
        assert RECOVERED_MARKER in text
        assert "il vicino si chiama Enzo" in text
        assert "journal" in out
        assert RECOVERED_MARKER not in yesterday.read_text(encoding="utf-8")

    async def test_it_appends_and_leaves_the_earlier_lines_alone(self, tmp_path):
        """La proprietà per cui questo tool può entrare in una cassetta chiusa:
        appende, e non c'è modo di riscrivere la fonte da cui sta promuovendo."""
        root = _project(tmp_path, "orto")
        page = next(iter((root / "raw" / "journal").glob("*.md")))
        before = page.read_bytes()
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        try:
            tool = _store(tmp_path, "orto").build_tools().get("journal_append")
            await tool.execute(text="recuperato")
        finally:
            reset_workspace_scope(token)

        assert page.read_bytes().startswith(before)

    async def test_the_project_is_injected_not_deduced(self, tmp_path):
        """Su un turno dell'utente il progetto si deduce dallo scope, ed è giusto.
        Ma una passata interna gira con lo scope **di default**, quindi la
        deduzione darebbe «nessun progetto» e il tool rifiuterebbe: chi costruisce
        la cassetta sa su quale progetto sta lavorando e lo passa."""
        root = _project(tmp_path, "orto")
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        try:
            tool = _store(tmp_path, "orto").build_tools().get("journal_append")
            out = await tool.execute(text="un fatto")
        finally:
            reset_workspace_scope(token)

        assert "No journal here" not in out
        assert list((root / "raw" / "journal").glob("*.md"))


# ── La mappa, da sola, è una ragione per girare ──────────────────────────────


class TestTheMapAloneIsAReasonToRun:
    """Passo **T3.5**: la distanza fra «il produttore è riparato» e «l'artefatto è
    riparato».

    T3.4 insegna alla passata a potare una mappa oltre il tetto di iniezione. La
    misura del 23/08/2026 sul telefono dice che quell'istruzione non sarebbe
    arrivata a nessun modello: otto progetti veri, ``raw/journal/`` **vuota** su
    tutti e otto, mappa oltre il tetto su **sette**. Con il solo delta come
    innesco, nessuna passata partiva — e su un progetto che l'utente non sta usando
    «finché la cattura non scrive righe» vuol dire mai.

    Il test che vale più degli altri è
    ``test_a_pass_that_could_not_shrink_the_map_does_not_come_back``: una ragione
    che resta vera *dopo* la passata è un livelock, e nessuno dei due freni
    esistenti la ferma — il timbro del tentativo ritarda di
    ``min_hours_between_passes`` e non esclude, e ``failures`` non conta le
    potature a metà perché quelle **committano**.
    """

    def _sessions(self):
        return SimpleNamespace(read_session_metadata=lambda _key: None)

    async def test_an_empty_journal_and_an_oversized_map_still_calls_the_provider(self, tmp_path):
        root = _project(tmp_path, journal=False)
        _oversized_map(root)
        agent = _FakeAgent(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=0, ok=0))

        outcome = await run_gardener(agent, store)

        assert outcome.ran and outcome.map_pass
        assert len(agent.calls) == 1

    async def test_an_empty_journal_and_a_map_within_budget_spends_nothing(self, tmp_path):
        """Il contro-limite del test sopra: senza di lui la ragione nuova è «gira
        sempre», cioè un turno LLM per progetto a ogni distanza minima su
        un'installazione che non ha niente da fare."""
        _project(tmp_path, journal=False)
        agent = _FakeAgent(tmp_path)

        outcome = await run_gardener(agent, _store(tmp_path))

        assert outcome.status == "skipped_no_delta"
        assert agent.calls == [] and not outcome.map_pass

    async def test_the_prompt_names_the_map_as_the_whole_job(self, tmp_path):
        """A delta vuoto la sezione delle righe nuove non può restare vuota: il
        prompt apre dicendo «ti sono date le righe che nessuno ha letto», e un
        modello che non ne trova nessuna va a cercarsi del lavoro — o a
        inventarlo, che è la cosa che il prompt vieta in fondo."""
        root = _project(tmp_path, journal=False)
        _oversized_map(root)
        store = _store(tmp_path)

        prompt = store.build_prompt(store.read_delta())

        assert "This pass is here for the map alone" in prompt
        # E l'ordine di potare c'è: senza, la passata non ha nessun compito.
        assert "pruning it is a promotion" in prompt

    async def test_the_cross_check_stays_out_of_a_map_pass(self, tmp_path, monkeypatch):
        """Una passata a delta vuoto gira **per la mappa** e ha un compito solo.

        Da T2.10 la guardia in ``build_prompt`` è tutto quel che tiene fuori il
        confronto: il lato «registrato» adesso ci sarebbe comunque
        (``read_journal_days`` legge i giorni recenti, non i giorni del delta).
        Quindi la ragione non è più «non ci sarebbe niente contro cui misurare» ma
        il compito e la spesa — fino a 18.000 caratteri di prompt, 6.000 di
        messaggi più 12.000 di diario, per un lavoro che a questa passata non
        appartiene."""
        root = _project(tmp_path, journal=False)
        _oversized_map(root)
        from jenny.agent import gardener as gmod

        monkeypatch.setattr(
            gmod, "read_recent_user_messages", lambda *_a, **_k: (["ho detto una cosa"], False)
        )
        store = _store(tmp_path)

        prompt = store.build_prompt(store.read_delta())

        assert "What the user actually said" not in prompt
        assert "ho detto una cosa" not in prompt

    async def test_a_map_pass_records_the_size_it_left_behind(self, tmp_path):
        root = _project(tmp_path, journal=False)
        before = _oversized_map(root)
        page = root / "wiki" / "index.md"
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        outcome = await run_gardener(_PruningAgent(tmp_path, page), store)

        after = len(page.read_text(encoding="utf-8").strip())
        assert outcome.status == "written" and outcome.map_pass
        assert outcome.map_before == before and outcome.map_after == after
        assert read_state(root).map_left_at == after

    async def test_a_pass_that_could_not_shrink_the_map_does_not_come_back(self, tmp_path):
        """**Il test del livelock, da un capo all'altro.**

        Il modello vede l'ordine di potare e non pota niente (``no_write``). Il
        tick dopo il cancello del fermo è aperto, quello della distanza lo si apre
        di proposito — sette ore — e la mappa è ancora esattamente dov'era: se il
        freno fosse il solo timbro del tentativo, questa passata tornerebbe ogni
        sei ore, con lo stesso prompt e lo stesso esito, per sempre.
        """
        from jenny.agent.gardener_schedule import pick_project

        root = _project(tmp_path, journal=False)
        chars = _oversized_map(root)
        store = _with_states(_store(tmp_path), _states(attempted=2, ok=0))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "no_write" and outcome.map_pass
        assert read_state(root).map_left_at == chars

        later = datetime.now() + timedelta(hours=7)
        assert pick_project(
            tmp_path, idle_min=30, min_hours_between_passes=6,
            sessions=self._sessions(), now=later,
        ) is None

    async def test_a_map_that_grew_past_it_comes_back(self, tmp_path):
        """L'altro verso, senza il quale il freno è un blocco definitivo: una mappa
        che *ricresce* è lavoro nuovo, non lo stesso lavoro rifatto."""
        from jenny.agent.gardener_schedule import pick_project

        root = _project(tmp_path, journal=False)
        _oversized_map(root)
        store = _with_states(_store(tmp_path), _states(attempted=2, ok=0))
        await run_gardener(_FakeAgent(tmp_path), store)

        page = root / "wiki" / "index.md"
        page.write_text(page.read_text("utf-8") + "\nProsa cresciuta dopo.\n", "utf-8")

        later = datetime.now() + timedelta(hours=7)
        pick = pick_project(
            tmp_path, idle_min=30, min_hours_between_passes=6,
            sessions=self._sessions(), now=later,
        )

        assert pick is not None and pick.reason == "map"

    async def test_the_log_line_of_a_map_pass_says_the_map_alone(self, tmp_path):
        """«0 journal lines ()» sarebbe una riga di registro che non dice cosa è
        successo, e questo log è l'unico registro che c'è."""
        root = _project(tmp_path, journal=False)
        before = _oversized_map(root)
        page = root / "wiki" / "index.md"
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        await run_gardener(_PruningAgent(tmp_path, page), store)

        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "gardener | the map alone → 1 writes" in log
        assert f"map pruned: {before} →" in log

    async def test_a_provider_that_fell_over_does_not_disarm_the_prune(self, tmp_path):
        """Il ramo ``failed`` non registra la misura: il provider è caduto, l'ordine
        di potare non l'ha visto nessuno, e disarmare lì vorrebbe dire perdere la
        potatura di quella mappa per un guasto di rete. Il timbro c'è comunque,
        quindi la ripetizione resta ferma alla distanza minima."""
        root = _project(tmp_path, journal=False)
        _oversized_map(root)
        store = _with_states(_store(tmp_path), _states(attempted=0, ok=0))

        outcome = await run_gardener(_ExplodingAgent(tmp_path), store)

        assert outcome.status == "failed" and outcome.map_pass
        state = read_state(root)
        assert state.map_left_at is None
        assert state.last_attempt_at and state.failures == 1


# ── T2.5: il delta si legge una volta, e la chiave non si accumula ────────────


class TestTheDeltaIsReadOnce:
    """Il diario si apre una volta per passata, non due.

    ``read_journal_delta`` fa un ``read_text`` **intero** di ogni
    ``raw/journal/*.md`` prima di guardare il cursore, quindi il conto delle
    aperture è il costo vero, non una metrica di comodo.
    """

    @staticmethod
    def _journal_reads(monkeypatch) -> list[str]:
        """I ``read_text`` sui file di diario, in ordine.

        Si spia ``Path.read_text`` e non ``read_journal_delta``: il difetto era
        *quante volte i file si aprono*, e una chiamata in meno alla funzione non
        sarebbe la stessa cosa se dentro leggesse due volte.
        """
        seen: list[str] = []
        original = Path.read_text

        def _spy(self, *a, **kw):
            if "journal" in self.as_posix():
                seen.append(self.name)
            return original(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _spy)
        return seen

    async def test_a_full_pass_opens_each_journal_file_once(self, tmp_path, monkeypatch):
        """La selezione **e** la passata, come nel cron: due letture erano due.

        Il fixture porta un diario con righe non lette, quindi ``pick_project``
        arriva fino al delta e ``run_gardener`` gira davvero: senza righe il
        cancello del delta chiuderebbe la passata e questo test passerebbe con
        zero letture, cioè misurando niente. Per questo si asserisce **esattamente
        una** apertura per file e non «al massimo una».
        """
        from jenny.agent.gardener_schedule import pick_project

        _project(tmp_path)
        store_states = _states(attempted=1, ok=1)
        reads = self._journal_reads(monkeypatch)

        pick = pick_project(
            tmp_path, idle_min=30, min_hours_between_passes=6,
            sessions=SimpleNamespace(read_session_metadata=lambda _key: None),
        )
        assert pick is not None and pick.delta_lines == 2
        outcome = await run_gardener(
            _FakeAgent(tmp_path), _with_states(pick.store, store_states), delta=pick.delta
        )

        assert outcome.ran, "la passata non è partita: il conteggio non misurerebbe niente"
        assert reads == ["20260822.md"]

    async def test_the_pass_uses_the_delta_it_was_handed(self, tmp_path):
        """L'identità, non solo il conteggio: il prompt parla di *quel* delta.

        È la metà che il conteggio non copre. Una passata che rileggesse i diari e
        poi ignorasse l'argomento darebbe lo stesso numero di aperture se la
        rilettura fosse abbastanza furba, e comunque committerebbe un cursore che
        copre righe che il modello non ha mai visto.
        """
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        delta = store.read_delta()
        # Una riga arriva **dopo** la lettura: la passata non deve vederla, e il
        # cursore committato non deve dichiararla digerita.
        with (root / "raw" / "journal" / "20260822.md").open("a", encoding="utf-8") as fh:
            fh.write("- 09:20 — arrivata dopo la selezione\n")

        agent = _FakeAgent(tmp_path)
        outcome = await run_gardener(agent, store, delta=delta)

        assert outcome.status == "written"
        assert "arrivata dopo la selezione" not in agent.calls[0]["prompt"]
        assert read_state(root).cursor == {"raw/journal/20260822.md": 4}, (
            "la passata ha riletto il diario: il cursore copre la riga arrivata dopo"
        )

    async def test_the_manual_path_still_reads_it_itself(self, tmp_path):
        """``/gardener`` non ha nessuna selezione davanti, quindi la lettura è la
        sua: ``delta=None`` deve restare una strada che funziona, non un ripiego
        che nessuno percorre."""
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "written" and outcome.lines == 2
        assert read_state(root).cursor == {"raw/journal/20260822.md": 4}

    async def test_a_refused_pass_does_not_open_the_journal(self, tmp_path, monkeypatch):
        """Il conto della guardia di T2.3, riletto da qui: una passata rifiutata
        perché un'altra è in volo non deve pagare l'apertura dei diari. Vale anche
        per ``/gardener``, che è chi la lettura la farebbe."""
        from jenny.agent.gardener import _PASSES_IN_FLIGHT

        _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        reads = self._journal_reads(monkeypatch)

        _PASSES_IN_FLIGHT.add("viaggio")
        try:
            outcome = await run_gardener(_FakeAgent(tmp_path), store)
        finally:
            _PASSES_IN_FLIGHT.discard("viaggio")

        assert outcome.status == "already_running"
        assert reads == []


class _RegistryAgent(_FakeAgent):
    """Un agente che tiene i **registri veri** di ``AgentLoop``.

    Non reimplementa niente di ciò che si misura: ``SessionLocks``,
    ``FileStateStore`` e ``SessionManager.invalidate`` sono le classi di
    produzione, e i due metodi di sgombero — ``evict_pruned_sessions`` e
    ``forget_file_reads`` — sono le funzioni di ``AgentLoop`` prese così come
    sono. Quel che questo fake fa a mano sono le **due righe di ingresso di
    turno** che ``AgentLoop`` esegue per ogni ``process_direct``:
    ``_session_locks.get(key)`` (``loop.py``, in ``process_direct_outcome``) e
    ``_file_state_store.for_session(key)`` (in ``_run_agent_loop``). Sono loro a
    creare la voce, quindi sono loro che vanno riprodotte perché il conteggio
    voglia dire qualcosa.
    """

    def __init__(self, sessions_dir: Path) -> None:
        super().__init__(sessions_dir)
        from jenny.agent.session_locks import SessionLocks
        from jenny.agent.tools.file_state import FileStateStore

        self._session_locks = SessionLocks()
        self._file_state_store = FileStateStore()
        self._active_tasks: dict[str, list] = {}
        self.sessions = SimpleNamespace(
            sessions_dir=sessions_dir, invalidate=lambda _key: None
        )

    async def process_direct(self, prompt: str, **kwargs):
        key = kwargs.get("session_key")
        assert isinstance(key, str)
        self._session_locks.get(key)
        self._file_state_store.for_session(key)
        return await super().process_direct(prompt, **kwargs)

    @property
    def keyspace(self) -> tuple[int, int]:
        return (
            len(self._session_locks._locks),
            len(self._file_state_store._states_by_key),
        )


def _borrow_eviction(cls) -> None:
    """Mette sul fake i due metodi di sgombero **veri** di ``AgentLoop``."""
    from jenny.agent.loop import AgentLoop
    from jenny.agent.loop_tasks import LoopTasksMixin

    cls.evict_pruned_sessions = LoopTasksMixin.evict_pruned_sessions
    cls.forget_file_reads = AgentLoop.forget_file_reads


_borrow_eviction(_RegistryAgent)


class TestTheKeySpaceStopsGrowing:
    """Venti passate non lasciano venti voci morte in memoria.

    La chiave della passata porta il timestamp, quindi ogni giro ne crea una
    nuova. Il processo è pensato per stare su settimane.
    """

    async def test_twenty_passes_do_not_leave_twenty_entries(self, tmp_path):
        """I due registri, misurati insieme.

        E i due non erano nella stessa condizione, che è la correzione che questo
        test scrive: il lock era **già** limitato — ``prune_internal_sessions``
        tiene dieci file e ``evict_pruned_sessions`` sgombera le chiavi potate —
        mentre ``FileStateStore`` non sta su quella strada e cresceva di una voce
        per passata per la vita del processo.
        """
        root = _project(tmp_path)
        agent = _RegistryAgent(tmp_path)

        for day in range(20):
            (root / "raw" / "journal" / f"202609{day + 1:02d}.md").write_text(
                f"# giorno {day}\n\n- 09:00 — riga {day}\n", "utf-8"
            )
            await run_gardener(
                agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1))
            )

        locks, file_states = agent.keyspace
        assert len(agent.calls) == 20, "le passate non sono girate: il conto non misura niente"
        # Il lock resta limitato dalla potatura (dieci file tenuti, più il giro
        # in corso); lo stato dei file si dimentica subito, quindi zero.
        assert file_states == 0
        assert locks <= 11

    async def test_the_key_of_the_pass_is_the_key_forgotten(self, tmp_path, monkeypatch):
        """La chiave giusta e non una nuova.

        ``store.session_key()`` legge l'orologio a ogni chiamata, quindi chiamarlo
        una seconda volta nel ``finally`` dimenticherebbe una chiave che non è mai
        esistita e lascerebbe lì quella vera. **L'orologio va spostato a mano**: la
        chiave ha risoluzione di un secondo, e una passata di test dura meno di
        così — con l'orologio vero le due chiamate cadono nello stesso secondo, il
        difetto sparisce e il test misurerebbe soltanto la propria velocità. Qui
        ogni lettura avanza di un minuto, quindi una seconda chiamata **si vede
        sempre**.
        """
        _project(tmp_path)
        real_now = datetime.now()
        ticks = iter(range(1, 500))

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003 — la firma è quella di datetime
                return real_now + timedelta(minutes=next(ticks))

        monkeypatch.setattr("jenny.agent.gardener.datetime", _Clock)
        agent = _RegistryAgent(tmp_path)
        forgotten: list[str] = []
        real = agent.forget_file_reads

        def _spy(key: str) -> None:
            forgotten.append(key)
            real(key)

        agent.forget_file_reads = _spy  # type: ignore[method-assign]

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        assert forgotten == [agent.calls[0]["session_key"]]

    async def test_a_pass_that_blew_up_forgets_its_key_too(self, tmp_path):
        """Il ramo che conta: su un provider giù è quello che si prende ogni
        mezz'ora, ed è dove le voci morte si sarebbero accumulate più in fretta.
        """
        _project(tmp_path)

        class _Boom(_RegistryAgent):
            async def process_direct(self, prompt: str, **kwargs):
                await super().process_direct(prompt, **kwargs)
                raise RuntimeError("provider is down")

        agent = _Boom(tmp_path)
        outcome = await run_gardener(
            agent, _with_states(_store(tmp_path), _states(attempted=0, ok=0))
        )

        assert outcome.status == "failed"
        assert agent.keyspace[1] == 0


def test_the_borrowed_eviction_is_the_real_one() -> None:
    """Il cancello sotto ``_RegistryAgent``: se ``AgentLoop.forget_file_reads``
    smettesse di sgomberare ``FileStateStore``, i test qui sopra passerebbero
    comunque — starebbero misurando un fake. Questo legge il metodo vero.
    """
    from jenny.agent.loop import AgentLoop
    from jenny.agent.tools.file_state import FileStateStore

    holder = SimpleNamespace(_file_state_store=FileStateStore())
    holder._file_state_store.for_session("gardener:x-1")
    AgentLoop.forget_file_reads(holder, "gardener:x-1")

    assert holder._file_state_store._states_by_key == {}


# ── I tetti di produzione ────────────────────────────────────────────────────


class TestTheProductionCaps:
    """I tetti nel valore che gira sul telefono, non nel valore iniettato (T8.5).

    Un tetto con un default e un parametro per sovrascriverlo si prova due volte:
    che *tagli* (e per quello ogni test inietta il proprio numero, piccolo, così
    il corpus resta leggibile), e che il numero **di produzione** sia quello. Il
    secondo mancava: misurato il 23/08, ``MAX_DELTA_LINES = 200 -> 100000``
    passava tutti gli 8.245 test, e così ``_MAX_INVENTORY_ENTRIES = 300 -> 3000``.

    La forma del difetto è la stessa nei due casi, ed è la ragione per cui questi
    test costruiscono un corpus **grande in assoluto**: l'unico test che leggeva
    ``_MAX_INVENTORY_ENTRIES`` ne ricavava anche il numero di pagine da creare, e
    un test che scala col tetto non lo può fissare — resta verde qualunque numero
    ci sia scritto.

    Il caso che questi due tetti difendono non è la giornata parlante, sono venti
    righe: è il cursore perso e il progetto vecchio, cioè mesi di diario e
    centinaia di pagine in un prompt solo, su una bolletta di token che paga
    l'utente.
    """

    def test_the_delta_cap_binds_with_no_cap_injected(self, tmp_path):
        """5.000 voci, ``GardenerState()`` vuoto, **nessun** ``max_lines``."""
        root = _project(tmp_path, "orto", journal=False)
        journal = root / "raw" / "journal"
        journal.mkdir(parents=True)
        (journal / "20260101.md").write_text(
            "# 2026-01-01\n\n" + "".join(f"- 09:00 — voce {i}\n" for i in range(5000)),
            "utf-8",
        )

        delta = read_journal_delta(root, GardenerState())

        assert delta.line_count == MAX_DELTA_LINES
        # Le altre non sono perse: sono dichiarate, e tornano al giro dopo. È la
        # metà del contratto che il ramo ha già pagato due volte per averla
        # ignorata (troncare zitti).
        assert delta.left_behind == 5000 - MAX_DELTA_LINES
        # E il numero è un numero da telefono. Largo, per non fissare una seconda
        # copia del tetto qui dentro, ma un ordine di grandezza è un'affermazione:
        # mille voci di diario in un prompt non sono un tetto.
        assert delta.line_count < 1000

    def test_the_inventory_cap_binds_with_a_corpus_that_does_not_scale(self, tmp_path):
        """500 pagine, un numero scritto qui e non derivato dal tetto."""
        from jenny.agent.gardener import _MAX_INVENTORY_ENTRIES

        root = _project(tmp_path)
        for i in range(500):
            _page_of(root, f"p{i:04d}.md", 200)

        inventory = _store(tmp_path).build_inventory()
        entries = [line for line in inventory.splitlines() if line.startswith("- `")]

        assert len(entries) == _MAX_INVENTORY_ENTRIES
        assert len(entries) < 500, "il tetto non ha tagliato niente"
        assert "list truncated: 500 pages in all" in inventory


# ── La data del registro ─────────────────────────────────────────────────────


class _AdvancingClock:
    """Un orologio che avanza a ogni lettura, e che **conta** le letture.

    L'orologio vero non serve a niente qui: le due letture di ``log_pass``
    cadevano nello stesso secondo, quindi la mezzanotte non la attraversavano mai
    e il difetto era invisibile (è la trappola che T2.5 e T2.11 hanno già pagato).
    Con un orologio che avanza, la seconda lettura — se qualcuno la rimette — cade
    nel giorno dopo, e si vede.
    """

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=45)) -> None:
        self._at = start
        self._step = step
        self.reads = 0

    def __call__(self) -> datetime:
        stamp = self._at
        self._at = self._at + self._step
        self.reads += 1
        return stamp


def _log_pages(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "log").glob("*.md"))


def _two_zones() -> tuple[str, str]:
    """Due fusi a **25 ore** di distanza: le loro date locali non coincidono mai.

    Venticinque ore e non una: a distanza minore esiste un istante in cui i due
    fusi stanno nello stesso giorno, e un test che ci cade sopra passa per caso.
    """
    from datetime import timezone as _tz

    from jenny.utils.helpers import safe_zoneinfo

    east, west = "Pacific/Kiritimati", "Pacific/Niue"
    now = datetime.now(_tz.utc)
    if now.astimezone(safe_zoneinfo(east)).date() == now.astimezone(safe_zoneinfo(west)).date():
        pytest.skip("tzdata degradato: i due fusi collassano sull'offset di sistema")
    return east, west


class TestTheLogIsDatedOnce:
    """Il nome della pagina e l'ora della riga vengono dallo **stesso** istante.

    Era il difetto di B18 un piano più in su: ``date.today()`` per la pagina e
    ``datetime.now()`` per l'``## [HH:MM]``, due letture, quindi una passata a
    cavallo della mezzanotte si annotava nel registro di *ieri* — e con l'ora di
    sistema, mentre l'ora che il modello legge è quella del fuso configurato.
    """

    def test_the_page_and_the_hour_come_from_one_reading(self, tmp_path):
        """Un solo colpo d'orologio: 23:59:30 resta nel 23, non diventa il 24."""
        root = _project(tmp_path)
        clock = _AdvancingClock(datetime(2026, 8, 23, 23, 59, 30))
        # L'orologio dal **costruttore**, che è il punto d'iniezione dichiarato:
        # ``for_project`` non lo espone, e questo è l'unico test che ne ha bisogno.
        store = GardenerStore(tmp_path / "wikis" / "viaggio", tmp_path, now=clock)

        store.log_pass(store.read_delta(), elapsed=1.0, writes=1, timezone="Europe/Rome")

        # Una lettura sola: con due, la seconda cade alle 00:00:15 del giorno dopo.
        assert clock.reads == 1
        assert _log_pages(root) == ["20260823.md"]
        page = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "## [23:59]" in page, page
        assert "# 2026-08-23" in page, page

    def test_the_hour_is_the_configured_zone_and_not_the_system_one(self, tmp_path):
        """Due passate nello stesso istante, due fusi, **due pagine**.

        Nessun orologio finto: quel che si misura è che il fuso arriva
        all'orologio. A venticinque ore di distanza le due date locali sono
        diverse per costruzione, quindi il test non ha un istante fortunato.
        """
        east, west = _two_zones()
        root = _project(tmp_path)
        store = GardenerStore.for_project(tmp_path, "viaggio")
        assert store is not None
        delta = store.read_delta()

        store.log_pass(delta, elapsed=1.0, writes=1, timezone=east)
        store.log_pass(delta, elapsed=1.0, writes=1, timezone=west)

        assert len(_log_pages(root)) == 2, _log_pages(root)

    async def test_the_pass_dates_the_log_in_the_agents_zone(self, tmp_path):
        """Il cablaggio, non la firma: il fuso lo passa ``run_gardener``.

        Un test che chiama ``log_pass`` da sé prova che il parametro esiste; che
        arrivi il fuso *dell'agente* lo prova solo una passata intera — è la
        lezione di T2.5, dove la doppia lettura viveva nel cablaggio e non nella
        firma.
        """
        east, west = _two_zones()
        pages = []

        # Due progetti e non due passate sullo stesso: la prima passata registra
        # il cursore, quindi la seconda sullo stesso progetto non troverebbe
        # niente da leggere e uscirebbe **senza chiamare il provider e senza
        # scrivere il registro** — un test che passa misurando una pagina sola.
        for name, zone in (("viaggio", east), ("altro-fuso", west)):
            root = _project(tmp_path, name)
            store = GardenerStore.for_project(tmp_path, name)
            assert store is not None
            agent = _FakeAgent(tmp_path)
            agent.context.timezone = zone

            await run_gardener(agent, _with_states(store, _states(attempted=1, ok=1)))

            assert len(agent.calls) == 1, "la passata non ha girato"
            pages.append(_log_pages(root))

        assert all(len(p) == 1 for p in pages), pages
        assert pages[0] != pages[1], pages
