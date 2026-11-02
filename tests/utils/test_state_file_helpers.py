"""Le reti dei file di stato in ``utils/path.py``: lettura JSONL, backup, quarantena."""

from __future__ import annotations

from pathlib import Path

from jenny.utils.path import iter_jsonl, quarantine, rotate_valid_json_backup


def test_a_jsonl_reader_keeps_the_objects_and_names_the_rest() -> None:
    bad: list[str] = []
    lines = ['{"a": 1}', "", "  ", '{"b": 2', "[1, 2]", '"x"', '{"c": 3}\n']
    assert list(iter_jsonl(lines, on_bad=bad.append)) == [{"a": 1}, {"c": 3}]
    assert bad == ['{"b": 2', "[1, 2]", '"x"']


def test_the_backup_follows_only_a_file_that_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    rotate_valid_json_backup(path)
    assert not (tmp_path / "jobs.json.bak").exists()
    path.write_text('{"v": 1}', encoding="utf-8")
    rotate_valid_json_backup(path)
    path.write_text("{rotto", encoding="utf-8")
    rotate_valid_json_backup(path)
    assert (tmp_path / "jobs.json.bak").read_text(encoding="utf-8") == '{"v": 1}'


def test_two_quarantines_in_the_same_second_keep_both(tmp_path: Path) -> None:
    path = tmp_path / "board.json"
    kept = []
    for text in ("primo", "secondo"):
        path.write_text(text, encoding="utf-8")
        target = quarantine(path)
        assert target is not None and target.name.startswith("board.corrupt-")
        assert target.suffix == ".json"
        kept.append(target)
    assert not path.exists()
    assert sorted(p.read_text(encoding="utf-8") for p in kept) == ["primo", "secondo"]
