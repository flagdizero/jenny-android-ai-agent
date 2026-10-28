"""Una sessione del browser per agente: Jenny e ogni aiutante hanno la loro pagina.

Prima la sessione era una sola per tutta l'app: due aiutanti si rubavano la
pagina, e la chiusura di uno cancellava i cookie dell'altro. Qui si fissano le
regole della separazione — proprietario, slot (cioe' profilo Kotlin), attesa di
uno slot libero, chiusura a fine aiutante — e ``browser_read`` a pezzi, con il
bridge finto di ``test_browser.py``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

from jenny.agent.tools import browser
from jenny.agent.tools.browser import (
    BrowserCloseTool,
    BrowserDoTool,
    BrowserOpenTool,
    BrowserReadTool,
    BrowserSnapshotTool,
)
from jenny.config.tool_schemas import AndroidWebBrowserConfig
from tests.agent.tools.test_browser import FakeBridge, _allow_url, _install, _instance, _tool


@pytest.fixture(autouse=True)
def _clean_state():
    browser.reset_browser_state()
    yield
    browser.reset_browser_state()


async def _snap(owner: str, **over) -> dict:
    return await browser._call(
        object(), "snapshot", "full", "", 100, 30, timeout=5, owner=owner, **over
    )


class TestOneSessionPerOwner:
    async def test_two_helpers_get_two_pages_on_two_profiles(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool, owner="aaa").execute(url="https://uno.test/")
        await _tool(BrowserOpenTool, owner="bbb").execute(url="https://due.test/")
        a, b = holder["all"]
        assert a is not b
        assert {a.slot, b.slot} == {0, 1}
        assert ("open", "https://uno.test/", 30) in a.calls
        assert all(c[1] != "https://uno.test/" for c in b.calls if c[0] == "open")

    async def test_the_same_owner_reuses_its_page(self, monkeypatch):
        holder = _install(monkeypatch)
        await _snap("aaa")
        await _snap("aaa")
        assert len(holder["all"]) == 1

    async def test_close_closes_only_ones_own(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool, owner="aaa").execute(url="https://uno.test/")
        await _tool(BrowserOpenTool, owner="bbb").execute(url="https://due.test/")
        a, b = holder["all"]
        await _tool(BrowserCloseTool, owner="aaa").execute()
        assert a.closed == 1
        assert b.closed == 0
        assert _instance("bbb") is b

    async def test_the_reset_closes_them_all(self, monkeypatch):
        holder = _install(monkeypatch)
        await _snap("aaa")
        await _snap(browser.MAIN_OWNER)
        browser.reset_browser_state()
        assert [b.closed for b in holder["all"]] == [1, 1]
        assert browser._SESSIONS == {}
        assert browser._BUSY_SLOTS == set()

    async def test_an_idle_session_closes_without_touching_a_busy_one(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        holder = _install(monkeypatch)
        await _snap("idle", idle_s=0.1)
        for _ in range(6):
            await _snap("busy", idle_s=0.2)
            await asyncio.sleep(0.05)
        idle, busy = holder["all"]
        assert idle.closed == 1 and _instance("idle") is None
        assert busy.closed == 0 and _instance("busy") is busy

    async def test_calls_of_different_owners_do_not_wait_for_each_other(self, monkeypatch):
        """Il lucchetto e' della sessione: una pagina lenta di un aiutante non
        ferma quella di un altro."""
        entered = threading.Event()

        class Slow(FakeBridge):
            def snapshot(self, *a):
                if self.slot == 0:
                    entered.set()
                    time.sleep(0.4)
                return super().snapshot(*a)

        _install(monkeypatch, Slow)
        slow = asyncio.create_task(_snap("lento"))
        await asyncio.to_thread(entered.wait, 2)
        started = time.monotonic()
        await _snap("svelto")
        assert time.monotonic() - started < 0.3
        await slow


class TestThePolicyIsPerSession:
    """La politica dei clic che costano legge l'indice **della propria** pagina."""

    async def test_a_costly_button_on_one_page_does_not_block_another(self, monkeypatch):
        holder = _install(monkeypatch)
        browser._state("aaa").last_index["1:e4"] = ("button", "Paga ora")
        browser._state("bbb").last_index["1:e4"] = ("button", "Cerca")
        refused = await _tool(BrowserDoTool, owner="aaa").execute(
            steps=[{"action": "click", "ref": "1:e4"}]
        )
        assert refused.startswith("Error:") and "confirm" in refused
        assert "bridge" not in holder
        await _tool(BrowserDoTool, owner="bbb").execute(steps=[{"action": "click", "ref": "1:e4"}])
        assert [c[0] for c in holder["bridge"].calls][0] == "act"

    async def test_the_cursor_is_per_session(self, monkeypatch):
        """Il campo col cursore di un aiutante non decide l'Enter di un altro."""
        holder = _install(monkeypatch)
        browser._render_snapshot(browser._state("aaa"), {
            "url": "u", "version": 1, "index": {"1:e0": ["textbox", "Importo", "Paga ora"]},
            "text": "",
        })
        browser._state("aaa").focus_ref = "1:e0"
        out = await _tool(BrowserDoTool, owner="bbb").execute(steps=[{"action": "press"}])
        assert not out.startswith("Error:"), out
        sent = json.loads([c for c in holder["bridge"].calls if c[0] == "act"][0][1])
        assert sent[0]["submit"] is False      # cursore di bbb sconosciuto
        out = await _tool(BrowserDoTool, owner="aaa").execute(steps=[{"action": "press"}])
        assert out.startswith("Error:") and "Paga ora" in out

    async def test_reading_a_password_is_refused_on_the_page_that_has_it(self, monkeypatch):
        _install(monkeypatch)
        browser._state("aaa").last_index["1:e7"] = ("password", "")
        out = await _tool(BrowserReadTool, owner="aaa").execute(ref="1:e7")
        assert out.startswith("Error:")


