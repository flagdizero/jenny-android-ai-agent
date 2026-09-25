"""``is_path_within``: il confine che le rotte della WebUI e la wiki controllano.

Prima era scritto a mano in ogni punto (``resolve().relative_to(...)``), con
eccezioni catturate a caso. Qui le proprietà che contano, una volta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.security.workspace_policy import is_path_within


def test_the_root_itself_and_its_children_are_inside(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert is_path_within(tmp_path, tmp_path)
    assert is_path_within(tmp_path / "a" / "b", tmp_path)
    assert is_path_within(tmp_path / "non" / "esiste.md", tmp_path)


def test_dot_dot_is_resolved_before_the_check(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    assert not is_path_within(root / ".." / "raw" / "x.md", root)
    assert is_path_within(root / "note" / ".." / "x.md", root)


def test_a_sibling_with_the_same_prefix_is_outside(tmp_path: Path) -> None:
    """``/a/wiki`` non contiene ``/a/wiki-raw``: è un confine di percorso, non di stringa."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki-raw").mkdir()
    assert not is_path_within(tmp_path / "wiki-raw" / "x.md", tmp_path / "wiki")


def test_a_symlink_pointing_outside_is_outside(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    secret = tmp_path / "segreto.md"
    secret.write_text("x", encoding="utf-8")
    (root / "scorciatoia.md").symlink_to(secret)
    assert not is_path_within(root / "scorciatoia.md", root)


def test_a_root_reached_through_a_symlink_still_contains_its_files(tmp_path: Path) -> None:
    real = tmp_path / "reale"
    (real / "sub").mkdir(parents=True)
    link = tmp_path / "collegamento"
    link.symlink_to(real, target_is_directory=True)
    assert is_path_within(link / "sub", link)
    assert is_path_within((link / "sub").resolve(), link, path_resolved=True)


def test_a_symlink_loop_is_outside_not_an_exception(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "a").symlink_to(root / "b")
    (root / "b").symlink_to(root / "a")
    # Nessuna eccezione, mai un 500. Il *valore* qui dipende dalla versione:
    # su 3.13+ ``resolve(strict=False)`` attraversa il loop senza sollevare e il
    # percorso resta sotto la radice; su 3.11 solleva ``RuntimeError``, e che
    # quello diventi un no lo fissa il test qui sotto, per ogni versione.
    assert isinstance(is_path_within(root / "a" / "x", root), bool)


@pytest.mark.parametrize("error", [OSError, RuntimeError])
@pytest.mark.parametrize("which", ["path", "root"])
def test_a_resolution_error_is_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception], which: str
) -> None:
    """Qualunque errore di ``resolve()`` — del percorso o della radice — è un no.

    ``RuntimeError`` è quello che 3.11 solleva su un loop di symlink,
    ``OSError`` un permesso negato o un nome troppo lungo: la docstring
    promette «nel dubbio si sta fuori», e il loop vero sopra non lo prova su
    tutte le versioni.
    """
    root = tmp_path / "wiki"
    root.mkdir()
    target = root / "x.md"
    boom = target if which == "path" else root
    real_resolve = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        if self == boom:
            raise error("simulato")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert is_path_within(target, root) is False


def test_garbage_is_outside(tmp_path: Path) -> None:
    assert not is_path_within(None, tmp_path)  # type: ignore[arg-type]
    assert not is_path_within(tmp_path, None)  # type: ignore[arg-type]


def test_a_root_created_after_a_first_check_is_seen(tmp_path: Path) -> None:
    """Niente cache: una radice che cambia a runtime si rilegge ogni volta."""
    root = tmp_path / "wiki"
    assert is_path_within(root / "x.md", root)
    root.mkdir()
    real = tmp_path / "altrove"
    real.mkdir()
    root.rmdir()
    root.symlink_to(real, target_is_directory=True)
    assert is_path_within(real / "x.md", root)
