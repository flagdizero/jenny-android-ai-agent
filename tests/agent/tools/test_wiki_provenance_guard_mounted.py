"""La guardia di provenienza vale per **chi scrive**, non per chi se la monta.

Il difetto, misurato sul telefono il 26/08/2026. ``_provenance_guard`` esisteva
dal 24/08 e stava in un posto solo: il gancio composto di ``run_gardener``. Cioè
la passata con **meno** contesto — nomi di pagina, non corpi — era l'unica
trattenuta, e la conversazione, che ha i corpi, la giornata intera e la libertà
di ristrutturare, non era trattenuta affatto.

Quel giorno in ``wikis/salute`` una richiesta di sistemare la wiki ha fatto un
buon lavoro e, dentro, ha riscritto la ``source:`` di ``riattivazione-fisica.md``
come lista YAML a due voci. I due lettori che la interpretano hanno dato due
risposte diverse — ``_page_frontmatter`` la prima voce **col trattino attaccato**
(un percorso che non risolve), il ``parse_frontmatter`` del lint la seconda — e la
provenienza di quella pagina è diventata illeggibile senza che niente lo dicesse.
Nessun rifiuto era possibile: la pagina era a ``state: open``, e tutto il gancio
si pronunciava solo verso l'alto.

**Quel che questi test provano è il montaggio**, non la funzione (quella ha già
``test_gardener_provenance.py``): che il rifiuto arrivi passando dai tool veri,
per tutti e tre, e che fuori da una ``wiki/`` di progetto non arrivi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.apply_patch import ApplyPatchTool
from jenny.agent.tools.filesystem import EditFileTool, WriteFileTool

# Le tre parole che distinguono i due rifiuti. Meno di una frase intera, così la
# frase si può riscrivere; abbastanza da non confondere una forma con uno stato.
LIST_REFUSED = "`source:` is one value"
STATE_REFUSED = "can carry a state above `open`"

# Il caso di campo, alla lettera: due voci, la prima una riga di diario vera.
LIST_SOURCE_PAGE = (
    "---\n"
    "state: open\n"
    "source:\n"
    "  - raw/journal/20260826.md#11:00\n"
    "  - raw/research/evidenze.md\n"
    "---\n"
    "\n"
    "# Riattivazione fisica\n"
)


@pytest.fixture
def ws(tmp_path: Path):
    """Un workspace con un progetto vero: diario, ricerca, mappa.

    ``raw/journal/`` con una riga ``[said]`` serve a togliere di mezzo l'altra
    metà del gancio: se il rifiuto arriva, arriva per la **forma** e non perché
    la riga non regge.
    """
    root = tmp_path / "workspace"
    project = root / "wikis" / "salute"
    (project / "wiki").mkdir(parents=True)
    (project / "raw" / "journal").mkdir(parents=True)
    (project / "raw" / "research").mkdir(parents=True)
    (project / "raw" / "journal" / "20260826.md").write_text(
        "# 2026-08-26\n\n- 11:00 — [said] Camminata spezzata, la mattina.\n",
        encoding="utf-8",
    )
    (project / "raw" / "research" / "evidenze.md").write_text(
        "# Evidenze\n\nCopiato verbatim da fuori.\n", encoding="utf-8"
    )
    (project / "wiki" / "index.md").write_text("# Salute\n\n## Pages\n", encoding="utf-8")
    (root / "memory").mkdir()
    return root, project


class TestTheMount:
    """Tutti e tre i tool di scrittura, perché tutti e tre l'hanno fatto.

    Il funnel è uno (``_FsTool._check_write_size``), ma è esattamente il genere di
    proprietà che si rompe quando qualcuno aggiunge una quarta strada: il costo di
    provarla tre volte è un ``for``.
    """

    async def test_write_file(self, ws) -> None:
        root, project = ws
        page = project / "wiki" / "riattivazione-fisica.md"

        result = await WriteFileTool(workspace=root, allowed_dir=root).execute(
            path=str(page), content=LIST_SOURCE_PAGE
        )

        assert LIST_REFUSED in result
        assert not page.exists(), "un rifiuto che scrive comunque non è un rifiuto"

    async def test_edit_file(self, ws) -> None:
        root, project = ws
        page = project / "wiki" / "riattivazione-fisica.md"
        good = LIST_SOURCE_PAGE.replace(
            "source:\n  - raw/journal/20260826.md#11:00\n  - raw/research/evidenze.md",
            "source: raw/journal/20260826.md#11:00",
        )
        page.write_text(good, encoding="utf-8")

        result = await EditFileTool(workspace=root, allowed_dir=root).execute(
            path=str(page),
            old_text="source: raw/journal/20260826.md#11:00",
            new_text="source:\n  - raw/journal/20260826.md#11:00\n  - raw/research/evidenze.md",
        )

        assert LIST_REFUSED in result
        assert page.read_text(encoding="utf-8") == good

    async def test_apply_patch(self, ws) -> None:
        root, project = ws
        page = project / "wiki" / "nuova.md"

        result = await ApplyPatchTool(workspace=root, allowed_dir=root).execute(
            edits=[{"path": str(page), "action": "add", "new_text": LIST_SOURCE_PAGE}]
        )

        assert LIST_REFUSED in result
        assert not page.exists()


class TestWhereItMustNotReach:
    """I tre silenzi, e sono la ragione per cui il gancio può essere universale.

    Un gancio montato in ``_FsTool`` vede **ogni** scrittura del repo. Se parlasse
    fuori dalle pagine di progetto sarebbe un rifiuto in mezzo a ``memory/``, e
    quello lo si scopre in produzione.
    """

    async def test_a_file_outside_a_project(self, ws) -> None:
        """Le stesse parole in ``memory/`` non sono una pagina di wiki."""
        root, _ = ws
        target = root / "memory" / "MEMORY.md"

        result = await WriteFileTool(workspace=root, allowed_dir=root).execute(
            path=str(target), content=LIST_SOURCE_PAGE
        )

        assert LIST_REFUSED not in result
        assert target.exists()

    async def test_the_map(self, ws) -> None:
        """``index.md`` non ha ``state:`` e ha un tetto suo: fuori per contratto.

        L'esclusione non è scritta qui — è quella di ``wiki_page_rel``, che è la
        definizione canonica di «pagina che il blocco di progetto inietta».
        """
        root, project = ws

        result = await WriteFileTool(workspace=root, allowed_dir=root).execute(
            path=str(project / "wiki" / "index.md"), content=LIST_SOURCE_PAGE
        )

        assert LIST_REFUSED not in result

    async def test_a_single_source_at_open(self, ws) -> None:
        """Il contro-limite: senza questo, il gancio potrebbe rifiutare tutto.

        È la forma normale — una pagina appena promossa, ``open``, ancorata a una
        riga — e passa. Le tre asserzioni sopra resterebbero verdi con un gancio
        che rifiuta ogni scrittura dentro ``wiki/``.
        """
        root, project = ws
        page = project / "wiki" / "camminata.md"

        result = await WriteFileTool(workspace=root, allowed_dir=root).execute(
            path=str(page),
            content=(
                "---\nstate: open\nsource: raw/journal/20260826.md#11:00\n---\n\n# Camminata\n"
            ),
        )

        assert LIST_REFUSED not in result
        assert STATE_REFUSED not in result
        assert page.exists()


class TestThePossibleAdvice:
    """Un rifiuto su cui non si può agire si riprova identico.

    Il 26/08 lo stesso difetto è stato trovato in due lettori: il lint diceva
    «aggiungi un ``#HH:MM``» a cinque pagine su cinque di ``salute``, la cui
    ``source:`` è un documento di ``raw/research/`` dove quel minuto non esiste. Il
    gancio in scrittura diceva la stessa cosa. Vanno corretti entrambi, ed è per
    questo che il test è qui e non solo in ``test_lint_wiki.py``.
    """

    async def test_decided_on_a_document_does_not_ask_for_the_time(self, ws) -> None:
        root, project = ws

        result = await WriteFileTool(workspace=root, allowed_dir=root).execute(
            path=str(project / "wiki" / "evidenze.md"),
            content=(
                "---\nstate: decided\nsource: raw/research/evidenze.md\n---\n\n# Evidenze\n"
            ),
        )

        assert STATE_REFUSED in result
        assert "names a document copied into `raw/`" in result
        assert "#HH:MM" not in result, (
            "su un documento quel minuto non esiste: il consiglio manda a una "
            "riparazione impossibile"
        )
        # E dice l'unica strada che c'è, invece di lasciare il modello a indovinarla.
        assert "capture it as a journal line first" in result

    async def test_decided_on_a_bare_day_still_asks_for_the_time(self, ws) -> None:
        """Il contro-limite del test sopra: dove l'ora **si può** aggiungere, si chiede.

        Senza questa asserzione la correzione potrebbe aver tolto il consiglio
        buono insieme a quello impossibile.
        """
        root, project = ws

        result = await WriteFileTool(workspace=root, allowed_dir=root).execute(
            path=str(project / "wiki" / "camminata.md"),
            content=(
                "---\nstate: decided\nsource: raw/journal/20260826.md\n---\n\n# Camminata\n"
            ),
        )

        assert STATE_REFUSED in result
        assert "#HH:MM" in result
        assert "names a document copied into `raw/`" not in result


async def test_the_injected_hook_speaks_first(ws) -> None:
    """L'ordine, e non è estetico.

    Quando il gancio iniettato è la cessione del passo del giardiniere, quel
    rifiuto è l'unica cosa vera da dire alla passata: è stata fermata perché
    l'utente è rientrato, non per una ``source:``. Un rifiuto di provenienza che
    arrivasse prima le racconterebbe un'altra storia — la proprietà che
    ``_yield_to_user_guard`` chiama «al primo rifiuto la passata è decisa».
    """
    root, project = ws
    mine = "Refused: the user is back, stop writing."

    result = await WriteFileTool(
        workspace=root, allowed_dir=root, write_size_guard=lambda _p, _t: mine
    ).execute(path=str(project / "wiki" / "riattivazione-fisica.md"), content=LIST_SOURCE_PAGE)

    assert mine in result
    assert LIST_REFUSED not in result