class TestSlots:
    async def test_open_waits_for_a_free_slot_then_says_so(self, monkeypatch):
        _allow_url(monkeypatch)
        monkeypatch.setattr(browser, "_SLOT_WAIT_S", 0.2)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool, owner="aaa", max_sessions=1).execute(url="https://uno.test/")
        started = time.monotonic()
        out = await _tool(BrowserOpenTool, owner="bbb", max_sessions=1).execute(
            url="https://due.test/"
        )
        assert time.monotonic() - started >= 0.18
        assert out.startswith("Error:")
        assert "all 1 browser sessions are in use" in out
        assert len(holder["all"]) == 1

    async def test_open_gets_the_slot_freed_while_it_waits(self, monkeypatch):
        _allow_url(monkeypatch)
        monkeypatch.setattr(browser, "_SLOT_WAIT_S", 5)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool, owner="aaa", max_sessions=1).execute(url="https://uno.test/")
        waiting = asyncio.create_task(
            _tool(BrowserOpenTool, owner="bbb", max_sessions=1).execute(url="https://due.test/")
        )
        await asyncio.sleep(0.1)
        assert not waiting.done()
        await _tool(BrowserCloseTool, owner="aaa").execute()
        out = await asyncio.wait_for(waiting, 2)
        assert "ref=1:e0" in out
        assert holder["all"][1].slot == 0

    async def test_other_calls_do_not_wait(self, monkeypatch):
        """Solo ``browser_open`` aspetta: uno snapshot senza pagina e senza slot
        risponde subito."""
        monkeypatch.setattr(browser, "_SLOT_WAIT_S", 5)
        _install(monkeypatch)
        await _snap("aaa", max_sessions=1)
        started = time.monotonic()
        out = await _tool(BrowserSnapshotTool, owner="bbb", max_sessions=1).execute()
        assert time.monotonic() - started < 0.5
        assert out.startswith("Error:") and "in use" in out

    async def test_the_slot_is_free_only_when_the_close_has_finished(self, monkeypatch):
        """Riaprire il profilo mentre la sessione vecchia lo svuota porterebbe
        via i cookie alla nuova: lo slot si libera a chiusura finita."""
        _allow_url(monkeypatch)
        monkeypatch.setattr(browser, "_SLOT_WAIT_S", 5)

        class SlowClose(FakeBridge):
            def open(self, url, timeout):
                self.opened_at = time.monotonic()
                return super().open(url, timeout)

            def close(self):
                time.sleep(0.3)
                self.finished_closing = time.monotonic()
                return super().close()

        holder = _install(monkeypatch, SlowClose)
        await _tool(BrowserOpenTool, owner="aaa", max_sessions=1).execute(url="https://uno.test/")
        closing = asyncio.create_task(_tool(BrowserCloseTool, owner="aaa").execute())
        await asyncio.sleep(0.05)
        await _tool(BrowserOpenTool, owner="bbb", max_sessions=1).execute(url="https://due.test/")
        await closing
        first, second = holder["all"]
        assert second.slot == 0
        assert second.opened_at >= first.finished_closing

    async def test_a_failed_construction_gives_the_slot_back(self, monkeypatch):
        def factory():
            def make(context, slot=0):
                raise RuntimeError("WebView in aggiornamento")
            return make

        monkeypatch.setattr(browser, "_resolve_bridge_class", factory)
        with pytest.raises(RuntimeError):
            await _snap("aaa")
        assert browser._BUSY_SLOTS == set()


