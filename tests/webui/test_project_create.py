"""Creare un progetto dalla WebUI, e vedere i progetti che esistono.

Prima di questo lavoro il chip faceva due cose sbagliate, entrambe silenziose:
elencava `workspace/projects/`, una cartella che non esiste (i progetti **sono**
le wiki, `roadmap/project-sessions.md`), e "Nuovo progetto" chiamava
`/api/workspace/mkdir` — cartella nuda, nessun albero, nessun `AGENTS.md`,
nessuna voce nel registro. Una wiki rotta che sembrava un progetto.

Dal 22/08 (**T1** di `roadmap/taccuino-passi.md`) lo scaffolder e' nel package
(`webui/project_scaffold.py`) e costruisce il **formato nostro**: pagine piatte
sotto `wiki/`, un diario, la mappa. Il fixture monta comunque il checkout della
skill nel workspace, perche' da la' viene ancora `reindex_wikis.py` — il registro
del workspace e' comune ai due formati.
"""

from __future__ import annotations

import json
import shutil
import urllib.parse
from pathlib import Path

import pytest
from support.gateway_http import make_handler
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from jenny.webui.commands import (
    MAX_PROJECT_SEED_CHARS,
    CommandContext,
    CommandError,
    dispatch_command,
)
from jenny.webui.workspaces import WebUIWorkspaceController

_REPO = Path(__file__).resolve().parents[2]
_SKILL_SCRIPTS = _REPO / "jenny" / "skills" / "llm-wiki" / "scripts"
_AUTH_SECRET = "test-secret"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Workspace con il checkout della skill, come sul dispositivo."""
    root = tmp_path / "workspace"
    scripts = root / "skills" / "llm-wiki" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(_SKILL_SCRIPTS / "reindex_wikis.py", scripts / "reindex_wikis.py")
    return root


@pytest.fixture
def ctx(workspace: Path) -> CommandContext:
    return CommandContext(get_workspace_root=lambda: workspace, invalidate_session=lambda _key: None, busy_session_keys=lambda: ())


async def _create(ctx: CommandContext, **params) -> dict:
    return await dispatch_command(ctx, "project.create", params)


# ── quel che il pulsante deve produrre ───────────────────────────────────────


def _frontmatter(text: str) -> dict:
    """La frontmatter di *text*, parsata. Solleva se il blocco non è YAML valido —
    che è il punto: una riga di scope scritta dall'utente non deve poterlo rompere."""
    import yaml

    block = text.split("---", 2)[1]
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict), f"frontmatter non parsabile: {block!r}"
    return parsed


