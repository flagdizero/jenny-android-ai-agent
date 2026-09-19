"""Test del layer comando della WebUI (``jenny/webui/commands.py``).

Qui è finita la logica che stava in ``/api/workspace/write`` e in
``/api/audit/{id}/resolve``: entrambe portavano contenuto (il testo di un file,
una nota libera) dentro un header HTTP, e su quel trasporto il contenuto non ci
sta — 8192 byte per riga, solo ISO-8859-1. La copertura delle route rimosse
(auth, flag, path traversal, atomicità) si trova qui, più la regressione che
prima era impossibile far passare: un file italiano con emoji, oltre 8 KB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui.commands import (
    MAX_WRITE_BYTES,
    CommandContext,
    CommandError,
    dispatch_command,
)

# Il contenuto che il vecchio trasporto non poteva spedire: emoji (fuori da
# ISO-8859-1, `new Headers()` le rifiuta), accenti (surrogate escape lato
# server → UnicodeEncodeError → 400) e più di 8192 byte (MAX_LINE_LENGTH).
_SOUL_LIKE = (
    "# Chi sono\n\nsono Jenny 😏 e parlo con papi — perché è così che è nata "
    "questa cosa 💋\n\n" + "riempimento: però, città, già, ciò 🙄\n" * 400
)


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


@pytest.fixture()
def ctx(workspace_root: Path) -> CommandContext:
    return CommandContext(get_workspace_root=lambda: workspace_root, invalidate_session=lambda _key: None)


def _set_workspace_config(config_path: Path, **overrides) -> None:
    config = load_config(config_path)
    for key, value in overrides.items():
        setattr(config.workspace, key, value)
    save_config(config, config_path)


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


async def test_unknown_method_is_a_bad_request(ctx: CommandContext) -> None:
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "workspace.nuke", {})
    assert exc.value.code == "bad_request"


async def test_unexpected_exception_becomes_internal(
    ctx: CommandContext, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un bug in un comando non deve mai uscire come traceback verso il client."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("jenny.webui.workspace_files.write_file", boom)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "workspace.write", {"path": "a.txt", "content": "x"})
    assert exc.value.code == "internal"
    assert "kaboom" not in exc.value.message


# ---------------------------------------------------------------------------
# workspace.write
# ---------------------------------------------------------------------------


async def test_write_saves_utf8_content_over_8kb(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """La regressione: SOUL.md con emoji e oltre 8 KB torna sul disco identico."""
    assert len(_SOUL_LIKE.encode("utf-8")) > 8192

    result = await dispatch_command(
        ctx, "workspace.write", {"path": "SOUL.md", "content": _SOUL_LIKE}
    )

    saved = (workspace_root / "SOUL.md").read_text(encoding="utf-8")
    assert saved == _SOUL_LIKE
    assert "😏" in saved and "perché" in saved
    assert result["bytes"] == len(_SOUL_LIKE.encode("utf-8"))


async def test_write_creates_missing_parents(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    await dispatch_command(
        ctx, "workspace.write", {"path": "new/note.txt", "content": "ciao"}
    )
    assert (workspace_root / "new" / "note.txt").read_text(encoding="utf-8") == "ciao"


async def test_write_requires_allow_write(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    _set_workspace_config(config_path, allow_write=False)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "workspace.write", {"path": "a.txt", "content": "x"})
    assert exc.value.code == "forbidden"
    assert not (workspace_root / "a.txt").exists()


async def test_write_requires_workspace_enabled(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    _set_workspace_config(config_path, enabled=False)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "workspace.write", {"path": "a.txt", "content": "x"})
    assert exc.value.code == "unavailable"


async def test_write_fails_closed_when_config_raises(
    ctx: CommandContext,
    workspace_root: Path,
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config illeggibile non scavalca il gate: niente scrittura."""

    def _boom(*args, **kwargs):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("jenny.config.loader.load_config", _boom)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "workspace.write", {"path": "a.txt", "content": "x"})
    assert exc.value.code == "unavailable"
    assert not (workspace_root / "a.txt").exists()


async def test_write_rejects_path_traversal(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "workspace.write", {"path": "../outside.txt", "content": "x"}
        )
    assert exc.value.code == "bad_request"
    assert not (workspace_root.parent / "outside.txt").exists()


async def test_write_requires_a_path(ctx: CommandContext, config_path: Path) -> None:
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "workspace.write", {"content": "x"})
    assert exc.value.code == "bad_request"


async def test_write_rejects_non_string_content(
    ctx: CommandContext, config_path: Path
) -> None:
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "workspace.write", {"path": "a.txt", "content": 42})
    assert exc.value.code == "bad_request"