class TestOlderApk:
    async def test_a_bridge_without_the_slot_constructor_still_opens(self, monkeypatch):
        """Un APK col solo costruttore a un argomento: Chaquopy solleva
        ``TypeError`` sui due argomenti, e si ripiega su quello vecchio."""
        _allow_url(monkeypatch)
        built = []

        def factory():
            def make(context):
                b = FakeBridge(context)
                built.append(b)
                return b
            return make

        monkeypatch.setattr(browser, "_resolve_bridge_class", factory)
        out = await _tool(BrowserOpenTool).execute(url="https://uno.test/")
        assert "ref=1:e0" in out
        assert len(built) == 1


class TestOwnerFromTheContext:
    def _ctx(self, lineage):
        cfg = SimpleNamespace(android_web=SimpleNamespace(browser=AndroidWebBrowserConfig()))
        return SimpleNamespace(android_context=object(), config=cfg, subagent_lineage_id=lineage)

    def test_a_helper_owns_its_session_by_lineage(self):
        tools = [cls.create(self._ctx("ab12cd34")) for cls in browser.TOOLS]
        assert {t.owner for t in tools} == {"ab12cd34"}

    def test_jenny_is_main(self):
        assert BrowserOpenTool.create(self._ctx(None)).owner == browser.MAIN_OWNER

    def test_max_sessions_comes_from_the_config(self):
        ctx = self._ctx(None)
        ctx.config.android_web.browser = AndroidWebBrowserConfig(max_sessions=5)
        assert BrowserOpenTool.create(ctx).max_sessions == 5

    def test_the_config_default_and_alias(self):
        from jenny.config.schema import ToolsConfig

        assert AndroidWebBrowserConfig().max_sessions == 3
        cfg = ToolsConfig.model_validate({"androidWeb": {"browser": {"maxSessions": 4}}})
        assert cfg.android_web.browser.max_sessions == 4


class TestClosingAtTheEndOfAHelper:
    async def test_closes_the_helper_and_its_fetches_only(self, monkeypatch):
        holder = _install(monkeypatch)
        await _snap("ab12cd34", max_sessions=4)
        await _snap("ab12cd34#fetch-7", max_sessions=4)
        await _snap("zz99", max_sessions=4)
        await _snap(browser.MAIN_OWNER, max_sessions=4)
        await browser.close_owner_sessions("ab12cd34")
        assert [b.closed for b in holder["all"]] == [1, 1, 0, 0]
        assert set(browser._SESSIONS) == {"zz99", browser.MAIN_OWNER}
        assert browser._BUSY_SLOTS == {holder["all"][2].slot, holder["all"][3].slot}

    async def test_a_resumed_helper_opens_a_fresh_page(self, monkeypatch):
        holder = _install(monkeypatch)
        await _snap("ab12cd34")
        await browser.close_owner_sessions("ab12cd34")
        await _snap("ab12cd34")
        assert len(holder["all"]) == 2 and holder["all"][1].closed == 0

    async def test_a_call_waiting_on_the_retired_session_uses_a_new_one(self, monkeypatch):
        holder = _install(monkeypatch)
        await _snap("ab12cd34")
        old = browser._state("ab12cd34")
        async with old.lock:
            waiting = asyncio.create_task(_snap("ab12cd34"))
            await asyncio.sleep(0.02)
            browser._retire(old)
            await browser._destroy_browser_async(old)
        await waiting
        assert browser._state("ab12cd34") is not old
        assert _instance("ab12cd34") is holder["all"][1]

    async def test_without_a_session_nothing_happens(self):
        await browser.close_owner_sessions("ab12cd34")
        assert browser._SESSIONS == {}

    async def test_the_subagent_run_closes_its_lineage(self, monkeypatch):
        from jenny.agent.subagent import SubagentManager

        closed = []

        async def fake_close(owner):
            closed.append(owner)

        monkeypatch.setattr(browser, "close_owner_sessions", fake_close)
        browser._state("ab12cd34")
        # Non si aspetta: la fine corsa dell'aiutante non deve restare appesa
        # alla chiusura in Kotlin (fino a 10 s), o il piano lo conta ancora vivo.
        SubagentManager._close_browser_session("ab12cd34")
        SubagentManager._close_browser_session("")
        assert closed == []
        await asyncio.gather(*browser._BACKGROUND_CLOSES)
        assert closed == ["ab12cd34"]

    async def test_no_session_no_close(self, monkeypatch):
        from jenny.agent.subagent import SubagentManager

        closed = []

        async def fake_close(owner):
            closed.append(owner)

        monkeypatch.setattr(browser, "close_owner_sessions", fake_close)
        SubagentManager._close_browser_session("mai-aperta")
        assert browser._BACKGROUND_CLOSES == set()
        assert closed == []

    async def test_a_failing_close_does_not_escape_the_run(self, monkeypatch):
        from jenny.agent.subagent import SubagentManager

        def explode(owner):
            raise RuntimeError("main thread fermo")

        monkeypatch.setattr(browser, "close_owner_sessions_later", explode)
        SubagentManager._close_browser_session("ab12cd34")