class TestAProjectIsBornComplete:
    async def test_creates_the_tree_the_registry_and_the_scope_line(self, ctx, workspace):
        result = await _create(ctx, name="patreon-creator", seed="Come si cresce su Patreon.")

        root = workspace / "wikis" / "patreon-creator"
        for rel in ("AGENTS.md", "wiki/index.md", "audit/.gitkeep"):
            assert (root / rel).is_file(), rel
        for rel in ("wiki", "raw/journal", "raw/research", "log", "audit/resolved"):
            assert (root / rel).is_dir(), rel
        assert result["name"] == "patreon-creator"
        assert result["seeded"] is True

        # La riga dell'utente sta dove il registro la va a prendere...
        schema = (root / "AGENTS.md").read_text(encoding="utf-8")
        # Quotata, e provata **parsando** invece di confrontando una stringa: la
        # vecchia asserzione fissava la forma non quotata, cioè esattamente il
        # difetto che il 22/08 ha fatto perdere tutta la frontmatter a una wiki
        # la cui riga di scope conteneva un due punti.
        assert _frontmatter(schema)["summary"] == "Come si cresce su Patreon."
        # ...e nella mappa, che è quel che l'agente legge per primo (T3).
        assert "Come si cresce su Patreon." in (root / "wiki" / "index.md").read_text("utf-8")
        # Nessun segnaposto: il seme entra alla nascita, non per sostituzione.
        assert "<one-line scope" not in schema
        # ...e infatti nel registro c'e'.
        registry = (workspace / "wikis" / "_index.md").read_text(encoding="utf-8")
        assert "Come si cresce su Patreon." in registry
        assert "[[patreon-creator/wiki/index|patreon-creator]]" in registry

    async def test_does_not_create_the_search_pattern_taxonomy(self, ctx, workspace):
        """T1: le cartelle che obbligavano a scegliere «concept o entity?» **mentre**
        si prende un appunto non esistono più. Il pattern document-first resta, ma
        vive nella skill e nelle sette wiki che ce l'hanno già."""
        await _create(ctx, name="nuovo", seed="x")

        root = workspace / "wikis" / "nuovo"
        for rel in ("wiki/concepts", "wiki/entities", "wiki/summaries",
                    "outputs/queries", "raw/papers", "raw/articles", "raw/refs"):
            assert not (root / rel).exists(), rel

    async def test_the_title_comes_from_the_folder_name(self, ctx, workspace):
        await _create(ctx, name="patreon-creator", seed="x")

        schema = (workspace / "wikis" / "patreon-creator" / "AGENTS.md").read_text("utf-8")
        assert "# Patreon Creator" in schema

    async def test_is_empty_of_content(self, ctx, workspace):
        """"Nuovo" costruisce lo scaffolding, non un primo articolo."""
        await _create(ctx, name="nuovo", seed="x")

        root = workspace / "wikis" / "nuovo"
        # Sotto `wiki/` c'è la mappa e nient'altro: le pagine le scrive il lavoro.
        assert [p.name for p in (root / "wiki").iterdir()] == ["index.md"]
        # Il diario nasce vuoto: la prima pagina la scrive la prima cattura, e un
        # file creato qui sarebbe il diario di un giorno in cui non si è detto niente.
        assert list((root / "raw" / "journal").iterdir()) == []
        assert list((root / "raw" / "research").iterdir()) == []

    async def test_agents_md_is_born_without_instructions_to_the_model(self, ctx, workspace):
        """``## How we work here`` nasce **vuota**: c'è il posto, non il foglietto.

        Il segnaposto che ci stava fino al 24/08 era un'istruzione a chi compila il
        file, ma questo file finisce **intero** nel prompt di sistema di ogni turno
        del progetto — intero perché `id:` e `summary:` sono già compilati, quindi
        `_is_template_content` non lo riconosce come template — e ci arriva sotto
        l'intestazione «this project's own instructions». Cioè era una regola di
        lavoro congelata al giorno della nascita del progetto, in un file che il
        giardiniere ha il divieto esplicito di riscrivere: lo stesso errore che il
        22/08 ha tolto dal resto di questo template.

        Non lascia scoperta nessuna promessa: quell'istruzione la porta
        `agent/project.md`, viva e riscritta a ogni avvio.

        Le due asserzioni sono la decisione presa da due lati — senza la seconda,
        togliere l'intestazione insieme al testo passerebbe.
        """
        await _create(ctx, name="nuovo", seed="di cosa si tratta")

        schema = (workspace / "wikis" / "nuovo" / "AGENTS.md").read_text("utf-8")

        assert "## How we work here" in schema, "il posto resta: dice dove si scrive"
        assert "<" not in schema.split("## How we work here", 1)[1], (
            "e sotto non c'è niente: nessun segnaposto fra parentesi angolari"
        )

    async def test_the_map_is_born_with_its_sections(self, ctx, workspace):
        """Le sezioni nascono vuote ma nascono: il giardiniere (T4) aggiorna
        sezioni che esistono, invece di inventarsi una struttura ogni volta — che è
        il modo in cui due sessioni diverse producono due mappe diverse."""
        await _create(ctx, name="nuovo", seed="di cosa si tratta")

        map = (workspace / "wikis" / "nuovo" / "wiki" / "index.md").read_text("utf-8")
        for section in ("## Decided", "## Open", "## Pages"):
            assert section in map, section
        # Il diario è citato come percorso, **non** come `[[link]]`: sta fuori da
        # `wiki/`, e un wikilink fuori dalle pagine è morto per `resolve_wikilink`.
        assert "raw/journal" in map
        assert "[[raw/journal" not in map

    async def test_rerunning_it_rewrites_nothing(self, ctx, workspace):
        """Lo scaffolder scrive solo quel che manca: è la regola che rende sicuro
        ripassare su una cartella rimasta a metà."""
        from jenny.webui.project_scaffold import scaffold_project

        await _create(ctx, name="nuovo", seed="x")
        root = workspace / "wikis" / "nuovo"
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

        assert scaffold_project(root, "Nuovo", "x", '"x"') == []
        assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before

    async def test_an_existing_wiki_is_not_touched(self, ctx, workspace):
        """Il secondo progetto non deve poter riscrivere il primo."""
        await _create(ctx, name="primo", seed="il primo")
        before = (workspace / "wikis" / "primo" / "wiki" / "index.md").read_bytes()

        await _create(ctx, name="secondo", seed="il secondo")

        assert (workspace / "wikis" / "primo" / "wiki" / "index.md").read_bytes() == before


