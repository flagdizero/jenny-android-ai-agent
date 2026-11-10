"""Test delle route ``/api/workspace/*`` (file-manager del workspace).

``jenny/webui/workspace_routes.py`` non aveva ancora test dedicati: qui si
copre auth 401, il gate ``workspace.enabled`` (503), il rispetto di
``allow_write``, i path felici di ogni operazione e il rifiuto del path
traversal (delegato a ``workspace_files.validate_path``).

La **scrittura** non è più una route (il contenuto di un file non entra in un
header HTTP): i suoi test sono in ``tests/webui/test_commands.py``. Cancellazione,
rinomina e copia nemmeno: i loro sono in
``tests/webui/test_workspace_commands.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.gateway_http import make_request
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret
from jenny.config.loader import load_config, save_config
from jenny.webui.workspace_routes import WorkspaceRoutes

_SECRET = "s3cr3t-workspace"


def _request(path: str, token: str | None = _SECRET) -> WsRequest:
    return make_request(path, token)


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _set_workspace_config(config_path: Path, **overrides) -> None:
    config = load_config(config_path)
    for key, value in overrides.items():
        setattr(config.workspace, key, value)
    save_config(config, config_path)


@pytest.fixture()
def routes(workspace_root: Path) -> WorkspaceRoutes:
    return WorkspaceRoutes(
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        get_workspace_root=lambda: workspace_root,
    )


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_returns_none_for_unrelated_path(routes: WorkspaceRoutes) -> None:
    assert await routes.dispatch(_request("/api/other"), "/api/other") is None


# ---------------------------------------------------------------------------
# /api/workspace/list
# ---------------------------------------------------------------------------


async def test_list_requires_auth(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/list", token=None), "/api/workspace/list"
    )
    assert response.status_code == 401


async def test_list_returns_503_when_workspace_disabled(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    _set_workspace_config(config_path, enabled=False)
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 503


async def test_list_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "note.txt").write_text("hello", encoding="utf-8")
    (workspace_root / "sub").mkdir()
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    names = {item["name"] for item in _json(response)["items"]}
    assert names == {"note.txt", "sub"}


async def test_list_rejects_path_traversal(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/list?path=../../etc"), "/api/workspace/list"
    )
    assert response.status_code == 400


async def test_list_missing_subdir_returns_404(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/list?path=missing-dir"), "/api/workspace/list"
    )
    assert response.status_code == 404


async def test_list_survives_a_dangling_symlink(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    """Un link verso niente e' una voce della cartella, non un 404 della cartella.

    ``item.stat()`` segue il link: su un bersaglio che non c'e' solleva
    ``FileNotFoundError``, e la rotta rispondeva «path not found» per l'intera
    cartella — che invece c'e'. Stesso per un loop.
    """
    import os

    (workspace_root / "note.txt").write_text("hello", encoding="utf-8")
    os.symlink("non-esiste", workspace_root / "pendente")
    os.symlink("ciclo", workspace_root / "ciclo")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    items = {item["name"]: item for item in _json(response)["items"]}
    assert set(items) == {"note.txt", "pendente", "ciclo"}
    assert items["pendente"]["type"] == "file"
    assert items["pendente"]["size"] is None


async def test_list_marks_dotfiles_internal_without_manifest(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "note.txt").write_text("hello", encoding="utf-8")
    (workspace_root / ".hidden").write_text("y", encoding="utf-8")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name == {"note.txt": False, ".hidden": True}


async def test_list_marks_default_runtime_dirs_internal_without_manifest(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    # config.json/agent/cron/sessions/ui sono stato del runtime (segreti,
    # bundle rigenerati, storage dei motori interni), non contenuto
    # dell'utente: nascosti di default come i dotfile, senza bisogno di un
    # manifest esplicito.
    (workspace_root / "config.json").write_text("{}", encoding="utf-8")
    # Il backup e il file messo in quarantena portano le stesse chiavi API e lo
    # stesso secret del file vivo: vanno nascosti anche loro, altrimenti li si
    # vedrebbe nel browser senza developer mode.
    (workspace_root / "config.json.bak").write_text("{}", encoding="utf-8")
    (workspace_root / "config.corrupt-20260803T120000Z.json").write_text("x", encoding="utf-8")
    # Residuo di un ``atomic_write`` interrotto: runtime, non contenuto utente.
    (workspace_root / "note.txt.abc123.tmp").write_text("x", encoding="utf-8")
    (workspace_root / "agent").mkdir()
    (workspace_root / "agent" / "identity.md").write_text("x", encoding="utf-8")
    (workspace_root / "cron").mkdir()
    (workspace_root / "sessions").mkdir()
    (workspace_root / "ui").mkdir()
    (workspace_root / "AGENTS.md").write_text("x", encoding="utf-8")

    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name == {
        "config.json": True,
        "config.json.bak": True,
        "config.corrupt-20260803T120000Z.json": True,
        "note.txt.abc123.tmp": True,
        "agent": True,
        "cron": True,
        "sessions": True,
        "ui": True,
        "AGENTS.md": False,
    }


async def test_list_marks_update_state_internal(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    # update_state.json è il diario dell'updater (runtime/update_check.py), non
    # contenuto dell'utente. Un file dell'utente con nome vicino resta visibile:
    # il pattern è il nome esatto, non un prefisso.
    (workspace_root / "update_state.json").write_text("{}", encoding="utf-8")
    (workspace_root / "update_states.json").write_text("{}", encoding="utf-8")
    (workspace_root / "note.txt").write_text("hi", encoding="utf-8")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name == {
        "update_state.json": True,
        "update_states.json": False,
        "note.txt": False,
    }


async def test_list_marks_nested_pycache_internal(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    # __pycache__ nasce ovunque l'agente importi un modulo del workspace, non
    # solo nella radice: il pattern deve reggere a qualsiasi profondità, senza
    # nascondere gli script che l'utente ha scritto lì accanto.
    scripts = workspace_root / "skills" / "raincheck" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "__pycache__").mkdir()
    (scripts / "bot.py").write_text("print(1)\n", encoding="utf-8")

    response = await routes.dispatch(
        _request("/api/workspace/list?path=skills/raincheck/scripts"), "/api/workspace/list"
    )
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name == {"__pycache__": True, "bot.py": False}


async def test_list_marks_pycache_contents_internal(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    # Entrandoci in modalità avanzata, anche il contenuto va marcato: i nomi dei
    # bytecode sono arbitrari, quindi il match è sul path relativo.
    cache = workspace_root / "skills" / "raincheck" / "scripts" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "bot.cpython-311.pyc").write_bytes(b"\x00fake")
    response = await routes.dispatch(
        _request("/api/workspace/list?path=skills/raincheck/scripts/__pycache__"),
        "/api/workspace/list",
    )
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name == {"bot.cpython-311.pyc": True}


async def test_list_keeps_user_content_visible_with_default_patterns(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    # La metà che conta: nessuno dei default deve nascondere qualcosa che
    # l'utente abbia scritto, nemmeno quando il nome ci somiglia.
    (workspace_root / "my__pycache__notes").mkdir()
    (workspace_root / "my__pycache__notes" / "appunti.md").write_text("x", encoding="utf-8")
    for name in ("note.txt", "update.json", "agenti.md", "cronaca.txt", "uistyle.css"):
        (workspace_root / name).write_text("x", encoding="utf-8")

    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    assert all(item["internal"] is False for item in _json(response)["items"])

    nested = await routes.dispatch(
        _request("/api/workspace/list?path=my__pycache__notes"), "/api/workspace/list"
    )
    assert nested.status_code == 200
    assert all(item["internal"] is False for item in _json(nested)["items"])


async def test_list_uses_internal_manifest_patterns(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / ".jenny").mkdir()
    (workspace_root / ".jenny" / "internal.json").write_text(
        json.dumps({"patterns": ["secret.txt"]}), encoding="utf-8"
    )
    (workspace_root / "secret.txt").write_text("shh", encoding="utf-8")
    (workspace_root / "normal.txt").write_text("hi", encoding="utf-8")
    # Il manifest *sostituisce* i default, non ci si somma: update_state.json
    # torna visibile. È il contratto storico, che i nuovi pattern non cambiano.
    (workspace_root / "update_state.json").write_text("{}", encoding="utf-8")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name["secret.txt"] is True
    assert by_name["normal.txt"] is False
    assert by_name["update_state.json"] is False


async def test_list_works_when_workspace_root_is_a_symlink(
    tmp_path: Path, config_path: Path
) -> None:
    # Su Android /data/data/... e /data/user/0/... sono alias simlink dello
    # stesso path: get_workspace_root() ritorna la forma non risolta, mentre
    # validate_path() risolve i symlink prima di iterare la directory. Un
    # mismatch testuale tra le due forme rompe item.relative_to(workspace_root)
    # (visto dal vivo, non solo teoricamente: bug trovato via verifica on-device).
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "note.txt").write_text("hello", encoding="utf-8")
    (real_root / ".hidden").write_text("y", encoding="utf-8")
    symlinked_root = tmp_path / "link"
    symlinked_root.symlink_to(real_root, target_is_directory=True)

    routes = WorkspaceRoutes(
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        get_workspace_root=lambda: symlinked_root,
    )

    list_response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert list_response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(list_response)["items"]}
    assert by_name == {"note.txt": False, ".hidden": True}


async def test_list_falls_back_to_default_on_malformed_manifest(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / ".jenny").mkdir()
    (workspace_root / ".jenny" / "internal.json").write_text("{not json", encoding="utf-8")
    (workspace_root / "normal.txt").write_text("hi", encoding="utf-8")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name["normal.txt"] is False


# ---------------------------------------------------------------------------
# /api/workspace/read
# ---------------------------------------------------------------------------


async def test_read_requires_auth(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/read?path=a.txt", token=None), "/api/workspace/read"
    )
    assert response.status_code == 401


async def test_read_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "note.txt").write_text("contenuto", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=note.txt"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert _json(response)["content"] == "contenuto"


async def test_read_missing_file_returns_404(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/read?path=missing.txt"), "/api/workspace/read"
    )
    assert response.status_code == 404


async def test_read_rejects_oversized_file(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    _set_workspace_config(config_path, max_file_size=4)
    (workspace_root / "big.txt").write_text("molto più lungo di 4 byte", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=big.txt"), "/api/workspace/read"
    )
    assert response.status_code == 400


async def test_read_binary_file_returns_415(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    """Un byte nullo nei primi 4 KB marca il file come binario → 415."""
    (workspace_root / "blob.bin").write_bytes(b"\x89PNG\x00\x1a\ndati")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=blob.bin"), "/api/workspace/read"
    )
    assert response.status_code == 415


async def test_read_text_without_extension(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    """La leggibilità dipende dal contenuto, mai dall'estensione."""
    (workspace_root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=Dockerfile"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert _json(response)["content"] == "FROM scratch\n"


async def test_read_jsonl_is_text(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "log.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=log.jsonl"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert _json(response)["content"] == '{"a": 1}\n{"a": 2}\n'


async def test_read_invalid_utf8_is_tolerated(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    """Byte non-UTF-8 senza null byte: si legge con caratteri sostitutivi."""
    (workspace_root / "latin.txt").write_bytes(b"caff\xe8 e brioche")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=latin.txt"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert "caff" in _json(response)["content"]
    assert "�" in _json(response)["content"]


# ---------------------------------------------------------------------------
# /api/workspace/mkdir
# ---------------------------------------------------------------------------


async def test_mkdir_requires_allow_write(routes: WorkspaceRoutes, config_path: Path) -> None:
    _set_workspace_config(config_path, allow_write=False)
    response = await routes.dispatch(
        _request("/api/workspace/mkdir?path=newdir"), "/api/workspace/mkdir"
    )
    assert response.status_code == 403


async def test_mkdir_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/mkdir?path=newdir"), "/api/workspace/mkdir"
    )
    assert response.status_code == 200
    assert (workspace_root / "newdir").is_dir()


# ---------------------------------------------------------------------------
# rename / delete / copy: non piu' rotte, ma comandi dell'RPC
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/workspace/delete?path=gone.txt",
        "/api/workspace/rename?oldPath=gone.txt&newPath=new.txt",
        "/api/workspace/copy?path=gone.txt&dest=dst.txt",
    ],
)
async def test_the_writing_gets_are_gone(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path, path: str
) -> None:
    """Cancellare, rinominare e copiare sono comandi dell'RPC WebSocket
    (``workspace.delete``/``rename``/``copy``, in ``tests/webui/test_workspace_commands.py``):
    una GET che scrive sul disco non esiste piu', e l'indirizzo e' un 404."""
    from unittest.mock import MagicMock

    from support.gateway_http import make_handler

    (workspace_root / "gone.txt").write_text("z", encoding="utf-8")
    route = path.split("?")[0]
    assert await routes.dispatch(_request(path), route) is None
    reply = await make_handler(workspace_root).dispatch(MagicMock(), make_request(path))
    assert reply.status_code == 404
    assert (workspace_root / "gone.txt").read_text(encoding="utf-8") == "z"
    assert not (workspace_root / "new.txt").exists()
    assert not (workspace_root / "dst.txt").exists()


