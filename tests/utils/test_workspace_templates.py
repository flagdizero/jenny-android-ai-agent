"""Test di sync_workspace_templates (jenny.utils.helpers).

Copertura ricollocata qui dopo la rimozione di tests/agent/test_onboard_logic.py
(che testava anche _merge_missing_defaults, simbolo eliminato): questi invarianti
proteggono i file utente esistenti nel workspace e restano comportamento vivo
(chiamanti di produzione: runtime/container.py e android_entry.py).
"""

from pathlib import Path
from types import SimpleNamespace

import jenny.utils.helpers as helpers_module
from jenny.runtime.container import GatewayContainer
from jenny.utils.helpers import sync_workspace_templates


class TestSyncWorkspaceTemplates:
    """Tests for sync_workspace_templates file synchronization."""

    def test_creates_missing_files(self, tmp_path):
        """Should create template files that don't exist."""
        workspace = tmp_path / "workspace"

        added = sync_workspace_templates(workspace, silent=True)

        # Check that some files were created
        assert isinstance(added, list)
        # The actual files depend on the templates directory

    def test_does_not_overwrite_existing_files(self, tmp_path):
        """Should not overwrite files that already exist."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "AGENTS.md").write_text("existing content")

        sync_workspace_templates(workspace, silent=True)

        # Existing file should not be changed
        content = (workspace / "AGENTS.md").read_text()
        assert content == "existing content"

    def test_does_not_create_tools_md(self, tmp_path):
        """Tool contract is injected internally, not copied into user workspaces."""
        workspace = tmp_path / "workspace"

        added = sync_workspace_templates(workspace, silent=True)

        assert "TOOLS.md" not in added
        assert not (workspace / "TOOLS.md").exists()

    def test_preserves_existing_tools_md_without_overwriting(self, tmp_path):
        """Legacy user workspaces may have TOOLS.md; sync should leave it untouched."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        tools_path = workspace / "TOOLS.md"
        tools_path.write_text("custom tool notes", encoding="utf-8")

        sync_workspace_templates(workspace, silent=True)

        assert tools_path.read_text(encoding="utf-8") == "custom tool notes"

    def test_creates_memory_directory(self, tmp_path):
        """Should create memory directory structure."""
        workspace = tmp_path / "workspace"

        sync_workspace_templates(workspace, silent=True)

        assert (workspace / "memory").exists() or (workspace / "skills").exists()

    def test_creates_output_directory(self, tmp_path):
        """La cartella dei risultati esiste dopo il primo sync, ed è segnalata."""
        workspace = tmp_path / "workspace"

        added = sync_workspace_templates(workspace, silent=True)

        assert (workspace / "output").is_dir()
        assert "output/" in added

    def test_output_directory_is_idempotent(self, tmp_path):
        """Un secondo avvio non la ricrea né la riporta di nuovo fra gli aggiunti."""
        workspace = tmp_path / "workspace"
        sync_workspace_templates(workspace, silent=True)

        added = sync_workspace_templates(workspace, silent=True)

        assert (workspace / "output").is_dir()
        assert "output/" not in added

    def test_preserves_existing_output_contents(self, tmp_path):
        """Contiene lavoro finito dell'utente: il sync non deve mai spazzarla."""
        workspace = tmp_path / "workspace"
        output_dir = workspace / "output"
        output_dir.mkdir(parents=True)
        report = output_dir / "report.md"
        report.write_text("risultato precedente", encoding="utf-8")

        added = sync_workspace_templates(workspace, silent=True)

        assert report.read_text(encoding="utf-8") == "risultato precedente"
        assert "output/" not in added

    def test_regular_file_named_output_does_not_block_boot(self, tmp_path):
        """Un file (non una cartella) chiamato ``output`` non deve fermare l'avvio.

        È esattamente il residuo che la cartella ``output/`` esiste per evitare:
        un risultato lasciato nella radice del workspace, che qui capita di
        chiamarsi come la cartella stessa.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "output").write_text("risultato dimenticato", encoding="utf-8")

        sync_workspace_templates(workspace, silent=True)

        assert (workspace / "output").is_dir()
        displaced = sorted(workspace.glob("output.displaced*"))
        assert len(displaced) == 1
        assert displaced[0].read_text(encoding="utf-8") == "risultato dimenticato"

    def test_dangling_symlink_named_output_does_not_block_boot(self, tmp_path):
        """Anche un link rotto occupa il nome: ``exist_ok`` non lo perdona."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "output").symlink_to(workspace / "non-esiste")

        sync_workspace_templates(workspace, silent=True)

        assert (workspace / "output").is_dir()
        displaced = sorted(workspace.glob("output.displaced*"))
        assert len(displaced) == 1
        assert displaced[0].is_symlink()

    def test_symlink_to_directory_named_output_is_left_alone(self, tmp_path):
        """Un link *funzionante* a una cartella è una cartella: non si sposta."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        real = tmp_path / "altrove"
        real.mkdir()
        (real / "report.md").write_text("lavoro finito", encoding="utf-8")
        (workspace / "output").symlink_to(real)

        added = sync_workspace_templates(workspace, silent=True)

        assert (workspace / "output").is_symlink()
        assert (workspace / "output" / "report.md").read_text(encoding="utf-8") == "lavoro finito"
        assert not list(workspace.glob("output.displaced*"))
        assert "output/" not in added

    def test_real_output_directory_is_not_displaced(self, tmp_path):
        """Il caso normale con la cartella già presente resta identico a prima."""
        workspace = tmp_path / "workspace"
        (workspace / "output").mkdir(parents=True)

        added = sync_workspace_templates(workspace, silent=True)

        assert (workspace / "output").is_dir()
        assert not list(workspace.glob("output.displaced*"))
        assert "output/" not in added

    def test_displaced_names_do_not_collide(self, tmp_path):
        """Due avvii con due residui diversi conservano entrambi."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "output").write_text("primo", encoding="utf-8")
        sync_workspace_templates(workspace, silent=True)
        (workspace / "output").rmdir()
        (workspace / "output").write_text("secondo", encoding="utf-8")

        sync_workspace_templates(workspace, silent=True)

        assert (workspace / "output").is_dir()
        bodies = {p.read_text(encoding="utf-8") for p in workspace.glob("output.displaced*")}
        assert bodies == {"primo", "secondo"}

    def test_returns_list_of_added_files(self, tmp_path):
        """Should return list of relative paths for added files."""
        workspace = tmp_path / "workspace"

        added = sync_workspace_templates(workspace, silent=True)

        assert isinstance(added, list)
        # All paths should be relative to workspace
        for path in added:
            assert not Path(path).is_absolute()