# ── un progetto rimasto a metà si può finire ─────────────────────────────────


class TestAHalfDoneProjectCanBeFinished:
    """Il top-up dello scaffolder, raggiungibile dalla UI.

    Lo scaffolder è scritto per essere rilanciato (`_write_if_absent` su ogni
    file, `wiki/` creata **per prima** di proposito così un albero morto a metà
    resta visibile al picker), ma il comando rifiutava appena la cartella
    esisteva: quel comportamento non era raggiungibile da nessuna parte. Il
    risultato era una cartella elencata, senza mappa, e irreparabile — su un
    telefono l'utente non ci arriva a mano.
    """

    async def test_a_folder_with_only_the_wiki_gets_completed(self, ctx, workspace):
        (workspace / "wikis" / "morta-a-meta" / "wiki").mkdir(parents=True)

        result = await _create(ctx, name="morta-a-meta", seed="di cosa si tratta")

        root = workspace / "wikis" / "morta-a-meta"
        for rel in ("AGENTS.md", "wiki/index.md", "audit/.gitkeep"):
            assert (root / rel).is_file(), rel
        for rel in ("raw/journal", "raw/research", "log", "audit/resolved"):
            assert (root / rel).is_dir(), rel
        assert result["seeded"] is True
        schema = (root / "AGENTS.md").read_text("utf-8")
        assert _frontmatter(schema)["summary"] == "di cosa si tratta"

    async def test_the_seed_gets_in_even_if_startup_already_wrote_the_minimal_agents(
        self, ctx, workspace
    ):
        """Il caso vero, e quello che si perdeva in silenzio.

        Fra il crash e il ritentativo c'è un riavvio, e la migrazione
        (`utils/wiki_migration.py`) scrive un `AGENTS.md` **minimo** a ogni
        cartella che contiene `wiki/`: id più segnaposto. Lo scaffolder, che non
        riscrive quel che c'è, lascerebbe il segnaposto — progetto completo e
        senza scopo, cioè la cosa per cui la riga viene chiesta.
        """
        from jenny.utils.wiki_migration import migrate_wikis

        (workspace / "wikis" / "morta-a-meta" / "wiki").mkdir(parents=True)
        migrate_wikis(workspace / "wikis")
        schema = workspace / "wikis" / "morta-a-meta" / "AGENTS.md"
        before = _frontmatter(schema.read_text("utf-8"))
        assert before["summary"].startswith("<"), "il fixture non riproduce il segnaposto"

        result = await _create(ctx, name="morta-a-meta", seed="Come si cresce su Patreon.")

        after = _frontmatter(schema.read_text("utf-8"))
        assert after["summary"] == "Come si cresce su Patreon."
        assert result["seeded"] is True
        # L'id scritto dalla migrazione resta quello: è l'identità della wiki, e
        # riscriverlo staccherebbe la cartella dalla sua chat.
        assert after["id"] == before["id"]
        # E il registro adesso la vede con la sua riga, non con «(no scope set)».
        registry = (workspace / "wikis" / "_index.md").read_text("utf-8")
        assert "Come si cresce su Patreon." in registry

    async def test_a_real_scope_is_not_rewritten(self, ctx, workspace):
        """Completare un progetto non è riscriverne lo scope: se la creazione era
        morta *fra* l'`AGENTS.md` e la mappa, la riga della prima volta è un dato
        dell'utente e vince su quella di stavolta."""
        await _create(ctx, name="quasi", seed="la riga di prima")
        root = workspace / "wikis" / "quasi"
        (root / "wiki" / "index.md").unlink()

        result = await _create(ctx, name="quasi", seed="la riga di stavolta")

        assert _frontmatter((root / "AGENTS.md").read_text("utf-8"))["summary"] == "la riga di prima"
        assert result["seeded"] is False
        # La mappa invece nasce adesso, quindi porta la riga di stavolta.
        assert "la riga di stavolta" in (root / "wiki" / "index.md").read_text("utf-8")

    async def test_a_folder_that_is_not_a_project_is_a_different_refusal(self, ctx, workspace):
        """Due rifiuti distinguibili: "ce l'hai già" non è "c'è qualcosa di mezzo".

        Il client interpola il messaggio del server nel toast (`scope.createFailed`),
        quindi la differenza arriva all'utente — e le due cose si risolvono in
        due modi diversi: aprire il progetto, o rinominare la cartella.
        """
        (workspace / "wikis" / "intrusa").mkdir(parents=True)
        (workspace / "wikis" / "intrusa" / "roba.txt").write_text("x", encoding="utf-8")

        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="intrusa", seed="x")

        assert exc.value.code == "bad_request"
        assert "already exists" not in exc.value.message
        assert "not a project" in exc.value.message
        # E non ci ha scaffoldato dentro niente.
        assert not (workspace / "wikis" / "intrusa" / "wiki").exists()


