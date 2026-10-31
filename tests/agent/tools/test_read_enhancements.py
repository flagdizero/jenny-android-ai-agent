"""Tests for ReadFileTool enhancements: description fix, read dedup, PDF support, device blacklist, office docs."""

import os

import pytest

from jenny.agent.tools import file_state
from jenny.agent.tools.filesystem import ReadFileTool, WriteFileTool

# ---------------------------------------------------------------------------
# Description fix
# ---------------------------------------------------------------------------

class TestReadDescriptionFix:

    def test_description_mentions_image_support(self):
        tool = ReadFileTool()
        assert "image" in tool.description.lower()


# ---------------------------------------------------------------------------
# Read deduplication
# ---------------------------------------------------------------------------

class TestReadDedup:
    """Same file + same offset/limit + unchanged mtime -> short stub."""

    @pytest.fixture()
    def tool(self, tmp_path):
        return ReadFileTool(workspace=tmp_path)

    @pytest.fixture()
    def write_tool(self, tmp_path):
        return WriteFileTool(workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_second_read_returns_unchanged_stub(self, tool, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
        first = await tool.execute(path=str(f))
        assert "line 0" in first
        second = await tool.execute(path=str(f))
        assert "unchanged" in second.lower()
        # Stub should not contain file content
        assert "line 0" not in second

    @pytest.mark.asyncio
    async def test_read_after_external_modification_returns_full(self, tool, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("original", encoding="utf-8")
        await tool.execute(path=str(f))
        # Modify the file externally
        f.write_text("modified content", encoding="utf-8")
        second = await tool.execute(path=str(f))
        assert "modified content" in second

    @pytest.mark.asyncio
    async def test_different_offset_returns_full(self, tool, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("\n".join(f"line {i}" for i in range(1, 21)), encoding="utf-8")
        await tool.execute(path=str(f), offset=1, limit=5)
        second = await tool.execute(path=str(f), offset=6, limit=5)
        # Different offset → full read, not stub
        assert "line 6" in second

    @pytest.mark.asyncio
    async def test_first_read_after_write_returns_full_content(self, tool, write_tool, tmp_path):
        f = tmp_path / "fresh.txt"
        result = await write_tool.execute(path=str(f), content="hello")
        assert "Successfully" in result
        read_result = await tool.execute(path=str(f))
        assert "hello" in read_result
        assert "unchanged" not in read_result.lower()

    @pytest.mark.asyncio
    async def test_dedup_does_not_apply_to_images(self, tool, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-data")
        first = await tool.execute(path=str(f))
        assert isinstance(first, list)
        second = await tool.execute(path=str(f))
        # Images should always return full content blocks, not a stub
        assert isinstance(second, list)


# ---------------------------------------------------------------------------
# Cross-session isolation (issue #3571)
# ---------------------------------------------------------------------------
# Each session must keep its own read cache. When session A reads a file,
# session B reading the same file must still receive the full content, not
# the "[File unchanged since last read]" dedup stub. The stub is only valid
# within the session that first cached the read.

class TestReadDedupSessionIsolation:

    @pytest.mark.asyncio
    async def test_separate_sessions_do_not_share_dedup_state(self, tmp_path):
        f = tmp_path / "shared.txt"
        f.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")

        session_a_tool = ReadFileTool(workspace=tmp_path)
        session_b_tool = ReadFileTool(workspace=tmp_path)

        first = await session_a_tool.execute(path=str(f))
        assert "line 0" in first

        # Session B has never read this file before — it must see the full
        # content, not the dedup stub from session A.
        second = await session_b_tool.execute(path=str(f))
        assert "unchanged" not in second.lower(), (
            "Session B should not inherit session A's read-dedup state. "
            f"Got: {second!r}"
        )
        assert "line 0" in second

    @pytest.mark.asyncio
    async def test_shared_loop_tool_uses_bound_session_state(self, tmp_path):
        f = tmp_path / "shared.txt"
        f.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")

        # AgentLoop registers one shared ReadFileTool instance. The session
        # boundary is the task-local FileStates binding, not the tool object.
        shared_tool = ReadFileTool(workspace=tmp_path)
        session_a = file_state.FileStates()
        session_b = file_state.FileStates()

        token = file_state.bind_file_states(session_a)
        try:
            first = await shared_tool.execute(path=str(f))
            repeat = await shared_tool.execute(path=str(f))
        finally:
            file_state.reset_file_states(token)

        assert "line 0" in first
        assert "unchanged" in repeat.lower()

        token = file_state.bind_file_states(session_b)
        try:
            second_session_read = await shared_tool.execute(path=str(f))
        finally:
            file_state.reset_file_states(token)

        assert "unchanged" not in second_session_read.lower()
        assert "line 0" in second_session_read


# ---------------------------------------------------------------------------
# PDF support
# ---------------------------------------------------------------------------

class TestReadPdf:

    @pytest.fixture()
    def tool(self, tmp_path):
        return ReadFileTool(workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_pdf_blank_returns_no_text(self, tool, tmp_path):
        from pypdf import PdfWriter

        pdf_path = tmp_path / "test.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(str(pdf_path))

        result = await tool.execute(path=str(pdf_path))
        assert "no extractable text" in result

    @pytest.mark.asyncio
    async def test_pdf_pages_parameter_out_of_range(self, tool, tmp_path):
        from pypdf import PdfWriter

        pdf_path = tmp_path / "multi.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(str(pdf_path))

        result = await tool.execute(path=str(pdf_path), pages="5-10")
        assert "Error" in result
        assert "out of bounds" in result

    @pytest.mark.asyncio
    async def test_pdf_file_not_found_error(self, tool, tmp_path):
        result = await tool.execute(path=str(tmp_path / "nope.pdf"))
        assert "Error" in result
        assert "not found" in result


# ---------------------------------------------------------------------------
# Device path blacklist (Android/POSIX)
# ---------------------------------------------------------------------------

class TestReadDeviceBlacklist:

    @pytest.fixture()
    def tool(self):
        return ReadFileTool()

    @pytest.mark.asyncio
    async def test_dev_random_blocked(self, tool):
        result = await tool.execute(path="/dev/random")
        assert "Error" in result
        assert "blocked" in result.lower() or "device" in result.lower()

    @pytest.mark.asyncio
    async def test_dev_urandom_blocked(self, tool):
        result = await tool.execute(path="/dev/urandom")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_dev_zero_blocked(self, tool):
        result = await tool.execute(path="/dev/zero")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_proc_fd_blocked(self, tool):
        result = await tool.execute(path="/proc/self/fd/0")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_symlink_to_dev_zero_blocked(self, tmp_path):
        tool = ReadFileTool(workspace=tmp_path)
        link = tmp_path / "zero-link"
        link.symlink_to("/dev/zero")
        result = await tool.execute(path=str(link))
        assert "Error" in result
        assert "blocked" in result.lower() or "device" in result.lower()


# ---------------------------------------------------------------------------
# file_state: mtime-unchanged / content-changed fallback
# ---------------------------------------------------------------------------
# On filesystems with coarse mtime resolution (NTFS ~100ms, FAT 2s) a fast
# write-after-read can leave mtime unchanged. The content-hash fallback is
# what protects against stale-read warnings being false-negative on those
# platforms. Lock that behavior down here so nobody reverts it silently.

class TestFileStateHashFallback:

    def test_check_read_warns_when_content_changed_but_mtime_same(self, tmp_path):
        states = file_state.FileStates()
        f = tmp_path / "data.txt"
        f.write_text("original", encoding="utf-8")
        states.record_read(f)
        original_mtime = os.path.getmtime(f)

        f.write_text("modified", encoding="utf-8")
        os.utime(f, (original_mtime, original_mtime))
        assert os.path.getmtime(f) == original_mtime

        warning = states.check_read(f)
        assert warning is not None
        assert "modified" in warning.lower()

    def test_check_read_passes_when_content_and_mtime_unchanged(self, tmp_path):
        states = file_state.FileStates()
        f = tmp_path / "data.txt"
        f.write_text("stable", encoding="utf-8")
        states.record_read(f)

        assert states.check_read(f) is None


# ---------------------------------------------------------------------------
# Line-ending normalization
# ---------------------------------------------------------------------------
# ReadFileTool normalizes CRLF -> LF before line-splitting. This primarily
# helps Windows users whose checkouts carry CRLF line endings and whose
# subsequent StrReplace edits would otherwise miss on `\r` boundaries. The
# normalization applies on all platforms; these tests lock that in so the
# behavior is intentional and discoverable, not accidental.

class TestReadFileLineEndingNormalization:

    @pytest.fixture()
    def tool(self, tmp_path):
        return ReadFileTool(workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_crlf_is_normalized_to_lf(self, tool, tmp_path):
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
        result = await tool.execute(path=str(f))
        assert "\r" not in result
        assert "alpha" in result and "beta" in result and "gamma" in result

    @pytest.mark.asyncio
    async def test_lf_only_is_preserved(self, tool, tmp_path):
        f = tmp_path / "lf.txt"
        f.write_bytes(b"alpha\nbeta\ngamma\n")
        result = await tool.execute(path=str(f))
        assert "\r" not in result
        assert "alpha" in result and "beta" in result and "gamma" in result


class TestReadDescriptionUpdate:

    def test_description_mentions_pdf(self):
        tool = ReadFileTool()
        desc = tool.description.lower()
        assert "pdf" in desc

# ---------------------------------------------------------------------------
# Il presupposto del dedup, e cosa succede quando cade
# ---------------------------------------------------------------------------
# Lo stub "invariato dall'ultima lettura" e' vero sul *file* e afferma qualcosa
# sul *contesto*: che il contenuto e' gia' nella conversazione. Svuotata la
# conversazione — ``/new`` — quell'affermazione e' falsa, e il modello riceve
# una risposta che non contiene ne' il file ne' un modo di ottenerlo. Visto sul
# telefono il 23/08: la prima lettura di una sessione nuova tornava stub, e il
# modello se n'e' accorto da solo ("I don't have its content in this
# conversation") pagando un giro in piu'.

class TestDedupWhenTheConversationIsGone:

    @pytest.mark.asyncio
    async def test_dropping_a_session_makes_the_next_read_full_again(self, tmp_path):
        f = tmp_path / "page.md"
        f.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")
        store = file_state.FileStateStore()
        tool = ReadFileTool(workspace=tmp_path)

        token = file_state.bind_file_states(store.for_session("project:viaggio"))
        try:
            await tool.execute(path=str(f))
            repeat = await tool.execute(path=str(f))
        finally:
            file_state.reset_file_states(token)
        assert "unchanged" in repeat.lower(), "il dedup deve valere dentro una conversazione"

        store.drop("project:viaggio")

        token = file_state.bind_file_states(store.for_session("project:viaggio"))
        try:
            after_reset = await tool.execute(path=str(f))
        finally:
            file_state.reset_file_states(token)

        assert "line 0" in after_reset
        assert "unchanged" not in after_reset.lower()

    def test_dropping_one_session_leaves_the_others_alone(self):
        store = file_state.FileStateStore()
        keep = store.for_session("unified:default")
        store.for_session("project:viaggio")

        store.drop("project:viaggio")

        assert store.for_session("unified:default") is keep
        assert store.for_session("project:viaggio") is not keep

    def test_dropping_a_session_that_never_read_anything_is_not_an_error(self):
        file_state.FileStateStore().drop("project:mai-visto")

    @pytest.mark.asyncio
    async def test_the_stub_says_how_to_get_the_content_back(self, tmp_path):
        """Il dedup poggia su un presupposto che il tool non puo' verificare: se
        il contenuto sia ancora in conversazione. ``/new`` e' il caso in cui si sa
        con certezza che non c'e' (e li' si azzera), ma una compattazione lascia
        solo un suffisso e il contenuto puo' essere caduto oltre il confine.
        Per quei casi lo stub deve **dire come rimediare**, invece di lasciare al
        modello il compito di indovinarlo — che una volta gli e' riuscito e non e'
        una garanzia.
        """
        f = tmp_path / "page.md"
        f.write_text("contenuto\n", encoding="utf-8")
        tool = ReadFileTool(workspace=tmp_path)
        token = file_state.bind_file_states(file_state.FileStates())
        try:
            await tool.execute(path=str(f))
            stub = await tool.execute(path=str(f))
        finally:
            file_state.reset_file_states(token)

        assert "force=true" in stub
        # E resta riconoscibile a chi la classifica per il pannello dei subagent
        # (``agent/subagent_activity.py`` cerca questa frase).
        assert "unchanged since last read" in stub.lower()
