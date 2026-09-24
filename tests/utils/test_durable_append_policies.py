"""Chi tollera un ``fsync`` fallito e chi no: le due politiche, nei chiamanti.

Il transcript della WebUI tollera (le righe sono nel file, e far fallire un
turno per un ``fsync`` sarebbe peggio); la copia della coda di un progetto no,
perché subito dopo la sessione viene troncata e la copia deve essere su disco
*prima*. Fissato prima di raccogliere le tre scritture in un helper solo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from jenny.agent.memory import Consolidator, MemoryStore
from jenny.session.manager import SessionManager


def _fd_path(fd: int) -> str:
    if sys.platform.startswith("linux"):
        return os.readlink(f"/proc/self/fd/{fd}")
    import fcntl

    buf = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))  # type: ignore[attr-defined]
    return buf.split(b"\0", 1)[0].decode()


def _fail_fsync_for(monkeypatch, needle: str) -> list[str]:
    failed: list[str] = []
    real = os.fsync

    def fsync(fd: int):
        path = _fd_path(fd)
        if needle in path:
            failed.append(path)
            raise OSError("disco pieno")
        return real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    return failed


async def test_a_project_copy_that_cannot_be_synced_keeps_the_session(tmp_path: Path, monkeypatch) -> None:
    store = MemoryStore(tmp_path)
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(side_effect=RuntimeError("LLM giù"))
    sessions = SessionManager(store.workspace)
    consolidator = Consolidator(
        store=store, provider=provider, model="m", sessions=sessions,
        context_window_tokens=1000, build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]), max_completion_tokens=100,
    )
    (tmp_path / "wikis" / "patreon" / "wiki").mkdir(parents=True)
    session = sessions.get_or_create("project:patreon")
    for i in range(10):
        session.add_message("user", f"user msg {i}")
        session.add_message("assistant", f"assistant msg {i}")
    sessions.save(session)
    failed = _fail_fsync_for(monkeypatch, "raw/compacted")

    await consolidator.compact_idle_session("project:patreon", max_suffix=4)

    assert failed, "la copia nel progetto non e' passata dal fsync"
    reloaded = SessionManager(store.workspace).get_or_create("project:patreon")
    assert len(reloaded.messages) == 20, "senza la copia su disco la coda non si tronca"


def test_the_transcript_tolerates_an_fsync_failure(tmp_path: Path, monkeypatch) -> None:
    from jenny.webui import transcript_store

    monkeypatch.setattr(transcript_store, "webui_transcript_path",
                        lambda key: tmp_path / "transcripts" / f"{key}.jsonl")
    failed = _fail_fsync_for(monkeypatch, "transcripts")

    transcript_store.append_transcript_object("websocket:default", {"event": "message", "text": "ciao"})

    assert failed
    assert "ciao" in (tmp_path / "transcripts" / "websocket:default.jsonl").read_text(encoding="utf-8")
