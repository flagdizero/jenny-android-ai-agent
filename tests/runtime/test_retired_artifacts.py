"""La spazzata dei file di un lavoratore ritirato (D3 del piano).

Tre proprieta': i file ritirati spariscono; quel che l'utente ha scritto
accanto resta; la seconda passata non fa niente e non lo dice.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger as loguru_logger

from jenny.runtime.retired_artifacts import sweep_retired_artifacts


def _legacy_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "memory").mkdir(parents=True)
    (ws / "sessions").mkdir()
    (ws / "memory" / "WIKI.md").write_text("# Wiki Directory\n", encoding="utf-8")
    (ws / "memory" / ".atlas_state.json").write_text('{"fingerprint": "x"}', encoding="utf-8")
    for stamp in ("20260901-192635", "20260831-132809"):
        (ws / "sessions" / f"atlas_{stamp}.jsonl").write_text("{}\n", encoding="utf-8")
    # Quel che deve restare.
    (ws / "memory" / "MEMORY.md").write_text("- un fatto\n", encoding="utf-8")
    (ws / "memory" / "WIKI_POLICY.md").write_text("mie regole\n", encoding="utf-8")
    (ws / "memory" / "history.jsonl").write_text("{}\n", encoding="utf-8")
    (ws / "sessions" / "unified_default.jsonl").write_text("{}\n", encoding="utf-8")
    (ws / "sessions" / "dream_20260901-092817.jsonl").write_text("{}\n", encoding="utf-8")
    return ws


def test_the_retired_files_go_and_the_users_stay(tmp_path) -> None:
    ws = _legacy_workspace(tmp_path)

    removed = {p.relative_to(ws).as_posix() for p in sweep_retired_artifacts(ws)}

    assert removed == {
        "memory/WIKI.md",
        "memory/.atlas_state.json",
        "sessions/atlas_20260831-132809.jsonl",
        "sessions/atlas_20260901-192635.jsonl",
    }
    assert sorted(p.name for p in (ws / "memory").iterdir()) == [
        "MEMORY.md", "WIKI_POLICY.md", "history.jsonl",
    ]
    assert sorted(p.name for p in (ws / "sessions").iterdir()) == [
        "dream_20260901-092817.jsonl", "unified_default.jsonl",
    ]


def test_the_second_pass_does_nothing_and_says_nothing(tmp_path) -> None:
    ws = _legacy_workspace(tmp_path)
    sweep_retired_artifacts(ws)
    records: list[str] = []
    handler = loguru_logger.add(lambda m: records.append(str(m)), level="INFO")
    try:
        removed = sweep_retired_artifacts(ws)
    finally:
        loguru_logger.remove(handler)

    assert removed == []
    assert records == []


def test_a_fresh_workspace_is_fine(tmp_path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()

    assert sweep_retired_artifacts(ws) == []


def test_the_map_layout_goes_with_the_map(tmp_path) -> None:
    """Gli spilli della mappa a grafo: la mappa e' uscita dalla casa con la
    bacheca, e il file non lo legge piu' nessuno. Il resto di ``.jenny/`` resta."""
    ws = tmp_path / "workspace"
    (ws / ".jenny").mkdir(parents=True)
    (ws / ".jenny" / "map-layout.json").write_text('{"piante": {}}', encoding="utf-8")
    (ws / ".jenny" / "internal.json").write_text("{}", encoding="utf-8")
    removed = {p.relative_to(ws).as_posix() for p in sweep_retired_artifacts(ws)}
    assert removed == {".jenny/map-layout.json"}
    assert (ws / ".jenny" / "internal.json").exists()
