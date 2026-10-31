"""L'impronta della WebUI servita: quel che la pagina confronta alla
riconnessione per accorgersi di un aggiornamento (``jenny/webui/ui_version.py``).

Il 21/10/2026 due APK diverse avevano lo stesso ``versionCode``: l'impronta
viene dal contenuto dei file, non dal numero.
"""

from __future__ import annotations

import pytest

from jenny.webui import ui_version


class _Package:
    """I file della WebUI nel pacchetto, e quali si sono letti."""

    def __init__(self) -> None:
        self.files = {
            "index.html": b"<html>",
            "assets/home-board.js": b"export const a = 1;",
            "assets/i18n/it.json": b"{}",
            "assets/vendor/mermaid@10/mermaid.min.js": b"vendor bytes",
            "assets/fonts/x.woff2": b"font",
        }
        self.read: list[str] = []

    def read_asset(self, package: str, name: str) -> bytes | None:
        self.read.append(name)
        return self.files.get(name)


@pytest.fixture
def package(monkeypatch: pytest.MonkeyPatch) -> _Package:
    pkg = _Package()
    monkeypatch.setattr("jenny.utils.android_assets.ui_sources",
                        lambda: {rel: ("jenny.templates.ui", rel) for rel in pkg.files})
    monkeypatch.setattr("jenny.utils.android_assets.read_asset", pkg.read_asset)
    return pkg


def test_the_same_files_give_the_same_fingerprint(package) -> None:
    assert ui_version.compute() == ui_version.compute()
    assert len(ui_version.compute()) == 24


def test_a_changed_script_or_translation_changes_it(package) -> None:
    before = ui_version.compute()
    package.files["assets/home-board.js"] = b"export const a = 2;"
    after_js = ui_version.compute()
    package.files["assets/i18n/it.json"] = b'{"a": 1}'
    assert len({before, after_js, ui_version.compute()}) == 3


def test_vendor_and_fonts_count_by_name_only(package) -> None:
    before = ui_version.compute()
    package.files["assets/vendor/mermaid@10/mermaid.min.js"] = b"other vendor bytes"
    package.files["assets/fonts/x.woff2"] = b"other font"
    package.read.clear()
    assert ui_version.compute() == before
    assert "assets/vendor/mermaid@10/mermaid.min.js" not in package.read
    # Una libreria nuova cambia nome di cartella, e quello conta.
    package.files["assets/vendor/mermaid@11/mermaid.min.js"] = b"vendor bytes"
    assert ui_version.compute() != before


def test_it_is_empty_until_computed(package, monkeypatch) -> None:
    monkeypatch.setattr(ui_version, "_value", "")
    assert ui_version.current() == ""
    ui_version._run()
    assert ui_version.current() == ui_version.compute()


def test_a_failed_computation_leaves_it_empty(monkeypatch) -> None:
    monkeypatch.setattr(ui_version, "_value", "")

    def explode() -> str:
        raise OSError("apk unreadable")

    monkeypatch.setattr(ui_version, "compute", explode)
    ui_version._run()
    assert ui_version.current() == ""


def test_warm_starts_one_thread_per_process(monkeypatch) -> None:
    started: list[object] = []

    class _Thread:
        def __init__(self, *, target, name, daemon) -> None:
            started.append(target)

        def start(self) -> None:
            pass

    monkeypatch.setattr(ui_version, "_started", False)
    monkeypatch.setattr(ui_version.threading, "Thread", _Thread)
    ui_version.warm()
    ui_version.warm()
    assert len(started) == 1
