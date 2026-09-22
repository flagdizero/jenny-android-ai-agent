"""Discovery delle wiki (``jenny/utils/wiki_paths.py``).

Quel che il picker, il blocco ``## Wikis`` del prompt e il giardiniere leggono
per sapere quali wiki esistono e come si presentano. I due confini che questi
test fissano: cosa e' una wiki, e cosa e' una pagina.
"""

from __future__ import annotations

import os
from pathlib import Path

from jenny.utils.wiki_paths import (
    discover_wiki_roots,
    discover_wikis,
    is_wiki_page_rel,
    iter_wiki_pages,
    read_wiki_scope,
    wiki_schema_file,
)


def _make_wiki(wikis_dir: Path, name: str, *, pages: dict[str, str] | None = None) -> Path:
    root = wikis_dir / name
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(f"# {name}\n", encoding="utf-8")
    for rel, body in (pages or {}).items():
        target = root / "wiki" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def _touch_newer(path: Path) -> None:
    """Riscrive *path* forzando un mtime più recente.

    Su filesystem a granularità bassa due write consecutive possono condividere
    lo stesso mtime: qui lo spostiamo in avanti a mano invece di sperare.
    """
    path.write_text(path.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


class TestDiscovery:
    def test_finds_only_dirs_with_a_wiki_subfolder(self, tmp_path):
        wikis = tmp_path / "wikis"
        _make_wiki(wikis, "main")
        (wikis / "notawiki").mkdir()

        assert list(discover_wikis(wikis)) == ["main"]

    def test_missing_dir_is_not_an_error(self, tmp_path):
        assert discover_wikis(tmp_path / "nope") == {}

    def test_roots_point_one_level_above_pages(self, tmp_path):
        wikis = tmp_path / "wikis"
        _make_wiki(wikis, "main")

        assert discover_wiki_roots(wikis)["main"] == wikis / "main"
        assert discover_wikis(wikis)["main"] == wikis / "main" / "wiki"


class TestScope:
    def test_prefers_frontmatter_summary(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").write_text(
            "---\nsummary: AI loops and step executors\n---\n\n# main\n", encoding="utf-8"
        )

        assert read_wiki_scope(root) == "AI loops and step executors"

    def test_falls_back_to_first_scope_bullet(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").write_text(
            "# main\n\n## Scope\n\nWhat this wiki covers:\n\n- Personal projects\n- Other stuff\n",
            encoding="utf-8",
        )

        assert read_wiki_scope(root) == "Personal projects"

    def test_placeholders_do_not_count_as_scope(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").write_text(
            "---\nsummary: <what this wiki is about>\n---\n\n# main\n", encoding="utf-8"
        )

        assert read_wiki_scope(root) == "(no scope set)"

    def test_missing_schema_file_is_reported(self, tmp_path):
        """Il nome nel messaggio e' quello che una wiki dovrebbe avere *oggi*.

        Dal 22/08 il file di istruzioni si chiama ``AGENTS.md``; ``CLAUDE.md``
        resta letto nelle sette wiki che l'avevano gia' (passo 2.3), ma non e'
        piu' il nome da suggerire a chi non ce l'ha.
        """
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").unlink()

        assert read_wiki_scope(root) == "(no AGENTS.md)"


class TestQualeFileDiIstruzioni:
    """Passo 7.5: ``AGENTS.md``, e **solo** quello.

    Fino al 22/08 i lettori accettavano tutt'e due i nomi: era il ripiego che il
    passo 2 aveva accettato per non toccare cartelle vere. Il passo 7 le migra a
    ogni avvio (``utils/wiki_migration.py``), quindi il ripiego e' stato tolto —
    due nomi per lo stesso file sono due nomi da tenere allineati in ognuno dei
    quattro lettori.

    Il nome vecchio resta noto a **due** posti, e a nessun lettore: la
    migrazione, che lo rinomina, e ``wiki_id``, che deve poter leggere l'identita'
    di una wiki non ancora migrata — se non ci riuscisse, quella wiki perderebbe
    la propria chat proprio nella finestra in cui e' piu' fragile.
    """

    def test_agents_e_il_file_di_istruzioni(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").write_text("---\nsummary: il nuovo\n---\n", encoding="utf-8")
        (root / "CLAUDE.md").write_text("---\nsummary: il vecchio\n---\n", encoding="utf-8")

        assert wiki_schema_file(root).name == "AGENTS.md"
        assert read_wiki_scope(root) == "il nuovo"

    def test_claude_da_solo_non_e_piu_un_file_di_istruzioni(self, tmp_path):
        """La migrazione lo rinomina al prossimo avvio; fino a la' non si legge.

        E' il prezzo dichiarato del 7.5: una finestra piccola, che si chiude da
        se'. Il ripiego costava quattro punti da tenere allineati per sempre.
        """
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").unlink()
        (root / "CLAUDE.md").write_text("---\nsummary: il vecchio\n---\n", encoding="utf-8")

        assert wiki_schema_file(root) is None

    def test_ma_l_id_di_una_wiki_non_migrata_si_legge_ancora(self, tmp_path):
        """Altrimenti un rinomino la perderebbe proprio prima della migrazione."""
        from jenny.utils.wiki_paths import wiki_id

        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").unlink()
        (root / "CLAUDE.md").write_text("---\nid: 3f9a2c1b7e04\n---\n", encoding="utf-8")

        assert wiki_id(root) == "3f9a2c1b7e04"

    def test_senza_nessuno_dei_due_e_none(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").unlink()

        assert wiki_schema_file(root) is None

class TestElencoPagineSenzaTitolo:
    """``iter_wiki_pages(titles=False)``: gli stessi percorsi, nessuna lettura. T3.11.

    Il titolo costa un ``read_text()`` **per pagina**, e chi lo usa è una
    minoranza: lo mettono nell'elenco l'inventario del giardiniere
    (``GardenerStore.build_inventory``); ``ContextBuilder._read_project_pages`` lo
    buttava via — dentro ``build_system_prompt``, cioè sul loop dell'evento a
    ogni turno.
    """

    def _wiki(self, tmp_path):
        return _make_wiki(
            tmp_path / "wikis",
            "main",
            pages={
                "index.md": "# Index\n\n- [[entities/ada]]\n",
                "entities/ada.md": "---\ntitle: Ada Lovelace\n---\n\n# Ada\n",
                "concepts/loop.md": "# Il ciclo\n\nx",
                "senza-titolo.md": "solo testo, nessuna intestazione\n",
                "summaries/doc.md": "# Riassunto\n",
                ".nascosta.md": "# Nascosta\n",
            },
        )

    def test_gli_stessi_percorsi_nello_stesso_ordine(self, tmp_path):
        """La firma nuova non è un secondo insieme di pagine: è lo stesso elenco
        senza la colonna che costa. Se divergessero, il conteggio che il blocco
        dichiara e le pagine che inietta verrebbero da due camminate diverse.
        """
        pages = self._wiki(tmp_path) / "wiki"

        assert iter_wiki_pages(pages, titles=False) == [
            rel for rel, _title in iter_wiki_pages(pages)
        ]
        # E le esclusioni valgono per tutt'e due: ``summaries/``, l'indice, i
        # nascosti.
        assert iter_wiki_pages(pages, titles=False) == [
            "concepts/loop.md",
            "entities/ada.md",
            "senza-titolo.md",
        ]

    def test_senza_titoli_non_apre_nessun_file(self, tmp_path):
        """Il punto del passo, provato dove sta: nessuna pagina viene aperta. La
        prova è per sabotaggio — ``read_text`` alza — perché un test sui
        millisecondi misurerebbe il disco, e un test sul risultato non vedrebbe
        la differenza (è esattamente il difetto che T3.11 ha trovato).
        """
        pages = self._wiki(tmp_path) / "wiki"
        real = Path.read_text

        def boom(self, *args, **kwargs):
            raise AssertionError(f"aperta una pagina per elencarla: {self}")

        Path.read_text = boom
        try:
            assert len(iter_wiki_pages(pages, titles=False)) == 3
        finally:
            Path.read_text = real

    def test_il_titolo_arriva_ancora_a_chi_lo_usa(self, tmp_path):
        """Il default non è cambiato, ed è quel che vedono i due inventari:
        ``title:`` del frontmatter, altrimenti il primo H1, altrimenti il nome
        del file.
        """
        pages = self._wiki(tmp_path) / "wiki"

        assert dict(iter_wiki_pages(pages)) == {
            "concepts/loop.md": "Il ciclo",
            "entities/ada.md": "Ada Lovelace",
            "senza-titolo.md": "senza-titolo",
        }

    def test_una_pagina_illeggibile_non_e_un_errore(self, tmp_path):
        """Il ripiego di prima, che il rifattore non deve aver perso: un file
        illegibile prende il nome del file come titolo, non alza.
        """
        root = self._wiki(tmp_path)
        (root / "wiki" / "binaria.md").write_bytes(b"\xff\xfe\x00binario")

        assert dict(iter_wiki_pages(root / "wiki"))["binaria.md"] == "binaria"

    def test_una_cartella_che_non_esiste_torna_vuoto_in_tutt_e_due_le_forme(self, tmp_path):
        assert iter_wiki_pages(tmp_path / "nope") == []
        assert iter_wiki_pages(tmp_path / "nope", titles=False) == []


class TestCheCosaEUnaPagina:
    """T9.5. Quattro funzioni rispondevano a «questo file è una pagina?» e non
    dicevano la stessa cosa. Il difetto non era l'estetica della duplicazione:

    * ``iter_page_files`` (grafo + ricerca) **non** saltava i nascosti, quindi un
      ``.bozza.md`` sotto ``wiki/`` non arrivava al modello e non compariva
      nell'albero, ma era un nodo del grafo e un risultato di ricerca;
    * ``_walk`` (albero dei file) saltava i nascosti a **ogni** livello, cioè
      anche le *cartelle*, mentre gli altri due guardavano solo il nome del
      file: una ``wiki/.bozze/`` era invisibile all'utente e iniettata nel
      prompt a ogni turno;

    Ora la regola è una — ``is_wiki_page_rel`` — e questi test sono i primi che
    ``iter_wiki_pages`` ha di suo dopo T3.11/T3.12 (quelli provano la manopola
    ``titles`` e la costante dell'indice, non l'insieme).

    **I consumatori sono due, non più tre** (22/09/2026): l'albero dei file se
    n'è andato con ``/api/tree``, che non aveva più nessun cliente da quando la
    wiki è uscita dall'officina. La regola resta una sola; è sparito uno dei
    posti in cui poteva divergere.
    """

    def _wiki(self, tmp_path) -> Path:
        """Una wiki con un esemplare di ogni caso limite, index compreso."""
        root = _make_wiki(
            tmp_path / "wikis",
            "main",
            pages={
                "index.md": "# La mappa\n",
                "semine.md": "# Semine\n",
                "concepts/loop.md": "# Il ciclo\n",
                # Un ``index.md`` di **sottocartella** è una pagina: è la forma
                # che la skill insegna per un topic diviso in cartella, e solo
                # la mappa alla radice è esclusa. Verificato oggi, e prima di
                # oggi non lo diceva nessun test.
                "concepts/Topic/index.md": "# Topic\n",
                "concepts/Topic/aspetto.md": "# Aspetto\n",
                "summaries/doc.md": "# Riassunto di una fonte\n",
                # Una pagina che si *chiama* come la cartella di servizio non è
                # nella cartella di servizio.
                "summaries.md": "# Sui riassunti\n",
                ".bozza.md": "# Bozza\n",
                ".bozze/nota.md": "# Nota in una cartella nascosta\n",
            },
        )
        return root

    _PAGES = (
        "concepts/Topic/aspetto.md",
        "concepts/Topic/index.md",
        "concepts/loop.md",
        "semine.md",
        "summaries.md",
    )

    def test_le_pagine_sono_queste_e_non_altre(self, tmp_path):
        pages = self._wiki(tmp_path) / "wiki"

        assert tuple(iter_wiki_pages(pages, titles=False)) == self._PAGES

    def test_una_cartella_nascosta_non_e_un_posto_dove_stanno_le_pagine(self, tmp_path):
        """Il caso che nessuna delle quattro implementazioni trattava allo stesso
        modo, e l'unico che cambia comportamento per l'utente: una
        ``wiki/.qualcosa/`` — una ``.git``, un ``.obsidian``, una cartella di
        bozze — non entra più nel prompt. Il drawer file non l'ha mai mostrata,
        quindi finora il modello leggeva a ogni turno pagine che l'utente non
        vedeva.
        """
        pages = self._wiki(tmp_path) / "wiki"

        assert ".bozze/nota.md" not in iter_wiki_pages(pages, titles=False)
        assert not is_wiki_page_rel(Path(".bozze/nota.md"))
        # E il livello di sopra resta pescabile: il filtro è sul punto iniziale,
        # non sul fatto di stare in una sottocartella.
        assert is_wiki_page_rel(Path("concepts/loop.md"))

    def test_index_maiuscolo_e_una_pagina_e_non_e_un_caso(self, tmp_path):
        """**Il confronto sull'indice resta sensibile alle maiuscole.**

        Su Android — l'unico runtime che esiste, e un filesystem che le
        distingue — ``wiki/INDEX.md`` *non* è il file che l'iniettore apre come
        mappa (``context.py::_read_map_source`` chiede ``index.md``). Se lo
        escludessimo anche da qui, il suo contenuto non raggiungerebbe il
        modello in nessuno dei due modi: né come mappa né come pagina. Meglio
        una pagina in più di un file muto.

        La wiki di questo test non ha un ``index.md``, così l'asserzione vale
        anche su un filesystem che non distingue le maiuscole (dove i due nomi
        sarebbero **lo stesso file**, e il test misurerebbe il filesystem).
        """
        root = _make_wiki(tmp_path / "wikis", "main", pages={"INDEX.md": "# Mappa?\n"})

        assert iter_wiki_pages(root / "wiki", titles=False) == ["INDEX.md"]

    def test_il_grafo_vede_le_stesse_pagine_piu_la_mappa(self, tmp_path):
        """Consumatore 2: ``webui/wiki.py::iter_page_files`` (grafo e ricerca).

        L'unica differenza legittima è l'indice, e va nel verso giusto: per il
        prompt la mappa è un blocco a sé, per chi navigherà è il nodo centrale.
        """
        from jenny.webui.wiki import iter_page_files

        pages = self._wiki(tmp_path) / "wiki"

        rels = {rel for rel, _path in iter_page_files(pages)}

        assert rels == set(self._PAGES) | {"index.md"}


class TestTheIndexFilenameHasOneDefinition:
    """T3.12. ``WIKI_INDEX_FILENAME`` esisteva, e serviva a **escludere** la mappa
    dall'elenco delle pagine; chi la mappa la *apriva* si scriveva ``"index.md"``
    a mano. Cambiare la costante avrebbe fatto due cose nella stessa mossa e
    senza far cadere niente: la mappa spariva dal blocco di progetto, e
    ``index.md`` cominciava a entrare fra le pagine iniettate.

    La prova è funzionale e non testuale: si sposta il nome e si guarda se i
    consumatori lo seguono. Il nome è importato per valore, quindi si sposta in
    un punto per modulo — che è esattamente l'elenco dei posti che un rename
    dovrebbe toccare, e il motivo per cui vale la pena averlo scritto.

    **T6.13 ha aggiunto i due moduli lato web**, che T3.12 aveva lasciato fuori
    perché quel perimetro era di un altro agente. Uno dei due è il caso peggiore
    di tutti: ``webui/project_scaffold.py`` è quello che **crea** il file di cui
    tutti gli altri presuppongono il nome, quindi con una copia là dentro un
    rename della costante non rompeva un lettore — faceva nascere i progetti
    nuovi con la mappa nel posto sbagliato.
    """

    @staticmethod
    def _move_the_name(monkeypatch, new_name: str) -> None:
        from jenny.agent import context as context_mod
        from jenny.agent import gardener as gardener_mod
        from jenny.utils import wiki_paths as wiki_paths_mod
        from jenny.webui import project_scaffold as scaffold_mod
        from jenny.webui import wiki_routes as routes_mod

        for mod in (wiki_paths_mod, context_mod, gardener_mod, scaffold_mod, routes_mod):
            monkeypatch.setattr(mod, "WIKI_INDEX_FILENAME", new_name)

    @staticmethod
    def _project(tmp_path: Path) -> Path:
        root = tmp_path / "wikis" / "orto"
        (root / "wiki").mkdir(parents=True)
        (root / "AGENTS.md").write_text("# orto\n", encoding="utf-8")
        (root / "wiki" / "index.md").write_text("# La mappa\n\n- [[semine]]\n", encoding="utf-8")
        (root / "wiki" / "mappa.md").write_text("# La mappa nuova\n", encoding="utf-8")
        (root / "wiki" / "semine.md").write_text("# Semine\n", encoding="utf-8")
        return root

    def test_the_injector_reads_the_map_the_constant_names(self, tmp_path, monkeypatch):
        from jenny.agent.context import _read_map_source

        root = self._project(tmp_path)

        assert "La mappa" in _read_map_source(root)

        self._move_the_name(monkeypatch, "mappa.md")

        assert "La mappa nuova" in _read_map_source(root)

    def test_the_gardener_writes_the_map_the_constant_names(self, tmp_path, monkeypatch):
        from jenny.agent.gardener import GardenerStore

        root = self._project(tmp_path)
        store = GardenerStore(root, tmp_path)

        assert store.map_path.name == "index.md"

        self._move_the_name(monkeypatch, "mappa.md")

        assert GardenerStore(root, tmp_path).map_path.name == "mappa.md"

    def test_the_page_list_excludes_the_map_the_constant_names(self, tmp_path, monkeypatch):
        root = self._project(tmp_path)

        assert iter_wiki_pages(root / "wiki", titles=False) == ["mappa.md", "semine.md"]

        self._move_the_name(monkeypatch, "mappa.md")

        assert iter_wiki_pages(root / "wiki", titles=False) == ["index.md", "semine.md"]

    def test_the_scaffolder_creates_the_map_the_constant_names(self, tmp_path, monkeypatch):
        """Il consumatore che **scrive** il file, non uno che lo legge (T6.13)."""
        from jenny.webui.project_scaffold import scaffold_project

        root = tmp_path / "wikis" / "orto"
        root.mkdir(parents=True)

        created = scaffold_project(root, title="Orto", seed="l'orto", quoted_seed="l'orto")

        assert "wiki/index.md" in created
        assert (root / "wiki" / "index.md").is_file()

        self._move_the_name(monkeypatch, "mappa.md")
        altro = tmp_path / "wikis" / "campo"
        altro.mkdir(parents=True)

        created = scaffold_project(altro, title="Campo", seed="il campo", quoted_seed="il campo")

        assert "wiki/mappa.md" in created
        assert (altro / "wiki" / "mappa.md").is_file()
        assert not (altro / "wiki" / "index.md").exists()

    def test_the_page_route_falls_back_on_the_map_the_constant_names(self, monkeypatch):
        """``/api/page`` senza ``page=``: il default è la mappa, e la mappa è
        quella che dice la costante (T6.13). La route completa è provata in
        ``tests/webui/test_wiki_routes_server_scope.py``; qui basta il cancello
        che decide il nome."""
        from jenny.utils.wiki_paths import safe_wiki_page_path

        assert safe_wiki_page_path("") == "index.md"

        self._move_the_name(monkeypatch, "mappa.md")

        assert safe_wiki_page_path("") == "mappa.md"

    def test_no_reader_in_the_package_still_writes_the_literal(self):
        """Il complemento del test funzionale: quello prova che i consumatori di
        oggi seguono la costante, questo che non ne ricompaia un altro.

        **Due severità, e la differenza non è arbitraria.** Sui moduli lato
        agente si cerca il literal in una *join di percorso* (``/ "index.md"``):
        ``context.py`` tiene il nome dentro una riga del blocco ``## Wikis``
        (``→ wikis/<nome>/wiki/index.md``, la pista che il modello segue per
        aprire la mappa), che è un consumatore vero ma di visualizzazione. Sui due moduli lato web,
        appena ripuliti, la regola è più stretta: **nessuna stringa** che finisca
        in ``index.md``, perché là il nome non arrivava mai da una join —
        ``_write_if_absent(root, "wiki/index.md", …)`` e ``target or "index.md"``
        sarebbero passati sotto il naso della regex più larga.

        Il registro ``wikis/_index.md`` non è la mappa e non è toccato da nessuna
        delle due: ``"_index.md"`` non combacia con ``(…/)?index.md``.
        """
        import re

        repo = Path(__file__).resolve().parents[2]
        joins = re.compile(r'/\s*"index\.md"')
        literals = re.compile(r'"(?:[^"\n]*/)?index\.md"')
        for rel in ("jenny/agent/context.py", "jenny/agent/gardener.py"):
            source = (repo / rel).read_text(encoding="utf-8")
            assert not joins.search(source), f"{rel} costruisce il percorso della mappa a mano"
        for rel in ("jenny/webui/project_scaffold.py", "jenny/webui/wiki_routes.py"):
            source = (repo / rel).read_text(encoding="utf-8")
            found = literals.findall(source)
            assert not found, f"{rel} nomina la mappa a mano: {found}"
