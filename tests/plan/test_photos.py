"""Le foto del quaderno e il ritorno indietro a tre vie."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jenny.plan import photos as photos_mod
from jenny.plan.photos import (
    GROUP_BOARD,
    GROUP_JOURNAL,
    GROUP_OTHER,
    GROUP_PAGES,
    GROUP_SOURCES,
    PlanPhotos,
    RollbackError,
    group_of,
)

# Un mtime lontano nel passato: i file così datati non sono «a rischio di
# corsa» rispetto alla foto precedente, quindi il loro hash si può riusare.
OLD_NS = 1_600_000_000 * 1_000_000_000


def _write(root: Path, rel: str, text: str, *, mtime_ns: int | None = OLD_NS) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


@pytest.fixture()
def nb(tmp_path: Path) -> Path:
    root = tmp_path / "wikis" / "piante"
    _write(root, "AGENTS.md", "id: piante")
    _write(root, "wiki/index.md", "# indice")
    _write(root, "wiki/acero.md", "acero v1")
    _write(root, "wiki/ginkgo.md", "ginkgo v1")
    _write(root, "raw/journal/20261023.md", "diario mattina")
    (root / "raw" / "research").mkdir(parents=True)
    _write(root, "board/board.json", '{"v": 1}')
    return root


@pytest.fixture()
def photos(tmp_path: Path, nb: Path) -> PlanPhotos:
    return PlanPhotos(tmp_path / "plans" / "p_0123456789ab", nb)


def _journal(tmp_path: Path) -> Path:
    return tmp_path / "plans" / "p_0123456789ab" / "rollback.journal"


# ── Gruppi ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "group"),
    [
        ("wiki/acero.md", GROUP_PAGES),
        ("wiki/sub/x.md", GROUP_PAGES),
        ("raw/research/acero.md", GROUP_SOURCES),
        ("raw/articles/x.md", GROUP_SOURCES),
        ("raw/journal/20261023.md", GROUP_JOURNAL),
        ("board/board.json", GROUP_BOARD),
        ("board/cards/c1.json", GROUP_BOARD),
        ("AGENTS.md", GROUP_OTHER),
        ("log/2026.md", GROUP_OTHER),
        ("wikipedia.md", GROUP_OTHER),
    ],
)
def test_group_of(path: str, group: str) -> None:
    assert group_of(path) == group


# ── Foto ─────────────────────────────────────────────────────────────────────


def test_photos_live_in_the_plan_dir_not_in_the_notebook(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    photo = photos.take("start")
    assert (tmp_path / "plans" / "p_0123456789ab" / "photos" / "manifests").is_dir()
    assert "raw/journal/20261023.md" in photos.files(photo)
    assert not any(p.startswith("photos") for p in photos.files(photo))


def test_two_identical_photos_are_one(photos: PlanPhotos) -> None:
    first = photos.take("a")
    assert photos.take("b") == first
    assert len(photos.engine.list_snapshots()) == 1


def test_diff(nb: Path, photos: PlanPhotos) -> None:
    a = photos.take("a")
    _write(nb, "wiki/acero.md", "acero v2", mtime_ns=None)
    _write(nb, "wiki/quercia.md", "nuova", mtime_ns=None)
    (nb / "wiki" / "ginkgo.md").unlink()
    b = photos.take("b")
    assert photos.diff(a, b) == {
        "added": ["wiki/quercia.md"],
        "changed": ["wiki/acero.md"],
        "removed": ["wiki/ginkgo.md"],
    }
    assert photos.diff(b, b) == {"added": [], "changed": [], "removed": []}


def _count_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reads: list[str] = []
    original = Path.read_bytes

    def _spy(self: Path) -> bytes:
        reads.append(self.name)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", _spy)
    return reads


def test_an_unchanged_file_is_not_read_again(
    nb: Path, photos: PlanPhotos, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = photos.take("a")
    reads = _count_reads(monkeypatch)
    _write(nb, "wiki/acero.md", "acero, seconda versione")  # stesso mtime, altra taglia
    b = photos.take("b")
    assert reads == ["acero.md"]
    assert photos.diff(a, b)["changed"] == ["wiki/acero.md"]
    # Niente di cambiato: nessuna lettura, e la foto è l'ultima.
    reads.clear()
    assert photos.take("c") == b
    assert reads == []


def test_same_size_but_new_mtime_is_hashed_again(
    nb: Path, photos: PlanPhotos, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = photos.take("a")
    reads = _count_reads(monkeypatch)
    _write(nb, "wiki/acero.md", "acero v9", mtime_ns=OLD_NS + 1)  # stessa taglia
    b = photos.take("b")
    assert reads == ["acero.md"]
    assert photos.diff(a, b)["changed"] == ["wiki/acero.md"]


def test_a_file_written_just_before_the_photo_is_always_read(
    nb: Path, photos: PlanPhotos, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Il caso «racy»: scritto ora, poi riscritto con la stessa taglia e lo
    # stesso mtime. La sua coppia (size, mtime) non prova niente, si rilegge.
    _write(nb, "wiki/acero.md", "acero v1", mtime_ns=None)
    stamp = (nb / "wiki" / "acero.md").stat().st_mtime_ns
    a = photos.take("a")
    reads = _count_reads(monkeypatch)
    _write(nb, "wiki/acero.md", "acero v7", mtime_ns=stamp)
    b = photos.take("b")
    assert reads == ["acero.md"]
    assert photos.diff(a, b)["changed"] == ["wiki/acero.md"]


def test_engine_default_still_reads_everything(nb: Path, photos: PlanPhotos) -> None:
    # Il riuso è opt-in: lo snapshot del workspace resta com'era.
    photos.take("a")
    _write(nb, "wiki/acero.md", "acero v2")
    manifest = photos.engine.create_snapshot(trigger="t")
    assert manifest is not None


# ── Ritorno: la tabella a tre vie ────────────────────────────────────────────


def _run_plan(nb: Path, photos: PlanPhotos) -> tuple[str, str]:
    """Foto A, poi il «piano» cambia il quaderno, poi foto B."""
    a = photos.take("start")
    _write(nb, "wiki/acero.md", "acero v2", mtime_ns=None)  # cambiato
    _write(nb, "wiki/quercia.md", "quercia", mtime_ns=None)  # aggiunto
    (nb / "wiki" / "ginkgo.md").unlink()  # cancellato
    _write(nb, "raw/research/acero.md", "fonte", mtime_ns=None)  # aggiunto, fonti
    _write(nb, "raw/journal/20261023.md", "diario mattina\nsera", mtime_ns=None)
    _write(nb, "board/board.json", '{"v": 2}', mtime_ns=None)
    b = photos.take("end")
    return a, b


def test_rollback_returns_the_notebook_to_a(tmp_path: Path, nb: Path, photos: PlanPhotos) -> None:
    a, b = _run_plan(nb, photos)
    report = photos.rollback(a, b, journal_path=_journal(tmp_path))

    assert report.restored == ["board/board.json", "wiki/acero.md", "wiki/ginkgo.md"]
    assert report.removed == ["raw/research/acero.md", "wiki/quercia.md"]
    assert report.excluded == ["raw/journal/20261023.md"]
    assert report.already == report.left == report.kept == []
    assert _read(nb, "wiki/acero.md") == "acero v1"
    assert _read(nb, "wiki/ginkgo.md") == "ginkgo v1"
    assert not (nb / "wiki" / "quercia.md").exists()
    assert _read(nb, "board/board.json") == '{"v": 1}'
    # Il diario non torna indietro.
    assert _read(nb, "raw/journal/20261023.md") == "diario mattina\nsera"
    # La cartella dello scaffold resta anche vuota.
    assert (nb / "raw" / "research").is_dir()
    assert not _journal(tmp_path).exists()
    assert report.before == b
    assert report.after is not None and report.after != b
    assert report.counts["restored"] == 3


def test_changed_after_the_plan_is_left_and_already_is_reported(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    a, b = _run_plan(nb, photos)
    _write(nb, "wiki/acero.md", "acero toccata dopo", mtime_ns=None)  # n ≠ e, n ≠ t → left
    _write(nb, "wiki/quercia.md", "quercia toccata dopo", mtime_ns=None)  # left
    _write(nb, "wiki/ginkgo.md", "ginkgo v1", mtime_ns=None)  # n == t → already
    _write(nb, "wiki/nuova-dopo.md", "x", mtime_ns=None)  # solo adesso: t == e → fuori conto

    report = photos.rollback(a, b, journal_path=_journal(tmp_path))

    assert report.left == ["wiki/acero.md", "wiki/quercia.md"]
    assert report.already == ["wiki/ginkgo.md"]
    assert "wiki/nuova-dopo.md" not in json.dumps(report.to_dict())
    assert _read(nb, "wiki/acero.md") == "acero toccata dopo"
    assert _read(nb, "wiki/quercia.md") == "quercia toccata dopo"
    assert _read(nb, "wiki/nuova-dopo.md") == "x"


def test_a_file_deleted_by_the_plan_and_still_absent_is_restored(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    a, b = _run_plan(nb, photos)
    report = photos.rollback(a, b, journal_path=_journal(tmp_path))
    assert "wiki/ginkgo.md" in report.restored


def test_kept_groups_are_skipped(tmp_path: Path, nb: Path, photos: PlanPhotos) -> None:
    a, b = _run_plan(nb, photos)
    report = photos.rollback(
        a, b, journal_path=_journal(tmp_path), keep_groups=[GROUP_SOURCES, GROUP_BOARD]
    )
    assert report.kept == ["board/board.json", "raw/research/acero.md"]
    assert _read(nb, "raw/research/acero.md") == "fonte"
    assert _read(nb, "board/board.json") == '{"v": 2}'
    assert _read(nb, "wiki/acero.md") == "acero v1"


def test_unknown_group_is_refused(tmp_path: Path, nb: Path, photos: PlanPhotos) -> None:
    a, b = _run_plan(nb, photos)
    with pytest.raises(ValueError):
        photos.rollback(a, b, journal_path=_journal(tmp_path), keep_groups=["journal"])


def test_dry_run_writes_nothing_and_reports_the_same(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    a, b = _run_plan(nb, photos)
    before = {p.relative_to(nb).as_posix(): p.read_bytes() for p in nb.rglob("*") if p.is_file()}

    preview = photos.rollback(a, b, journal_path=_journal(tmp_path), dry_run=True)

    after = {p.relative_to(nb).as_posix(): p.read_bytes() for p in nb.rglob("*") if p.is_file()}
    assert after == before
    assert not _journal(tmp_path).exists()
    assert preview.after is None and preview.dry_run
    real = photos.rollback(a, b, journal_path=_journal(tmp_path))
    for outcome in ("restored", "removed", "already", "left", "kept", "excluded"):
        assert getattr(preview, outcome) == getattr(real, outcome)


def test_undo_is_the_same_function_the_other_way(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    a, b = _run_plan(nb, photos)
    first = photos.rollback(a, b, journal_path=_journal(tmp_path))
    assert first.after is not None
    undo = photos.rollback(first.before, first.after, journal_path=_journal(tmp_path))
    assert _read(nb, "wiki/acero.md") == "acero v2"
    assert _read(nb, "wiki/quercia.md") == "quercia"
    assert not (nb / "wiki" / "ginkgo.md").exists()
    assert _read(nb, "raw/research/acero.md") == "fonte"
    # Stesso contenuto della foto B (l'id no: dipende da genitore e ora).
    assert undo.after is not None
    assert photos.files(undo.after) == photos.files(b)


def test_nested_dirs_left_empty_are_removed_but_not_the_root(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    a = photos.take("start")
    _write(nb, "wiki/specie/aceri/rosso.md", "rosso", mtime_ns=None)
    _write(nb, "extra/solo.md", "x", mtime_ns=None)
    b = photos.take("end")
    photos.rollback(a, b, journal_path=_journal(tmp_path))
    assert not (nb / "wiki" / "specie").exists()
    assert not (nb / "extra").exists()
    assert (nb / "wiki").is_dir()
    assert nb.is_dir()


def test_a_file_where_a_dir_was_and_back(tmp_path: Path, nb: Path, photos: PlanPhotos) -> None:
    _write(nb, "wiki/specie/acero.md", "acero dentro")
    a = photos.take("start")
    (nb / "wiki" / "specie" / "acero.md").unlink()
    (nb / "wiki" / "specie").rmdir()
    _write(nb, "wiki/specie", "ora sono un file", mtime_ns=None)
    b = photos.take("end")
    photos.rollback(a, b, journal_path=_journal(tmp_path))
    assert _read(nb, "wiki/specie/acero.md") == "acero dentro"


# ── Sicurezza ────────────────────────────────────────────────────────────────


def test_unsafe_path_is_refused_before_any_write(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    a, b = _run_plan(nb, photos)
    # La cartella delle pagine diventa un link simbolico che porta fuori: il
    # ritorno deve rimettere ``wiki/ginkgo.md``, cioè scrivere attraverso il link.
    outside = tmp_path / "fuori"
    outside.mkdir()
    (nb / "wiki").rename(tmp_path / "wiki-vera")
    (nb / "wiki").symlink_to(outside, target_is_directory=True)
    before_board = _read(nb, "board/board.json")

    with pytest.raises(RollbackError):
        photos.rollback(a, b, journal_path=_journal(tmp_path))

    assert list(outside.iterdir()) == []
    assert _read(nb, "board/board.json") == before_board
    assert not _journal(tmp_path).exists()


def test_a_journal_with_a_traversal_is_refused(
    tmp_path: Path, nb: Path, photos: PlanPhotos
) -> None:
    journal = _journal(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"ops": [{"path": "../fuori.md", "hash": None}]}), "utf-8")
    with pytest.raises(RollbackError):
        photos.recover_pending(journal)
    assert journal.exists()


def test_the_journal_itself_never_gets_an_op(tmp_path: Path, nb: Path, photos: PlanPhotos) -> None:
    journal = _journal(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps({"ops": [{"path": "raw/journal/20261023.md", "hash": None}]}), "utf-8"
    )
    with pytest.raises(RollbackError):
        photos.recover_pending(journal)
    assert (nb / "raw" / "journal" / "20261023.md").exists()


# ── Crollo a metà e recupero ─────────────────────────────────────────────────


def test_crash_mid_rollback_then_recovery(
    tmp_path: Path, nb: Path, photos: PlanPhotos, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _run_plan(nb, photos)
    journal = _journal(tmp_path)
    original = PlanPhotos._apply_one
    done: list[str] = []

    def _crash_after_two(self: PlanPhotos, op) -> None:
        if len(done) == 2:
            raise KeyboardInterrupt("gateway killed")
        original(self, op)
        done.append(op.path)

    monkeypatch.setattr(PlanPhotos, "_apply_one", _crash_after_two)
    with pytest.raises(KeyboardInterrupt):
        photos.rollback(a, b, journal_path=journal)
    monkeypatch.setattr(PlanPhotos, "_apply_one", original)

    # A metà: il journal c'è, e una parte delle operazioni è già sul disco.
    assert journal.exists()
    assert len(json.loads(journal.read_text("utf-8"))["ops"]) == 5
    assert done == ["raw/research/acero.md", "wiki/quercia.md"]
    assert _read(nb, "wiki/acero.md") == "acero v2"

    # Al boot: un nuovo oggetto, come dopo un riavvio.
    fresh = PlanPhotos(photos.plan_dir, nb)
    assert fresh.recover_pending(journal) is True
    assert not journal.exists()
    assert _read(nb, "wiki/acero.md") == "acero v1"
    assert _read(nb, "wiki/ginkgo.md") == "ginkgo v1"
    assert not (nb / "wiki" / "quercia.md").exists()
    assert fresh.recover_pending(journal) is False
    assert fresh.diff(a, fresh.take("recovered"))["changed"] == ["raw/journal/20261023.md"]


def test_ops_are_deletes_first(
    tmp_path: Path, nb: Path, photos: PlanPhotos, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _run_plan(nb, photos)
    seen: list[bool] = []
    original = PlanPhotos._apply_one

    def _spy(self: PlanPhotos, op) -> None:
        seen.append(op.hash is None)
        original(self, op)

    monkeypatch.setattr(PlanPhotos, "_apply_one", _spy)
    photos.rollback(a, b, journal_path=_journal(tmp_path))
    assert seen == sorted(seen, reverse=True)
    assert seen.count(True) == 2


def test_module_exposes_keepable_groups() -> None:
    assert set(photos_mod.KEEPABLE_GROUPS) == {"pages", "sources", "board", "other"}