# ---------------------------------------------------------------------------
# /api/workspace/download
# ---------------------------------------------------------------------------


async def test_download_requires_auth(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/download?path=a.txt", token=None),
        "/api/workspace/download",
    )
    assert response.status_code == 401


async def test_download_missing_file_returns_404(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/download?path=missing.bin"), "/api/workspace/download"
    )
    assert response.status_code == 404


async def test_download_rejects_directory(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "adir").mkdir()
    response = await routes.dispatch(
        _request("/api/workspace/download?path=adir"), "/api/workspace/download"
    )
    assert response.status_code == 400


async def test_download_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "file.bin").write_bytes(b"\x00\x01binary")
    response = await routes.dispatch(
        _request("/api/workspace/download?path=file.bin"), "/api/workspace/download"
    )
    assert response.status_code == 200
    assert response.body == b"\x00\x01binary"
    assert 'filename="file.bin"' in response.headers["Content-Disposition"]


async def test_download_rejects_path_traversal(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/download?path=../../etc/passwd"),
        "/api/workspace/download",
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("name", "fallback", "encoded"),
    [
        ("città 😏.txt", "citt_ _.txt", "citt%C3%A0%20%F0%9F%98%8F.txt"),
        ('dice "ciao".md', 'dice \\"ciao\\".md', "dice%20%22ciao%22.md"),
        ("a\r\nSet-Cookie: x=1.txt", "a__Set-Cookie: x=1.txt", "a%0D%0ASet-Cookie%3A%20x%3D1.txt"),
        ("back\\slash.txt", "back\\\\slash.txt", "back%5Cslash.txt"),
    ],
)
async def test_download_names_any_file_safely(
    routes: WorkspaceRoutes,
    workspace_root: Path,
    config_path: Path,
    name: str,
    fallback: str,
    encoded: str,
) -> None:
    """Il nome del file finiva crudo fra virgolette nell'header: un'emoji o un
    accento facevano rifiutare l'header a ``websockets`` (500), un ``"`` lo
    chiudeva prima, e un a-capo nel nome ne apriva un altro.
    Ora c'e' il ``filename*`` di RFC 6266 in UTF-8 percent-encodato, e un
    ``filename`` ASCII di ripiego con ``"`` e ``\\`` escapati."""
    from urllib.parse import quote

    (workspace_root / name).write_bytes(b"x")
    url = f"/api/workspace/download?path={quote(name, safe='')}"
    response = await routes.dispatch(_request(url), "/api/workspace/download")
    assert response.status_code == 200
    disposition = response.headers["Content-Disposition"]
    assert disposition == f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    # L'header deve passare il controllo di ``websockets`` (16.1 lo fa gia' nel
    # costruttore di ``Headers``, 16.0 no): ASCII stampabile e niente a-capo.
    assert disposition.isascii() and "\r" not in disposition and "\n" not in disposition