# ── quel che il comando deve rifiutare ───────────────────────────────────────


class TestTheGateIsOnTheServer:
    async def test_rejects_a_project_that_exists(self, ctx):
        await _create(ctx, name="patreon", seed="uno")

        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="patreon", seed="due")
        assert exc.value.code == "bad_request"
        # Il messaggio del progetto completo non cambia: è l'altro ramo che ne ha
        # uno nuovo, e i due devono restare distinguibili dal client.
        assert exc.value.message == "project already exists: patreon"

    @pytest.mark.parametrize(
        "name",
        [
            "../fuori",           # traversal
            "sotto/cartella",     # separatore
            ".nascosto",          # cartella nascosta dentro wikis/
            "..",
            "",
            "   ",
            "a" * 65,             # oltre il tetto
        ],
    )
    async def test_rejects_an_invalid_name(self, ctx, workspace, name):
        with pytest.raises(CommandError) as exc:
            await _create(ctx, name=name, seed="x")
        assert exc.value.code == "bad_request"
        # E non lascia niente per terra: il gate scatta prima del filesystem.
        assert not (workspace / "wikis").exists()

    @pytest.mark.parametrize("seed", ["", "   ", "\n\n"])
    async def test_rejects_a_project_without_a_scope_line(self, ctx, workspace, seed):
        """*"Devi scrivere tu qualcosa, sennò la chat è ferma"* — e' una regola,
        non un suggerimento del dialogo: vale anche per un client che non chiede."""
        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="muto", seed=seed)
        assert exc.value.code == "bad_request"
        assert not (workspace / "wikis").exists()

    async def test_rejects_a_line_too_long(self, ctx):
        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="lungo", seed="x" * (MAX_PROJECT_SEED_CHARS + 1))
        assert exc.value.code == "too_large"

    async def test_folds_newlines_instead_of_failing(self, ctx, workspace):
        """Il frontmatter e' YAML: una riga sola. Su una tastiera mobile un
        a-capo di troppo non deve costare un errore."""
        await _create(ctx, name="multi", seed="prima riga\nseconda   riga\n")

        schema = (workspace / "wikis" / "multi" / "AGENTS.md").read_text("utf-8")
        assert _frontmatter(schema)["summary"] == "prima riga seconda riga"

    async def test_a_workspace_without_the_skill_still_creates_the_project(self, tmp_path: Path):
        """Fino al 22/08 questo era un errore: lo scaffolder stava nel checkout
        della skill, quindi un workspace senza `skills/` non poteva creare niente.
        Ora lo scaffolder e' nel package e dalla skill viene solo `reindex_wikis`,
        che aggiorna il **registro**: se manca, il progetto nasce completo e il
        registro resta indietro — un inconveniente che `lint --workspace` ripara,
        non un fallimento della creazione."""
        empty = tmp_path / "vuoto"
        empty.mkdir()
        ctx = CommandContext(get_workspace_root=lambda: empty, invalidate_session=lambda _key: None, busy_session_keys=lambda: ())

        result = await _create(ctx, name="x", seed="y")

        assert result["registry"] is None
        assert (empty / "wikis" / "x" / "wiki" / "index.md").is_file()
        assert (empty / "wikis" / "x" / "raw" / "journal").is_dir()


