"""I file fra due Jenny: a pezzi, in ordine, interi o per niente."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest

from jenny.peers import files
from jenny.peers.link import PeerError
from tests.peers.harness import pair
from tests.support.aio import wait_until


def _payload(size: int) -> bytes:
    return os.urandom(size)


async def test_a_file_of_several_parts_arrives_whole(two, tmp_path: Path):
    a, b = two
    await pair(a, b)
    data = _payload(files.PART_BYTES * 2 + 1234)
    source = a.workspace / "report.pdf"
    source.write_bytes(data)

    info = await a.link.send_file(b.id, source)
    assert info["size"] == len(data)
    assert info["sha256"] == hashlib.sha256(data).hexdigest()

    await wait_until(lambda: b.link.received_file(a.id, info["fid"]) is not None, timeout=10)
    got = b.link.received_file(a.id, info["fid"])
    assert got is not None
    landed = b.workspace / got["path"]
    assert landed.read_bytes() == data
    assert landed.parent == b.workspace / "uploads" / "peers" / "Personale"
    # La copia in uscita sparisce con l'ultimo pezzo, e i pezzi in arrivo pure.
    await wait_until(lambda: not (files.outgoing_dir(a.workspace) / info["fid"]).exists(),
                     timeout=10)
    assert not (files.incoming_dir(b.workspace) / info["fid"]).exists()


async def test_a_file_too_large_is_refused_before_the_queue(two, tmp_path: Path):
    a, b = two
    await pair(a, b)
    big = a.workspace / "big.bin"
    with big.open("wb") as fh:
        fh.truncate(files.MAX_FILE_BYTES + 1)
    with pytest.raises(PeerError) as err:
        await a.link.send_file(b.id, big)
    assert err.value.code == "file_too_large"
    assert a.link.store.queued_count(b.id) == 0


def test_a_wrong_fingerprint_drops_the_file(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = _payload(1000)
    body = {
        "fid": "f" * 32, "i": 0, "n": 1, "size": len(data), "name": "x.pdf",
        "sha256": "0" * 64, "data": base64.b64encode(data).decode(),
    }
    with pytest.raises(files.FileError) as err:
        files.receive_part(workspace, "a" * 32, "Lavoro", body)
    assert err.value.code == "bad_file"
    assert not (workspace / "uploads").exists() or not any((workspace / "uploads").rglob("*.pdf"))


def test_a_part_of_the_wrong_length_is_refused(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    body = {
        "fid": "f" * 32, "i": 0, "n": 2, "size": files.PART_BYTES + 10, "name": "x.pdf",
        "sha256": "0" * 64, "data": base64.b64encode(b"short").decode(),
    }
    with pytest.raises(files.FileError) as err:
        files.receive_part(workspace, "a" * 32, "Lavoro", body)
    assert err.value.code == "bad_part"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("/abs/path/report.pdf", "report.pdf"),
        ("..", "file"),
        ("", "file"),
        ("fattura marzo (1).pdf", "fattura marzo (1).pdf"),
        ("a\x00b.pdf", "a_b.pdf"),
    ],
)
def test_names_never_leave_their_folder(raw: str, expected: str):
    assert files.safe_name(raw) == expected


def test_same_name_twice_keeps_both(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = []
    for content in (b"one", b"two"):
        body = {
            "fid": files.new_id(), "i": 0, "n": 1, "size": len(content), "name": "x.pdf",
            "sha256": hashlib.sha256(content).hexdigest(),
            "data": base64.b64encode(content).decode(),
        }
        paths.append(files.receive_part(workspace, "a" * 32, "Lavoro", body)["path"])
    assert paths == ["uploads/peers/Lavoro/x.pdf", "uploads/peers/Lavoro/x (2).pdf"]