def _bare_container(workspace: Path) -> GatewayContainer:
    """Container senza grafo: serve solo il chiamante della sync."""
    config = SimpleNamespace(
        workspace_path=workspace,
        gateway=SimpleNamespace(port=0),
    )
    return GatewayContainer(config)  # type: ignore[arg-type]


class TestContainerTemplateSync:
    """Il percorso d'avvio del container (l'altro chiamante della sync)."""

    def test_startup_survives_regular_file_named_output(self, tmp_path):
        """Il caso reale che teneva giù il gateway: file ``output`` nella radice."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "output").write_text("risultato dimenticato", encoding="utf-8")

        container = _bare_container(workspace)
        container._sync_templates()

        assert (workspace / "output").is_dir()

    def test_startup_survives_a_broken_sync(self, tmp_path, monkeypatch):
        """Rete di sicurezza: qualunque rottura della sync non ferma l'avvio."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        boom = RuntimeError("estrazione guasta")

        def _raise(*_args, **_kwargs):
            raise boom

        monkeypatch.setattr(helpers_module, "sync_workspace_templates", _raise)

        from loguru import logger

        errors: list[str] = []
        sink = logger.add(lambda m: errors.append(m.record["message"]), level="ERROR")
        try:
            container = _bare_container(workspace)
            container._sync_templates()
        finally:
            logger.remove(sink)

        # Non solleva, e lo dice a ERROR nominando la conseguenza vera.
        assert any("prompt di sistema" in e for e in errors), errors