class _LongRead(FakeBridge):
    """Una regione di 10.000 caratteri, tagliata dalla pagina come fa ``read`` nel JS."""

    TEXT = "".join(chr(ord("a") + i % 26) for i in range(10_000))

    def read(self, ref, max_chars, timeout):
        self.calls.append(("read", ref, max_chars, timeout))
        return self._js({
            "url": "https://esempio.test/", "chars": len(self.TEXT),
            "truncated": len(self.TEXT) > max_chars, "text": self.TEXT[:max_chars],
        })


class TestReadInParts:
    async def test_the_first_part_says_how_to_go_on(self, monkeypatch):
        holder = _install(monkeypatch, _LongRead)
        out = await _tool(BrowserReadTool).execute()
        assert holder["bridge"].calls[-1][2] == 4000
        assert _LongRead.TEXT[:4000] in out
        assert "chars 0–4000 of 10000; continue with offset=4000." in out

    async def test_the_next_part_starts_at_the_offset(self, monkeypatch):
        holder = _install(monkeypatch, _LongRead)
        out = await _tool(BrowserReadTool).execute(offset=4000)
        assert holder["bridge"].calls[-1][2] == 8000     # offset + max_read_chars
        body = out.split("\n\n", 1)[1]
        assert body.startswith(_LongRead.TEXT[4000:8000])
        assert "chars 4000–8000 of 10000; continue with offset=8000." in out

    async def test_the_last_part_says_it_is_the_end(self, monkeypatch):
        _install(monkeypatch, _LongRead)
        out = await _tool(BrowserReadTool).execute(offset=8000)
        assert _LongRead.TEXT[8000:] in out
        assert "continue with" not in out
        assert "chars 8000–10000 of 10000: the end of the region." in out

    async def test_past_the_end_is_an_error(self, monkeypatch):
        _install(monkeypatch, _LongRead)
        out = await _tool(BrowserReadTool).execute(offset=12000)
        assert out.startswith("Error:") and "10000 characters" in out

    async def test_a_short_region_has_no_footer(self, monkeypatch):
        _install(monkeypatch)
        out = await _tool(BrowserReadTool).execute()
        assert out.endswith("ciao")