def test_a_name_that_is_not_utf8_still_gets_a_header() -> None:
    """Su Linux (e su Android) un nome di file e' una sequenza di byte: quelli che
    non sono UTF-8 Python li porta come surrogati (``surrogateescape``), e
    ``quote`` li rifiutava con ``UnicodeEncodeError`` — 500 invece del file. Il
    byte che non si decodifica diventa U+FFFD nel ``filename*`` e ``_`` nel
    ripiego. Diretto sulla funzione: macOS un nome cosi' non lo crea."""
    from jenny.webui.workspace_routes import content_disposition

    name = b"foto-\xe9t\xe9.jpg".decode("utf-8", "surrogateescape")
    disposition = content_disposition(name)
    assert disposition == (
        "attachment; filename=\"foto-_t_.jpg\"; filename*=UTF-8''foto-%EF%BF%BDt%EF%BF%BD.jpg"
    )
    assert disposition.isascii()


@pytest.mark.parametrize("url", ["/api/workspace/read?path=adir", "/api/workspace/list?path=ciclo"])
async def test_a_filesystem_error_does_not_leak_the_absolute_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path, url: str
) -> None:
    """Il 400 portava ``str(OSError)`` — ``[Errno 21] Is a directory:
    '/data/user/0/…/workspace/adir'`` —, cioe' il percorso assoluto della cartella
    privata dell'app. Resta il perche', senza il dove."""
    import os

    (workspace_root / "adir").mkdir()
    os.symlink("ciclo", workspace_root / "ciclo")
    response = await routes.dispatch(_request(url), url.split("?")[0])
    assert response.status_code == 400
    body = response.body.decode("utf-8")
    assert str(workspace_root) not in body and str(workspace_root.resolve()) not in body
    assert "Errno" not in body
    assert body.strip()


# ---------------------------------------------------------------------------
# Il disco fuori dall'event loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "fn"),
    [
        ("/api/workspace/list", "list_directory"),
        ("/api/workspace/read?path=a.txt", "read_file"),
        ("/api/workspace/download?path=a.txt", "read_download"),
    ],
)
async def test_the_disk_work_runs_off_the_event_loop(
    routes: WorkspaceRoutes,
    workspace_root: Path,
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    fn: str,
) -> None:
    """Leggere fino a un file intero (``read_bytes``) o elencare una cartella
    grande sul loop fermava il gateway per tutti."""
    import threading

    from jenny.webui import workspace_files

    (workspace_root / "a.txt").write_text("ciao", encoding="utf-8")
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = getattr(workspace_files, fn)

    def spy(*args, **kwargs):
        seen.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(workspace_files, fn, spy)
    response = await routes.dispatch(_request(url), url.split("?")[0])
    assert response.status_code == 200
    assert seen and all(ident != loop_thread for ident in seen)
