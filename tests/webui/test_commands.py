"""Test del layer comando della WebUI (``jenny/webui/commands.py``).

Qui è finita la logica di quel che porta **contenuto**: il testo di un file, una
riga di regole, una pagina di quaderno. La superficie ``/api/`` non lo trasporta
— è servita dall'hook di handshake di ``websockets``, che non legge body: 8192
byte per riga e solo ISO-8859-1. La regressione che prima era impossibile far
passare è qui: un file italiano con emoji, oltre 8 KB.

C'era anche ``audit.resolve``, che chiudeva una segnalazione con una nota. Se
n'è andato il 22/09/2026 con la metà «leggi e chiudi» del giro degli audit: dal
telefono una segnalazione si apre e basta, e chi la lavora è Jenny, che il file
lo sposta con i suoi strumenti come le dice la skill.
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
    return CommandContext(get_workspace_root=lambda: workspace_root, invalidate_session=lambda _key: None, busy_session_keys=lambda: (), get_cron_service=lambda: None)


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
# page.write
# ---------------------------------------------------------------------------


_PAGE = "# Orto\n\nI pomodori vanno legati a giugno.\n"


def _workspace_with_page(workspace_root: Path, body: str = _PAGE) -> Path:
    """``wikis/main/wiki/index.md`` piu' i fratelli che il contenimento esclude."""
    pages_dir = workspace_root / "wikis" / "main" / "wiki"
    pages_dir.mkdir(parents=True)
    (pages_dir / "index.md").write_text(body, encoding="utf-8")
    (pages_dir / "note").mkdir()
    (pages_dir / "note" / "orto.md").write_text("# Orto\n", encoding="utf-8")
    raw = workspace_root / "wikis" / "main" / "raw"
    raw.mkdir()
    (raw / "appunti.md").write_text("# grezzo\n", encoding="utf-8")
    return pages_dir