class TestReadPageOnce:
    """Il ripiego di ``web_fetch``: una sessione sua, breve, mai in attesa."""

    async def test_reads_and_closes(self, monkeypatch):
        holder = _install(monkeypatch)
        page = await browser.read_page_once(
            object(), "https://esempio.test/", owner="main",
            cfg=AndroidWebBrowserConfig(), max_chars=5000, timeout=7,
        )
        assert page == {"url": "https://esempio.test/", "text": "ciao", "chars": 3,
                        "truncated": False}
        b = holder["bridge"]
        assert [c[0] for c in b.calls] == ["open", "read"]
        assert b.calls[1][2] == 5000
        await asyncio.gather(*browser._BACKGROUND_CLOSES)  # la chiusura non si aspetta
        assert b.closed == 1
        assert browser._SESSIONS == {} and browser._BUSY_SLOTS == set()

    async def test_no_free_slot_skips_at_once(self, monkeypatch):
        monkeypatch.setattr(browser, "_SLOT_WAIT_S", 5)
        holder = _install(monkeypatch)
        await _snap("aaa", max_sessions=1)
        started = time.monotonic()
        page = await browser.read_page_once(
            object(), "https://esempio.test/", owner="main",
            cfg=AndroidWebBrowserConfig(max_sessions=1), max_chars=5000, timeout=7,
        )
        assert time.monotonic() - started < 0.5
        assert "skipped" in page
        assert len(holder["all"]) == 1

    async def test_without_android_it_skips(self):
        page = await browser.read_page_once(
            None, "https://esempio.test/", owner="main",
            cfg=AndroidWebBrowserConfig(), max_chars=5000, timeout=7,
        )
        assert "skipped" in page

    async def test_without_the_kotlin_class_it_skips(self):
        # Fuori da Chaquopy ``from java import jclass`` non c'e'.
        page = await browser.read_page_once(
            object(), "https://esempio.test/", owner="main",
            cfg=AndroidWebBrowserConfig(), max_chars=5000, timeout=7,
        )
        assert "skipped" in page

    async def test_an_open_error_is_reported_and_the_session_closed(self, monkeypatch):
        class Broken(FakeBridge):
            def open(self, url, timeout):
                return json.dumps({"error": "WebView error: net::ERR_NAME_NOT_RESOLVED"})

        holder = _install(monkeypatch, Broken)
        page = await browser.read_page_once(
            object(), "https://esempio.test/", owner="main",
            cfg=AndroidWebBrowserConfig(), max_chars=5000, timeout=7,
        )
        assert "ERR_NAME_NOT_RESOLVED" in page["error"]
        assert [c[0] for c in holder["bridge"].calls] == []
        await asyncio.gather(*browser._BACKGROUND_CLOSES)  # la chiusura non si aspetta
        assert holder["bridge"].closed == 1
        assert browser._BUSY_SLOTS == set()

    async def test_fetches_leave_the_last_slot_to_browser_open(self, monkeypatch):
        """I ripieghi prendevano anche l'ultimo slot, e un aiutante che apriva una
        pagina aspettava 60 s e rinunciava (4 volte in 18 prove su s22)."""
        _allow_url(monkeypatch)
        monkeypatch.setattr(browser, "_SLOT_WAIT_S", 5)
        release = threading.Event()

        class Slow(FakeBridge):
            def read(self, ref, max_chars, timeout):
                release.wait(2)
                return super().read(ref, max_chars, timeout)

        holder = _install(monkeypatch, Slow)
        cfg = AndroidWebBrowserConfig(max_sessions=3)
        fetches = [
            asyncio.create_task(browser.read_page_once(
                object(), f"https://esempio.test/{i}", owner="main", cfg=cfg,
                max_chars=100, timeout=7,
            ))
            for i in range(3)
        ]
        await asyncio.sleep(0.1)
        assert len(holder["all"]) == 2
        started = time.monotonic()
        out = await _tool(BrowserOpenTool, owner="aaa", max_sessions=3).execute(
            url="https://uno.test/"
        )
        assert time.monotonic() - started < 0.5
        assert "ref=1:e0" in out
        release.set()
        pages = await asyncio.gather(*fetches)
        assert sum("skipped" in page for page in pages) == 1

    async def test_two_fetches_of_one_agent_do_not_share_a_page(self, monkeypatch):
        holder = _install(monkeypatch)
        cfg = AndroidWebBrowserConfig()
        await asyncio.gather(*(
            browser.read_page_once(object(), f"https://esempio.test/{i}", owner="main",
                                   cfg=cfg, max_chars=100, timeout=7)
            for i in range(2)
        ))
        assert len(holder["all"]) == 2
        await asyncio.gather(*browser._BACKGROUND_CLOSES)  # la chiusura non si aspetta
        assert all(b.closed == 1 for b in holder["all"])