# ── l'elenco che il chip legge ───────────────────────────────────────────────


@pytest.fixture
def handler(workspace: Path, monkeypatch):
    from jenny.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)
    # Controller vero e non un mock: la route ci legge lo scope da mettere nel
    # payload, ed e' proprio quello che questi test verificano.
    workspaces = WebUIWorkspaceController(
        session_manager=None,
        default_workspace=workspace,
        default_restrict_to_workspace=True,
    )
    return make_handler(workspace, workspaces=workspaces)


async def _get_projects(handler) -> dict:
    request = WsRequest(path=f"/api/projects?token={_AUTH_SECRET}", headers=Headers())
    response = await handler.wiki_routes.dispatch(request, "/api/projects")
    assert response is not None, "route not registered"
    assert response.status_code == 200, response.body
    return json.loads(response.body.decode("utf-8"))


class TestListProjects:
    async def test_lists_the_wikis_and_not_a_projects_folder(self, handler, ctx, workspace):
        await _create(ctx, name="alpha", seed="a")
        await _create(ctx, name="beta", seed="b")
        # Una cartella `projects/` accanto: non deve entrare nell'elenco.
        (workspace / "projects" / "fantasma").mkdir(parents=True)

        payload = await _get_projects(handler)

        assert payload["dir"] == "wikis"
        assert [p["name"] for p in payload["projects"]] == ["alpha", "beta"]
        assert all(isinstance(p["modified"], int) for p in payload["projects"])

    async def test_a_folder_without_wiki_is_not_a_project(self, handler, ctx, workspace):
        await _create(ctx, name="vera", seed="v")
        (workspace / "wikis" / "solo-una-cartella").mkdir()

        payload = await _get_projects(handler)

        assert [p["name"] for p in payload["projects"]] == ["vera"]

    async def test_no_project_is_not_an_error(self, handler):
        payload = await _get_projects(handler)
        assert payload["projects"] == []

    async def test_every_project_carries_its_page_count(
        self, handler, ctx, workspace
    ):
        """La pastiglia «N pagine» dell'intestazione legge di qui.

        Sta su questa route e non su una sua perche' e' l'unica che l'elenco
        chiama gia'; l'alternativa — ``/api/graph`` — e' la stessa cifra dentro
        una risposta che porta anche l'indice full-text.

        Il conteggio e' **ricorsivo** — le wiki vere mettono le pagine in
        ``concepts/`` ed ``entities/``, e un conteggio piatto direbbe zero
        proprio per i nove quaderni su quattordici che li usano — e conta con
        la regola di chi quelle pagine le elenca: ``summaries/`` resta fuori.
        Con una ``rglob`` nuda la porta diceva «7» e la stanza dietro mostrava
        sei righe.
        """
        await _create(ctx, name="alpha", seed="a")
        pages = workspace / "wikis" / "alpha" / "wiki"
        (pages / "concepts").mkdir(parents=True, exist_ok=True)
        (pages / "concepts" / "uno.md").write_text("# uno", encoding="utf-8")
        (pages / "concepts" / "due.md").write_text("# due", encoding="utf-8")
        # Il livello di citazione: non e' una pagina, e non entra nel grafo.
        (pages / "summaries").mkdir(parents=True, exist_ok=True)
        (pages / "summaries" / "fonte.md").write_text("# fonte", encoding="utf-8")
        # Fuori dalla pages-dir: la fonte grezza non e' una pagina.
        (workspace / "wikis" / "alpha" / "raw").mkdir(parents=True, exist_ok=True)
        (workspace / "wikis" / "alpha" / "raw" / "scarto.md").write_text("x", encoding="utf-8")

        payload = await _get_projects(handler)

        entry = next(p for p in payload["projects"] if p["name"] == "alpha")
        all = sum(1 for _ in pages.rglob("*.md"))
        assert entry["pages"] == all - 1, (
            f"il riassunto e' stato contato: {entry['pages']} su {all} file"
        )
        assert entry["pages"] >= 3, payload

    async def test_serve_il_token(self, handler):
        request = WsRequest(path="/api/projects", headers=Headers())
        response = await handler.wiki_routes.dispatch(request, "/api/projects")
        assert response is not None and response.status_code == 401