async def test_page_write_saves_and_reads_back(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Il giro intero, con il contenuto che il vecchio trasporto non spediva."""
    pages_dir = _workspace_with_page(workspace_root)
    nuovo = "# Orto\n\nI pomodori vanno legati a giugno — pero' gia' a maggio 😏\n"

    out = await dispatch_command(
        ctx,
        "page.write",
        {"wiki": "main", "page": "index.md", "content": nuovo, "base": _PAGE},
    )

    assert (pages_dir / "index.md").read_text(encoding="utf-8") == nuovo
    assert out["bytes"] == len(nuovo.encode("utf-8"))


async def test_page_write_in_a_subfolder(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    pages_dir = _workspace_with_page(workspace_root)
    await dispatch_command(
        ctx,
        "page.write",
        {"wiki": "main", "page": "note/orto.md", "content": "# Altro\n", "base": "# Orto\n"},
    )
    assert (pages_dir / "note" / "orto.md").read_text(encoding="utf-8") == "# Altro\n"


async def test_page_write_refuses_a_stale_base_without_writing(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """**La prova che conta.** Jenny ha riscritto la pagina mentre era aperta.

    Due asserzioni, e la seconda e' il punto: non basta che la risposta sia un
    ``conflict``, deve essere vero che **il file non e' stato toccato**. Un
    codice giusto su una scrittura avvenuta sarebbe il guasto peggiore dei due:
    l'utente vedrebbe un errore e crederebbe di non aver perso niente.
    """
    pages_dir = _workspace_with_page(workspace_root)
    intanto = "# Orto\n\nRiscritto da Jenny mentre l'editor era aperto.\n"
    (pages_dir / "index.md").write_text(intanto, encoding="utf-8")

    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx,
            "page.write",
            {"wiki": "main", "page": "index.md", "content": "il mio testo\n", "base": _PAGE},
        )

    assert exc.value.code == "conflict"
    assert (pages_dir / "index.md").read_text(encoding="utf-8") == intanto


async def test_page_write_needs_the_base(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Senza ``base`` non si scrive **e basta**: mancarlo non vale «vai avanti».

    E' lo stesso ragionamento del default ``refuse`` di ``_conversation_choice``:
    un parametro assente su un'operazione che puo' cancellare il lavoro di
    qualcun altro non deve poter valere il permesso di farlo.
    """
    pages_dir = _workspace_with_page(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "page.write", {"wiki": "main", "page": "index.md", "content": "x"}
        )
    assert exc.value.code == "bad_request"
    assert (pages_dir / "index.md").read_text(encoding="utf-8") == _PAGE


@pytest.mark.parametrize(
    "page",
    ["../raw/appunti.md", "../../main/wiki/index.md", "/etc/passwd", "note/../../raw/appunti.md"],
)
async def test_page_write_refuses_a_path_that_climbs(
    ctx: CommandContext, workspace_root: Path, config_path: Path, page: str
) -> None:
    """Gli stessi input di ``/api/page``, sull'altro verso: qui si scriverebbe."""
    workspace_root_pages = _workspace_with_page(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "page.write", {"wiki": "main", "page": page, "content": "x", "base": ""}
        )
    assert exc.value.code in {"bad_request", "not_found", "forbidden"}
    assert (workspace_root / "wikis" / "main" / "raw" / "appunti.md").read_text(
        encoding="utf-8"
    ) == "# grezzo\n"
    assert (workspace_root_pages / "index.md").read_text(encoding="utf-8") == _PAGE


async def test_page_write_refuses_a_symlink_out_of_the_pages_dir(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Il secondo cancello, e il solo input che lo distingue dal primo.

    ``safe_wiki_page_path`` guarda la stringa: ``scorciatoia.md`` non risale, e
    la supera. A fermarla e' il ``resolve().relative_to(...)``.
    """
    pages_dir = _workspace_with_page(workspace_root)
    bersaglio = workspace_root / "wikis" / "main" / "raw" / "appunti.md"
    (pages_dir / "scorciatoia.md").symlink_to(bersaglio)

    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx,
            "page.write",
            {"wiki": "main", "page": "scorciatoia.md", "content": "x", "base": "# grezzo\n"},
        )

    assert exc.value.code == "forbidden"
    assert bersaglio.read_text(encoding="utf-8") == "# grezzo\n"


async def test_page_write_only_touches_md(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """Il suffisso non si corregge al posto di chi salva (la lettura invece lo fa)."""
    pages_dir = _workspace_with_page(workspace_root)
    (pages_dir / "dati.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "page.write", {"wiki": "main", "page": "dati.json", "content": "x", "base": "{}"}
        )
    assert exc.value.code == "bad_request"
    assert (pages_dir / "dati.json").read_text(encoding="utf-8") == "{}"


async def test_page_write_does_not_create_a_new_page(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    pages_dir = _workspace_with_page(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "page.write", {"wiki": "main", "page": "nuova.md", "content": "x", "base": ""}
        )
    assert exc.value.code == "not_found"
    assert not (pages_dir / "nuova.md").exists()


async def test_page_write_unknown_wiki_is_not_found(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    _workspace_with_page(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "page.write", {"wiki": "ghost", "page": "index.md", "content": "x", "base": ""}
        )
    assert exc.value.code == "not_found"


async def test_page_write_is_blocked_when_the_wiki_is_off(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    pages_dir = _workspace_with_page(workspace_root)
    config = load_config(config_path)
    config.wiki.enabled = False
    save_config(config, config_path)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "page.write", {"wiki": "main", "page": "index.md", "content": "x", "base": _PAGE}
        )
    assert exc.value.code == "unavailable"
    assert (pages_dir / "index.md").read_text(encoding="utf-8") == _PAGE


async def test_page_write_is_blocked_when_writes_are_off(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    """``workspace.allow_write`` vale anche qui.

    ``audit.resolve`` non lo guarda, e la differenza e' voluta: quello chiude
    una nota dentro ``audit/``, questo riscrive una pagina — cioe' esattamente
    cio' che quel flag esiste per governare.
    """
    pages_dir = _workspace_with_page(workspace_root)
    _set_workspace_config(config_path, allow_write=False)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx, "page.write", {"wiki": "main", "page": "index.md", "content": "x", "base": _PAGE}
        )
    assert exc.value.code == "forbidden"
    assert (pages_dir / "index.md").read_text(encoding="utf-8") == _PAGE


async def test_page_write_refuses_more_than_the_cap(
    ctx: CommandContext, workspace_root: Path, config_path: Path
) -> None:
    pages_dir = _workspace_with_page(workspace_root)
    with pytest.raises(CommandError) as exc:
        await dispatch_command(
            ctx,
            "page.write",
            {
                "wiki": "main",
                "page": "index.md",
                "content": "x" * (MAX_WRITE_BYTES + 1),
                "base": _PAGE,
            },
        )
    assert exc.value.code == "too_large"
    assert (pages_dir / "index.md").read_text(encoding="utf-8") == _PAGE


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