async def test_write_rejects_content_over_the_cap(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Il tetto è un messaggio, non un troncamento silenzioso del trasporto."""
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "workspace.write", {"path": "big.txt", "content": "a" * (MAX_WRITE_BYTES + 1)}
        )
    assert exc.value.code == "too_large"
    assert not (workspace_root / "big.txt").exists()


async def test_write_that_fails_keeps_the_previous_content(
    ctx: CommandContext,
    workspace_root: Path,
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Salvare riscrive il file intero: deve restare atomico (rename finale)."""
    target = workspace_root / "note.txt"
    target.write_text("originale", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr("jenny.webui.workspace_files.atomic_write", boom)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "workspace.write", {"path": "note.txt", "content": "nuovo"}
        )
    assert exc.value.code == "bad_request"
    assert target.read_text(encoding="utf-8") == "originale"


# ---------------------------------------------------------------------------
# audit.resolve
# ---------------------------------------------------------------------------


def _workspace_with_audit(workspace_root: Path) -> str:
    """Crea ``wikis/main`` con una pagina e un audit aperto; ritorna il suo id."""
    from jenny.webui.wiki import create_audit

    pages_dir = workspace_root / "wikis" / "main" / "wiki"
    pages_dir.mkdir(parents=True)
    (pages_dir / "index.md").write_text("# Home\ncontent here", encoding="utf-8")
    created = create_audit(
        wiki_root=pages_dir.parent,
        target="index.md",
        raw_markdown="# Home\ncontent here",
        sel_start=8,
        sel_end=15,
        comment="typo",
        severity="warn",
        author="test",
    )
    return created["id"]


async def test_audit_resolve_moves_the_item_and_keeps_the_note(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    from jenny.webui.wiki import load_audits

    audit_id = _workspace_with_audit(workspace_root)

    await dispatch_command(
        ctx,
        "audit.resolve",
        {"audit_id": audit_id, "wiki": "main", "resolution": "corretto è già a posto 😏"},
    )

    wiki_root = workspace_root / "wikis" / "main"
    assert load_audits(wiki_root, mode="open") == []
    resolved = load_audits(wiki_root, mode="resolved")
    assert len(resolved) == 1
    # La nota arriva integra: era il motivo del percent-encoding nell'header.
    assert "corretto è già a posto 😏" in resolved[0].body


async def test_audit_resolve_requires_wiki(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    audit_id = _workspace_with_audit(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "audit.resolve", {"audit_id": audit_id})
    assert exc.value.code == "bad_request"


async def test_audit_resolve_unknown_wiki_is_not_found(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    audit_id = _workspace_with_audit(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "audit.resolve", {"audit_id": audit_id, "wiki": "ghost"}
        )
    assert exc.value.code == "not_found"


async def test_audit_resolve_malformed_id_is_a_bad_request(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    _workspace_with_audit(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "audit.resolve", {"audit_id": "nope", "wiki": "main"}
        )
    assert exc.value.code == "bad_request"


async def test_audit_resolve_absent_id_is_not_found(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Id ben formato ma inesistente: la distinzione 400/404 della route resta."""
    _workspace_with_audit(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "audit.resolve", {"audit_id": "20260812-215900-abcd", "wiki": "main"}
        )
    assert exc.value.code == "not_found"


async def test_audit_resolve_blocked_when_wiki_disabled(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    audit_id = _workspace_with_audit(workspace_root)
    config = load_config(config_path)
    config.wiki.enabled = False
    save_config(config, config_path)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "audit.resolve", {"audit_id": audit_id, "wiki": "main"}
        )
    assert exc.value.code == "unavailable"


# ---------------------------------------------------------------------------
# soul.rules.write
# ---------------------------------------------------------------------------


async def test_saving_rules_writes_the_truth_and_the_copy(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Due scritture, una sola operazione.

    La verità va dove Dream non può scrivere; dentro ``SOUL.md`` ne resta la
    copia che il prompt legge. Se il comando ne facesse una sola, l'altra
    comincerebbe a divergere al primo salvataggio.
    """
    from jenny.agent.soul_rules import RULES_FILE, extract_rules

    (workspace_root / "SOUL.md").write_text("# Soul\n\nI am Jenny.\n", encoding="utf-8")

    result = await dispatch_command(
        ctx, "soul.rules.write", {"content": "  Chiamami per nome. 😏  "}
    )

    assert result["chars"] == len("Chiamami per nome. 😏")
    assert (workspace_root / RULES_FILE).read_text(encoding="utf-8").strip() == (
        "Chiamami per nome. 😏"
    )
    soul = (workspace_root / "SOUL.md").read_text(encoding="utf-8")
    assert extract_rules(soul) == "Chiamami per nome. 😏"
    assert "I am Jenny." in soul


async def test_rules_longer_than_the_cap_are_refused(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Il tetto non è di trasporto — quello è mille volte più alto — è una
    misura di cosa sia una regola: quel testo entra nel prompt di ogni turno."""
    from jenny.webui.commands import MAX_SOUL_RULES_CHARS

    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "soul.rules.write", {"content": "x" * (MAX_SOUL_RULES_CHARS + 1)}
        )
    assert exc.value.code == "too_large"
    assert MAX_SOUL_RULES_CHARS < MAX_WRITE_BYTES, "il tetto delle regole è un limite di trasporto"


async def test_rules_are_refused_when_writes_are_off(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Lo stesso interruttore di ``workspace.write``: questo comando scrive due
    file del workspace, e non può essere la scorciatoia che lo aggira."""
    _set_workspace_config(config_path, allow_write=False)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "soul.rules.write", {"content": "x"})
    assert exc.value.code == "forbidden"


async def test_emptying_the_rules_takes_the_block_out(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    from jenny.agent.soul_rules import HEADING, RULES_FILE

    (workspace_root / "SOUL.md").write_text("# Soul\n\nI am Jenny.\n", encoding="utf-8")
    await dispatch_command(ctx, "soul.rules.write", {"content": "Chiamami per nome."})
    await dispatch_command(ctx, "soul.rules.write", {"content": ""})

    assert not (workspace_root / RULES_FILE).exists()
    soul = (workspace_root / "SOUL.md").read_text(encoding="utf-8")
    assert HEADING not in soul
    assert "I am Jenny." in soul


async def test_rules_that_are_not_text_are_a_bad_request(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "soul.rules.write", {"content": {"a": 1}})
    assert exc.value.code == "bad_request"