class TestAListedNameIsAnOpenableName:
    """Il chip non può offrire una cartella che il canale poi rifiuta.

    `project.create` la regex la applica, quindi da lì una cartella così non
    nasce; ma una cartella sotto `wikis/` può arrivare da qualunque altra parte —
    l'agente con `write_file`, lo scaffolder della skill, un rinomino fuori da
    Jenny, un ripristino da backup. E allora `_collect_projects` la elencava e
    `_envelope_chat_id` la dirottava sulla chat personale: scope
    `default()`, `session_kind` `personal` (cioè dentro `MEMORY.md`) e la
    trascrizione personale servita nella schermata del progetto.
    """

    @staticmethod
    def _wiki(workspace: Path, name: str) -> None:
        """Una wiki minima col nome dato: quel che `discover_wikis` cerca."""
        pages = workspace / "wikis" / name / "wiki"
        pages.mkdir(parents=True)
        (pages / "index.md").write_text(f"# {name}\n", encoding="utf-8")

    @pytest.mark.parametrize(
        "name",
        ["Ricerca ETF", "università", "perché", "progetto (2026)", ".nascosto", "x" * 65],
    )
    async def test_a_name_that_cannot_be_a_session_is_not_openable(
        self, handler, workspace, name
    ):
        self._wiki(workspace, name)
        self._wiki(workspace, "buona")

        payload = await _get_projects(handler)

        assert [p["name"] for p in payload["projects"]] == ["buona"]
        # **Non sparisce.** Su un telefono non c'è un file manager per
        # rinominarla: la sola strada è chiederlo all'agente, e per chiederlo
        # l'utente deve sapere che quella cartella c'è.
        assert [p["name"] for p in payload["unopenable"]] == [name]
        assert payload["unopenable"][0]["reason"] == "invalid_name"
        assert isinstance(payload["unopenable"][0]["modified"], int)

    async def test_a_valid_name_stays_openable(self, handler, ctx, workspace):
        await _create(ctx, name="alpha", seed="a")
        self._wiki(workspace, "b.eta_1-2")

        payload = await _get_projects(handler)

        assert [p["name"] for p in payload["projects"]] == ["alpha", "b.eta_1-2"]
        assert payload["unopenable"] == []

    async def test_every_listed_name_is_accepted_by_the_channel(self, handler, workspace):
        """Il contratto, dai due lati: quel che la route offre, il canale apre.

        Il difetto non era in nessuno dei due punti da solo — era che facevano
        domande diverse. Questo test le fa fare la stessa.
        """
        from jenny.channels.websocket import WebSocketChannel

        for name in ("buona", "Ricerca ETF", "università", "altra_1"):
            self._wiki(workspace, name)

        payload = await _get_projects(handler)

        for project in payload["projects"]:
            envelope = {"type": "message", "chat_id": f"project:{project['name']}"}
            assert WebSocketChannel._envelope_chat_id(envelope) == envelope["chat_id"]
        for project in payload["unopenable"]:
            envelope = {"type": "message", "chat_id": f"project:{project['name']}"}
            assert WebSocketChannel._envelope_chat_id(envelope) is None


# ── l'altro creatore di cartelle: lo scaffolder della skill ─────────────────


def _scaffold_module():
    """``scaffold.py`` della skill, importato dal checkout in ``jenny/skills/``.

    Non fa parte del package importabile e si importa ``reindex_wikis`` da se',
    quindi la dir ``scripts/`` va su ``sys.path`` — come in
    ``tests/skills/llm_wiki/test_scripts.py``, che fa lo stesso inserimento.
    """
    import sys

    if str(_SKILL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SKILL_SCRIPTS))
    import scaffold

    return scaffold


