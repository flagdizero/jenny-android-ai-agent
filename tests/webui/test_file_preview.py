"""Test della logica pura di preview file (jenny/webui/file_preview.py).

Nessuna route qui: ``file_preview_payload`` è invocata direttamente con uno
``WorkspaceScope`` reale su ``tmp_path``. Copre i codici di errore (400/403/
404/415), il troncamento, il rilevamento binario e le utility di normalizza-
zione path/linguaggio.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.security.workspace_access import default_workspace_scope
from jenny.webui.file_preview import (
    WebUIFilePreviewError,
    _clean_preview_path,
    _language_for_path,
    file_preview_payload,
)


@pytest.fixture()
def scope(tmp_path: Path):
    return default_workspace_scope(tmp_path, restrict_to_workspace=True)


# ---------------------------------------------------------------------------
# Errori di validazione/accesso
# ---------------------------------------------------------------------------


def test_missing_path_raises_400(scope) -> None:
    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload(None, scope=scope)
    assert exc.value.status == 400

    with pytest.raises(WebUIFilePreviewError) as exc2:
        file_preview_payload("   ", scope=scope)
    assert exc2.value.status == 400


def test_path_too_long_raises_400(scope) -> None:
    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload("a" * 4097, scope=scope)
    assert exc.value.status == 400


def test_missing_file_raises_404(scope) -> None:
    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload("does-not-exist.py", scope=scope)
    assert exc.value.status == 404


def test_path_outside_workspace_raises_403(scope, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("segreto", encoding="utf-8")
    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload(str(outside), scope=scope)
    assert exc.value.status == 403


def test_directory_path_raises_404(scope, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload("sub", scope=scope)
    assert exc.value.status == 404


# ---------------------------------------------------------------------------
# Path felice + troncamento + binari
# ---------------------------------------------------------------------------


def test_happy_path_returns_content_and_metadata(scope, tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('ciao')\n", encoding="utf-8")

    payload = file_preview_payload("src/main.py", scope=scope)

    assert payload["content"] == "print('ciao')\n"
    assert payload["language"] == "python"
    assert payload["display_path"] == "src/main.py"
    assert payload["truncated"] is False
    assert payload["size"] == target.stat().st_size
    assert payload["project_path"] == str(scope.project_path)


def test_binary_file_raises_415(scope, tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"abc\x00def")

    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload("data.bin", scope=scope)
    assert exc.value.status == 415


def test_large_file_is_truncated(scope, tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")

    payload = file_preview_payload("big.txt", scope=scope, max_bytes=10)

    assert payload["truncated"] is True
    assert payload["content"] == "x" * 10


def test_small_file_under_max_bytes_is_not_truncated(scope, tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("x" * 10, encoding="utf-8")

    payload = file_preview_payload("small.txt", scope=scope, max_bytes=10)

    assert payload["truncated"] is False
    assert payload["content"] == "x" * 10


def test_file_uri_is_resolved(scope, tmp_path: Path) -> None:
    target = tmp_path / "uri.py"
    target.write_text("ok", encoding="utf-8")

    payload = file_preview_payload(f"file://{target}", scope=scope)

    assert payload["content"] == "ok"


# ---------------------------------------------------------------------------
# Helper puri: normalizzazione path e mapping linguaggio
# ---------------------------------------------------------------------------


def test_clean_preview_path_strips_line_column_suffix() -> None:
    assert _clean_preview_path("src/main.py:12:5") == "src/main.py"
    assert _clean_preview_path("src/main.py:12") == "src/main.py"


def test_clean_preview_path_handles_empty_input() -> None:
    assert _clean_preview_path(None) == ""
    assert _clean_preview_path("   ") == ""


def test_clean_preview_path_strips_query_and_fragment() -> None:
    assert _clean_preview_path("src/main.py?foo=bar#L10") == "src/main.py"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("app.py", "python"),
        ("Dockerfile", "dockerfile"),
        ("index.tsx", "tsx"),
        ("styles.scss", "scss"),
        ("readme.mystery", "mystery"),
        ("noext", "text"),
    ],
)
def test_language_for_path(name: str, expected: str) -> None:
    assert _language_for_path(Path(name)) == expected


# ---------------------------------------------------------------------------
# Un quaderno: i percorsi delle pagine partono da ``wiki/``
# ---------------------------------------------------------------------------


@pytest.fixture()
def notebook(tmp_path: Path):
    """``wikis/piante`` con una pagina sotto ``wiki/entities/``, come sul telefono."""
    root = tmp_path / "wikis" / "piante"
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "wiki" / "entities" / "Pothos.md").write_text("# Pothos\n", encoding="utf-8")
    return root


def test_a_notebook_page_path_is_found_under_wiki(notebook, tmp_path: Path) -> None:
    """Nella chat di piante Jenny scrive ``entities/Pothos.md``: il percorso di
    una pagina, relativo a ``wiki/``. L'anteprima lo cercava dalla radice del
    progetto e rispondeva 404 («Failed to load» sul Titan 2, 26/09/2026)."""
    scope = default_workspace_scope(notebook, restrict_to_workspace=True)

    payload = file_preview_payload("entities/Pothos.md", scope=scope, workspace_root=tmp_path)

    assert payload["content"] == "# Pothos\n"
    assert payload["display_path"] == "wiki/entities/Pothos.md"
    # «Apri nell'editor» apre dal workspace: gli serve il percorso da li'.
    assert payload["workspace_path"] == "wikis/piante/wiki/entities/Pothos.md"


def test_a_file_at_the_project_root_wins_over_wiki(notebook) -> None:
    (notebook / "entities").mkdir()
    (notebook / "entities" / "Pothos.md").write_text("fuori da wiki\n", encoding="utf-8")
    scope = default_workspace_scope(notebook, restrict_to_workspace=True)

    assert file_preview_payload("entities/Pothos.md", scope=scope)["content"] == "fuori da wiki\n"


def test_outside_a_notebook_there_is_no_wiki_fallback(tmp_path: Path) -> None:
    folder = tmp_path / "progetto"
    (folder / "altro" / "entities").mkdir(parents=True)
    (folder / "altro" / "entities" / "x.md").write_text("x", encoding="utf-8")
    scope = default_workspace_scope(folder, restrict_to_workspace=True)

    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload("entities/x.md", scope=scope)
    assert exc.value.status == 404


def test_the_wiki_fallback_does_not_leave_the_project(notebook, tmp_path: Path) -> None:
    (tmp_path / "wikis" / "segreto.md").write_text("no", encoding="utf-8")
    scope = default_workspace_scope(notebook, restrict_to_workspace=True)

    with pytest.raises(WebUIFilePreviewError) as exc:
        file_preview_payload("../../segreto.md", scope=scope)
    assert exc.value.status in (403, 404)