class TestTheScaffolderWarns:
    """`project.create` applica la regex; lo scaffolder della skill no.

    È la seconda porta per cui una cartella non apribile entra in `wikis/`, e
    l'agente la usa quando la wiki nasce da una conversazione invece che dal
    chip. Non può rifiutare — gira anche in top-up su wiki che esistono, e una
    wiki col nome sbagliato è proprio quella che ha più bisogno di essere
    riparata — quindi avvisa e continua.
    """

    @pytest.mark.parametrize("name", ["Ricerca ETF", "università", "progetto (2026)", ".x"])
    def test_warns_about_a_name_that_cannot_be_a_chat(self, tmp_path, name, capsys):
        scaffold = _scaffold_module()

        assert scaffold._warn_if_unopenable(str(tmp_path / "wikis" / name)) is True
        err = capsys.readouterr().err
        assert name in err and "cannot be a project chat name" in err

    @pytest.mark.parametrize("name", ["ricerca-etf", "b.eta_1", "X2"])
    def test_is_silent_on_a_good_name(self, tmp_path, name, capsys):
        scaffold = _scaffold_module()

        assert scaffold._warn_if_unopenable(str(tmp_path / "wikis" / name)) is False
        assert "cannot be a project chat name" not in capsys.readouterr().err

    def test_the_full_scaffold_warns_and_creates_anyway(self, tmp_path, capsys):
        scaffold = _scaffold_module()
        root = tmp_path / "wikis" / "Ricerca ETF"

        scaffold.scaffold(str(root), "Ricerca ETF")

        captured = capsys.readouterr()
        assert "cannot be a project chat name" in captured.err
        assert (root / "wiki" / "index.md").is_file()

    def test_the_copied_regex_does_not_diverge_from_the_canonical_one(self):
        """La copia è deliberata (lo script non può importare `jenny`), quindi il
        test è il solo posto che tiene le due in pari."""
        from jenny.session.keys import is_valid_project_name

        scaffold = _scaffold_module()
        for name in [
            "a", "X2", "b.eta_1-2", "x" * 64,
            "Ricerca ETF", "università", ".nascosto", "-x", "", "x" * 65, "a..b",
            "x\n",  # ``$`` combacia anche prima di un a capo finale: ``\Z`` no
        ]:
            copied = bool(scaffold._VALID_WIKI_NAME.match(name)) and ".." not in name
            assert copied == is_valid_project_name(name), name


# ── il chip non deve tornare a leggere la cartella sbagliata ─────────────────


def test_the_chip_no_longer_reads_a_projects_folder():
    """Guardia contro il ritorno del difetto: il chip elencava
    `workspace/projects/` con `listWorkspace`, e quella cartella non esiste."""
    source = (
        _REPO / "jenny" / "templates" / "ui" / "assets" / "shared" / "scope-chip.js"
    ).read_text(encoding="utf-8")

    assert "listWorkspace" not in source
    assert "createWorkspaceFolder" not in source
    assert "api.listProjects()" in source
    # La creazione non la fa più il chip: la fa il giro condiviso, che è anche
    # quello che usa il pannello della casa.
    flow = (
        _REPO / "jenny" / "templates" / "ui" / "assets" / "shared" / "project-create.js"
    ).read_text(encoding="utf-8")
    assert "rpc.createProject(" in flow
    assert "createProjectFlow(" in source


# ── aprire la chat di un progetto ────────────────────────────────────────────


async def _get_thread(handler, key: str):
    quoted = urllib.parse.quote(key, safe="")
    request = WsRequest(
        path=f"/api/sessions/{quoted}/webui-thread?token={_AUTH_SECRET}", headers=Headers()
    )
    return handler._handle_webui_thread_get(request, quoted)


class TestTheProjectThread:
    """La route che serve la conversazione disegnata.

    La sua guardia accettava solo chiavi `websocket:*`, quindi avrebbe risposto
    **404 a ogni progetto**: la chat di un progetto non si sarebbe potuta aprire,
    e il sintomo sarebbe stato una schermata vuota senza errori nel client.
    """

    async def test_a_project_key_is_no_longer_404(self, handler, ctx, workspace):
        await _create(ctx, name="patreon", seed="di cosa si occupa")

        response = await _get_thread(handler, "project:patreon")

        assert response.status_code != 404, response.body

    async def test_the_payload_carries_the_project_folder(self, handler, ctx, workspace):
        """E la porta **prima del primo messaggio**.

        Il chip legge lo scope da qui: leggendolo dai metadati della sessione —
        che non esistono finché non si scrive — un progetto appena aperto avrebbe
        mostrato "sessione personale" sopra il composer. Cioè esattamente la cosa
        che il chip esiste per non fare.
        """
        await _create(ctx, name="patreon", seed="di cosa si occupa")

        response = await _get_thread(handler, "project:patreon")
        payload = json.loads(response.body.decode("utf-8"))

        scope = payload["workspace_scope"]
        assert scope["project_name"] == "patreon"
        assert scope["project_path"].endswith("wikis/patreon")
        assert scope["access_mode"] == "restricted"

    async def test_an_internal_session_stays_unreadable(self, handler):
        """Il lato del confine che non doveva allargarsi."""
        for key in ("cron:job-1", "subagent:L1", "heartbeat", "dream:20260821"):
            response = await _get_thread(handler, key)
            assert response.status_code == 404, key


class TestTheLogDoesNotStealAnyonesStdout:
    """T9.4/G4. La rigenerazione del registro girava dentro un
    ``contextlib.redirect_stdout``, e ``create_project`` gira dentro un
    ``asyncio.to_thread``: ``redirect_stdout`` muta ``sys.stdout`` **di
    processo**, quindi per tutta quella finestra l'output di *ogni altro* thread
    finiva nel buffer che veniva buttato.

    Non è teorico. ``python_exec`` cattura quel che il codice del modello stampa
    proprio via ``sys.stdout``, e ha sostituito il proprio ``redirect_stdout``
    con un proxy per-thread esattamente per questa ragione (v. il commento
    «cattura di stdout PER THREAD» in ``agent/tools/python_exec.py``): il proxy
    però si consulta al momento della scrittura, e chi scrive legge
    ``sys.stdout``. Con una ``StringIO`` al suo posto, un ``print()`` del
    modello nel turno accanto spariva in silenzio.

    E non c'era niente da nascondere: ``regenerate_index`` stampa una riga sola,
    su **stderr**, che un ``redirect_stdout`` non tocca nemmeno.
    """

    @staticmethod
    def _fake_reindex(workspace: Path) -> None:
        """Un ``reindex_wikis.py`` che stampa **da un altro thread** mentre gira.

        È il turno accanto, reso deterministico: il thread parte e finisce
        dentro la finestra in cui il redirect esisteva, quindi non c'è nessuna
        corsa da vincere.
        """
        scripts = workspace / "skills" / "llm-wiki" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "reindex_wikis.py").write_text(
            "import threading\n"
            "\n"
            "def regenerate_index(wikis_dir):\n"
            "    def un_altro_turno():\n"
            "        print('OUTPUT-DI-UN-ALTRO-THREAD')\n"
            "    t = threading.Thread(target=un_altro_turno)\n"
            "    t.start()\n"
            "    t.join()\n"
            "    return wikis_dir / '_index.md'\n",
            encoding="utf-8",
        )

    async def test_a_print_from_another_thread_does_not_end_up_in_the_buffer(
        self, ctx, workspace, capsys
    ):
        self._fake_reindex(workspace)

        result = await _create(ctx, name="orto", seed="cosa semino")

        assert result["registry"].endswith("_index.md")
        assert "OUTPUT-DI-UN-ALTRO-THREAD" in capsys.readouterr().out

    async def test_and_the_project_is_still_born_complete(self, ctx, workspace):
        """La guardia d'assenza: il fix è una riga *togliata*, e un test che
        guarda solo lo stdout resterebbe verde anche se la rigenerazione del
        registro fosse saltata del tutto.
        """
        self._fake_reindex(workspace)

        result = await _create(ctx, name="orto", seed="cosa semino")

        assert (workspace / "wikis" / "orto" / "wiki" / "index.md").is_file()
        assert result["seeded"] is True
