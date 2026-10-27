"""Tests for Android WebView-backed web_search/web_fetch tools."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jenny.agent.tools import android_web
from jenny.agent.tools.android_web import (
    AndroidWebFetchTool,
    AndroidWebSearchTool,
    _looks_like_captcha,
    _parse_search_response,
)
from jenny.config.schema import ToolsConfig
from jenny.config.tool_schemas import AndroidWebFetchConfig, AndroidWebSearchConfig

RESULTS = [
    {"title": "Python", "url": "https://python.org", "snippet": "Official site"},
    {"title": "Docs", "url": "https://docs.python.org", "snippet": "Documentation"},
]


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    """Ensure the module-level bridge cache and lock never leak across tests.

    Recreating the lock (not just the bridge instance) matters because each
    test runs on its own event loop, and asyncio.Lock binds to the loop it's
    first awaited on.
    """
    android_web.reset_android_web_state()
    yield
    android_web.reset_android_web_state()


# La GET diretta vera: i test che la provano la rimettono al suo posto.
_REAL_DIRECT_FETCH = android_web._direct_fetch


@pytest.fixture(autouse=True)
def _no_direct_get(monkeypatch):
    """I test della WebView non escono in rete: qui la GET diretta non porta niente."""
    async def _none(url):
        return None

    monkeypatch.setattr(android_web, "_direct_fetch", _none)


class TestLooksLikeCaptcha:
    @pytest.mark.parametrize(
        "text",
        [
            "please solve this CAPTCHA to continue",
            "verify you are human before proceeding",
            "are you a robot?",
            # Real DuckDuckGo challenge wording (regression: previously missed).
            "Unfortunately, bots use DuckDuckGo too. Please complete the challenge.",
            "Select all squares containing a duck.",
            # Bing/Microsoft block-page wording.
            "Pardon our interruption. We detected unusual traffic.",
            "Your request has been blocked due to automated queries.",
            # Structural markers.
            '<form id="challenge-form" method="post">',
            '<form id="captcha-form">',
            "https://www.google.com/sorry/index",
        ],
    )
    def test_detects_block_pages(self, text):
        assert _looks_like_captcha(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Results for: python asyncio\n1. asyncio docs\n   https://docs.python.org",
            "<html><body><ol><li>Ordinary search result</li></ol></body></html>",
        ],
    )
    def test_no_false_positive_on_ordinary_pages(self, text):
        assert _looks_like_captcha(text) is False


class TestParseSearchResponse:
    def test_results_object(self):
        raw = json.dumps({"results": RESULTS, "pageText": ""})
        assert _parse_search_response(raw) == RESULTS

    def test_double_encoded_results_object(self):
        # evaluateJavascript JSON-encodes the JS string return value, so the
        # real payload arrives as a JSON string containing JSON (Bug C).
        raw = json.dumps(json.dumps({"results": RESULTS, "pageText": ""}))
        assert _parse_search_response(raw) == RESULTS

    def test_empty_results_with_captcha_page_text_raises(self):
        raw = json.dumps(
            {
                "results": [],
                "pageText": "Unfortunately, bots use DuckDuckGo too. Select all squares.",
            }
        )
        with pytest.raises(ValueError, match="CAPTCHA"):
            _parse_search_response(raw)

    def test_empty_results_with_clean_page_text_returns_empty(self):
        # Genuinely empty SERP must NOT raise — the caller reports
        # "No results for: ..." to the model.
        raw = json.dumps({"results": [], "pageText": "No results matched your search."})
        assert _parse_search_response(raw) == []

    def test_double_encoded_empty_with_captcha_raises(self):
        raw = json.dumps(
            json.dumps({"results": [], "pageText": "verify you are human"})
        )
        with pytest.raises(ValueError, match="CAPTCHA"):
            _parse_search_response(raw)

    def test_error_object_raises_with_message(self):
        raw = json.dumps({"error": "WebView timeout after 30s for https://bing.com"})
        with pytest.raises(ValueError, match="WebView timeout"):
            _parse_search_response(raw)

    def test_double_encoded_error_object_raises(self):
        raw = json.dumps(json.dumps({"error": "WebView HTTP error: 403"}))
        with pytest.raises(ValueError, match="403"):
            _parse_search_response(raw)

    def test_non_json_raises_invalid(self):
        with pytest.raises(ValueError, match="Invalid search response"):
            _parse_search_response("<<<garbage>>>")

    def test_non_json_captcha_page_raises_captcha(self):
        with pytest.raises(ValueError, match="CAPTCHA"):
            _parse_search_response("<html>verify you are human</html>")

    def test_legacy_bare_list_raises(self):
        # Kotlin and Python ship in the same APK, so a bare list is a
        # contract violation, not a compat case.
        with pytest.raises(ValueError, match="Unexpected search response type"):
            _parse_search_response(json.dumps(RESULTS))

    def test_captcha_marker_inside_snippet_is_not_a_false_positive(self):
        # A legitimate search about captchas must not be classified as blocked.
        results = [
            {
                "title": "What is reCAPTCHA?",
                "url": "https://example.com",
                "snippet": "reCAPTCHA verifies you are human.",
            }
        ]
        raw = json.dumps({"results": results, "pageText": ""})
        assert _parse_search_response(raw) == results


class TestResetAndroidWebState:
    def test_recreates_the_lock(self):
        """A stale asyncio.Lock bound to a previous (now-closed) event loop
        raises 'bound to a different event loop' the moment it's acquired
        again after a gateway restart within the same process — the lock
        must be replaced, not just the cached bridge instance."""
        old_lock = android_web._BRIDGE_LOCK
        android_web.reset_android_web_state()
        assert android_web._BRIDGE_LOCK is not old_lock

    def test_destroys_cached_bridge_instance(self):
        bridge = Mock()
        android_web._BRIDGE_INSTANCE = bridge
        android_web.reset_android_web_state()
        bridge.destroy.assert_called_once()
        assert android_web._BRIDGE_INSTANCE is None


class TestGetBridge:
    async def test_construction_failure_raises_clear_runtime_error(self):
        failing_cls = Mock(side_effect=Exception("ClassNotFoundException: stripped"))
        with patch.object(android_web, "_resolve_bridge_class", return_value=failing_cls):
            with pytest.raises(RuntimeError, match="Failed to construct AgenticSearchBridge"):
                await android_web._get_bridge(context=object())
        assert android_web._BRIDGE_INSTANCE is None

    async def test_instance_is_cached(self):
        instance = Mock(name="bridge")
        bridge_cls = Mock(return_value=instance)
        with patch.object(android_web, "_resolve_bridge_class", return_value=bridge_cls):
            first = await android_web._get_bridge(context=object())
            second = await android_web._get_bridge(context=object())
        assert first is instance and second is instance
        bridge_cls.assert_called_once()


class TestBridgeSearch:
    async def test_success(self):
        bridge = Mock()
        bridge.searchBing.return_value = json.dumps({"results": RESULTS, "pageText": ""})
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            results = await android_web._bridge_search(object(), "python", 5)
        assert results == RESULTS

    async def test_unsupported_engine_raises(self):
        bridge = Mock()
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            with pytest.raises(ValueError, match="Unsupported Android search engine"):
                await android_web._bridge_search(object(), "python", 5, search_engine="google")

    async def test_timeout_propagates(self):
        """Il timeout non viene inghiottito, e il backstop è quello dichiarato.

        Prima qui c'era solo la prima metà, e nemmeno quella per intero: lo stub
        di ``wait_for`` sollevava sempre ``TimeoutError``, col meccanismo vero
        patchato via, quindi il test asseriva l'eccezione del proprio stub. (Lo
        ``slow_search`` con ``time.sleep(1)`` era codice morto: ``coro.close()``
        fa sì che non giri mai.) Un timeout vero costerebbe almeno dieci secondi
        di orologio, perché il backstop è ``timeout + 10``; si verifica invece
        **quel numero**, che la docstring di ``_bridge_search`` promette e che
        nessun test copriva.
        """
        bridge = Mock()
        bridge.searchBing = Mock(return_value="[]")
        seen: dict[str, object] = {}

        async def fake_wait_for(coro, *args, timeout=None, **kwargs):
            seen["timeout"] = timeout
            coro.close()  # evita il warning sulla to_thread() non attesa
            raise asyncio.TimeoutError

        with patch.object(android_web, "_get_bridge", return_value=bridge):
            with patch.object(android_web.asyncio, "wait_for", fake_wait_for):
                with pytest.raises(asyncio.TimeoutError):
                    await android_web._bridge_search(object(), "python", 5, timeout=7)

        assert seen["timeout"] == 17, "il backstop asyncio deve essere timeout + 10"

    async def test_bridge_exception_propagates(self):
        bridge = Mock()
        bridge.searchBing.side_effect = Exception("JS bridge blew up")
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            with pytest.raises(Exception, match="JS bridge blew up"):
                await android_web._bridge_search(object(), "python", 5)


class TestBridgeFetchDecoding:
    def _bridge(self, raw):
        bridge = Mock()
        bridge.fetchUrl.return_value = raw
        return bridge

    async def test_dict_response_returns_html_and_final_url(self):
        bridge = self._bridge(json.dumps({"html": "<p>hi</p>", "finalUrl": "https://final"}))
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            html, final = await android_web._bridge_fetch(object(), "https://example.com")
        assert html == "<p>hi</p>"
        assert final == "https://final"

    async def test_error_object_raises(self):
        bridge = self._bridge(json.dumps({"error": "boom"}))
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            with pytest.raises(ValueError, match="boom"):
                await android_web._bridge_fetch(object(), "https://example.com")

    async def test_json_null_raises_clean_error_not_attributeerror(self):
        # Regressione: un URL che punta a un binario (es. .../foo.png) non ha
        # un documento HTML, il bridge restituisce JSON `null`. Prima causava
        # `AttributeError: 'NoneType' object has no attribute 'get'`.
        bridge = self._bridge("null")
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            with pytest.raises(ValueError, match="no HTML document"):
                await android_web._bridge_fetch(object(), "https://example.com/x.png")

    async def test_json_string_response_returned_as_content(self):
        bridge = self._bridge(json.dumps("<p>bare</p>"))
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            html, final = await android_web._bridge_fetch(object(), "https://example.com")
        assert html == "<p>bare</p>"
        assert final == "https://example.com"

    async def test_non_json_returned_as_raw_html(self):
        bridge = self._bridge("<html>plain</html>")
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            html, final = await android_web._bridge_fetch(object(), "https://example.com")
        assert html == "<html>plain</html>"
        assert final == "https://example.com"

    @pytest.mark.parametrize("raw", ["", "   \n"])
    async def test_blank_result_raises_instead_of_empty_success(self, raw):
        # `fetchUrl` non replica il controllo che `searchBing` fa sul proprio
        # risultato: `evaluateJavascript` senza valore arriva qui come stringa
        # vuota. Senza guardia diventava un fetch "riuscito" con testo vuoto e
        # status 200 — un fallimento silenzioso.
        bridge = self._bridge(raw)
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            with pytest.raises(ValueError, match="empty result"):
                await android_web._bridge_fetch(object(), "https://example.com")


class TestBridgeCallsAreSerialized:
    """The hidden WebView shares its Chromium renderer with the app's visible
    WebView, so two overlapping bridge calls can starve the visible WebView's
    input dispatch. _BRIDGE_LOCK must cover the whole operation (bridge
    acquisition + the actual blocking call), not just construction."""

    async def test_concurrent_bridge_search_calls_never_overlap(self):
        import time

        active = 0
        max_active = 0

        def slow_search(query, max_results, timeout):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.05)
            active -= 1
            return json.dumps({"results": RESULTS, "pageText": ""})

        bridge = Mock()
        bridge.searchBing = slow_search
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            await asyncio.gather(
                android_web._bridge_search(object(), "a", 5),
                android_web._bridge_search(object(), "b", 5),
            )
        assert max_active == 1

    async def test_concurrent_search_and_fetch_never_overlap(self):
        import time

        active = 0
        max_active = 0

        def slow_search(query, max_results, timeout):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.05)
            active -= 1
            return json.dumps({"results": RESULTS, "pageText": ""})

        def slow_fetch(url, timeout):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.05)
            active -= 1
            return json.dumps({"html": "<p>hi</p>", "finalUrl": url})

        bridge = Mock()
        bridge.searchBing = slow_search
        bridge.fetchUrl = slow_fetch
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            await asyncio.gather(
                android_web._bridge_search(object(), "a", 5),
                android_web._bridge_fetch(object(), "https://example.com"),
            )
        assert max_active == 1


class TestAndroidWebSearchToolExecute:
    def _tool(self) -> AndroidWebSearchTool:
        return AndroidWebSearchTool(android_context=object(), timeout=5)

    async def test_bridge_success_formats_results(self):
        bridge = Mock()
        bridge.searchBing.return_value = json.dumps({"results": RESULTS, "pageText": ""})
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            out = await self._tool().execute("python")
        assert "Results for: python" in out
        assert "https://python.org" in out

    async def test_bridge_empty_results_reports_no_results_without_fallback(self):
        bridge = Mock()
        bridge.searchBing.return_value = json.dumps(
            {"results": [], "pageText": "nothing matched"}
        )
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            out = await self._tool().execute("qzxv nonsense")
        assert "No results for: qzxv nonsense" in out

    async def test_bridge_captcha_reports_error_without_fallback(self):
        bridge = Mock()
        bridge.searchBing.return_value = json.dumps(
            {"results": [], "pageText": "verify you are human"}
        )
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            with patch.object(android_web, "destroy_bridge") as destroy:
                out = await self._tool().execute("python")
        destroy.assert_called_once()
        assert "web_search unavailable" in out
        assert "CAPTCHA" in out

    async def test_bridge_failure_reports_error(self):
        with patch.object(android_web, "_get_bridge", side_effect=RuntimeError("no bridge")):
            with patch.object(android_web, "destroy_bridge") as destroy:
                out = await self._tool().execute("python")
        destroy.assert_called_once()
        assert "web_search unavailable" in out

    async def test_bridge_timeout_destroys_bridge_and_reports_error(self):
        with patch.object(android_web, "_bridge_search", side_effect=asyncio.TimeoutError("timed out")):
            with patch.object(android_web, "destroy_bridge") as destroy:
                out = await self._tool().execute("python")
        destroy.assert_called_once()
        assert "web_search unavailable" in out

    async def test_cancelled_destroys_bridge_and_reraises(self):
        with patch.object(android_web, "_bridge_search", side_effect=asyncio.CancelledError):
            with patch.object(android_web, "destroy_bridge") as destroy:
                with pytest.raises(asyncio.CancelledError):
                    await self._tool().execute("python")
        destroy.assert_called_once()


class TestAndroidWebFetchToolExecute:
    def _tool(self) -> AndroidWebFetchTool:
        return AndroidWebFetchTool(android_context=object(), timeout=5)

    async def test_bridge_success(self):
        payload = json.dumps(
            {"html": "<html><body><h1>Hello</h1></body></html>", "finalUrl": "https://example.com/"}
        )
        with patch.object(
            android_web, "_bridge_fetch", return_value=(
                "<html><body><h1>Hello</h1></body></html>", "https://example.com/"
            )
        ):
            out = json.loads(await self._tool().execute("https://example.com"))
        assert out["finalUrl"] == "https://example.com/"
        assert "Hello" in out["text"]
        assert payload  # payload shape documented above

    async def test_bridge_failure_reports_error_without_fallback(self):
        with patch.object(android_web, "_bridge_fetch", side_effect=RuntimeError("no bridge")):
            with patch.object(android_web, "destroy_bridge") as destroy:
                out = json.loads(await self._tool().execute("https://example.com"))
        destroy.assert_called_once()
        assert "WebView fetch failed" in out["error"]
        assert "no bridge" in out["error"]

    async def test_no_document_error_is_actionable_and_spares_the_bridge(self):
        """Un URL che non è una pagina HTML non deve costare il WebView condiviso.

        Il bridge risponde, ma senza documento: plain text servito con CSP
        `sandbox` (raw.githubusercontent.com), download, binari. Il WebView è
        sano — è l'URL a non essere una pagina — e distruggerlo butterebbe
        cookie, localStorage e warm-up del renderer per il chiamante successivo.
        L'errore deve inoltre dire cosa fare, non solo che è andata male.
        """
        with patch.object(
            android_web,
            "_bridge_fetch",
            side_effect=ValueError("WebView returned no HTML document"),
        ):
            with patch.object(android_web, "destroy_bridge") as destroy:
                out = json.loads(await self._tool().execute("https://example.com/a.md"))
        destroy.assert_not_called()
        assert "no HTML document" in out["error"]
        assert "http_get" in out["hint"]

    async def test_timeout_error_also_suggests_http_get(self):
        # Una risposta che il browser scarica invece di aprire non fa mai
        # scattare `onPageFinished` (nessun DownloadListener lato Kotlin): da
        # qui si vede solo un timeout, quindi anche quello porta il consiglio.
        with patch.object(android_web, "_bridge_fetch", side_effect=asyncio.TimeoutError):
            with patch.object(android_web, "destroy_bridge") as destroy:
                out = json.loads(await self._tool().execute("https://example.com/x.zip"))
        destroy.assert_called_once()
        assert "timed out" in out["error"]
        assert "http_get" in out["hint"]

    async def test_final_url_redirected_to_loopback_is_blocked(self):
        """The WebView follows redirects/JS navigation with no per-hop SSRF
        check of its own (unlike httpx, this can't be fixed from the Python
        side before the fact). The best we can do after the fact is
        re-validate the finalUrl the bridge reports and withhold the fetched
        content if it landed somewhere disallowed."""
        with patch.object(
            android_web,
            "_bridge_fetch",
            return_value=("<html><body>admin panel</body></html>", "http://127.0.0.1:9111/admin"),
        ):
            out = json.loads(await self._tool().execute("https://example.com"))
        assert "error" in out
        assert "blocked" in out["error"].lower()
        assert out["finalUrl"] == "http://127.0.0.1:9111/admin"
        # The fetched content must never reach the caller once the final
        # destination fails the SSRF check.
        assert "text" not in out

    async def test_final_url_redirected_to_metadata_endpoint_is_blocked(self):
        with patch.object(
            android_web,
            "_bridge_fetch",
            return_value=("<html>creds</html>", "http://169.254.169.254/latest/meta-data/"),
        ):
            out = json.loads(await self._tool().execute("https://example.com"))
        assert "error" in out
        assert "text" not in out

    async def test_final_url_on_same_allowed_host_still_returns_content(self):
        """Regression guard: a normal fetch with no redirect (finalUrl ==
        initial url, both public) must still return content — the new
        post-fetch check must not break the common case."""
        with patch.object(
            android_web,
            "_bridge_fetch",
            return_value=("<html><body><h1>Hello</h1></body></html>", "https://example.com/"),
        ):
            out = json.loads(await self._tool().execute("https://example.com"))
        assert "error" not in out
        assert "Hello" in out["text"]
        assert out["finalUrl"] == "https://example.com/"


class TestToolGating:
    """search/fetch gate solely on the top-level ``android_web.enable`` flag.

    ``AndroidWebSearchConfig.enable`` / ``AndroidWebFetchConfig.enable`` were
    declared in the schema but never read anywhere (the tool ``enabled()``
    classmethods only checked ``ctx.config.android_web.enable``), so they were
    removed rather than wired up — see docs/configuration.md, which never
    documented them as independently configurable.
    """

    def test_per_tool_enable_fields_no_longer_exist(self):
        assert not hasattr(AndroidWebSearchConfig(), "enable")
        assert not hasattr(AndroidWebFetchConfig(), "enable")

    def test_top_level_flag_enables_both_tools(self):
        ctx = SimpleNamespace(android_context=Mock(), config=ToolsConfig())
        assert AndroidWebSearchTool.enabled(ctx) is True
        assert AndroidWebFetchTool.enabled(ctx) is True

    def test_top_level_flag_disables_both_tools(self):
        cfg = ToolsConfig()
        cfg.android_web.enable = False
        ctx = SimpleNamespace(android_context=Mock(), config=cfg)
        assert AndroidWebSearchTool.enabled(ctx) is False
        assert AndroidWebFetchTool.enabled(ctx) is False

    def test_legacy_per_tool_enable_key_in_config_is_silently_ignored(self):
        """A config.json written before the cleanup (or hand-edited to add
        the never-wired flag) must still parse and must not disable the tool:
        the key is unknown now and gets dropped, not enforced."""
        cfg = ToolsConfig.model_validate(
            {"androidWeb": {"search": {"enable": False}, "fetch": {"enable": False}}}
        )
        ctx = SimpleNamespace(android_context=Mock(), config=cfg)
        assert AndroidWebSearchTool.enabled(ctx) is True
        assert AndroidWebFetchTool.enabled(ctx) is True


class TestSearchDefault:
    """Otto risultati di partenza: con cinque le ricerche vere restavano povere."""

    def test_config_default(self):
        assert AndroidWebSearchConfig().max_results == 8
        with pytest.raises(Exception):  # il tetto resta 10
            AndroidWebSearchConfig(max_results=11)

    def test_tool_default_and_description(self):
        assert AndroidWebSearchTool(android_context=object()).max_results == 8
        assert "count defaults to 8 (max 10)" in AndroidWebSearchTool.description

    async def test_the_default_reaches_the_bridge(self):
        bridge = Mock()
        bridge.searchBing.return_value = json.dumps({"results": RESULTS, "pageText": ""})
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            await AndroidWebSearchTool(android_context=object()).execute("python")
        assert bridge.searchBing.call_args[0][1] == 8


_ARTICLE = "<html><body><article>" + ("<p>Paragrafo di contenuto vero.</p>" * 60) + "</article></body></html>"
_BROWSER_TEXT = "Il testo dell'articolo, letto dal browser. " * 40


def _allow_public(monkeypatch):
    """Validazione senza DNS: passa tutto tranne il loopback e la rete locale."""
    import jenny.security.network as net

    def fake(url, **kw):
        blocked = "127.0.0.1" in url or "192.168." in url
        return (not blocked, "private address" if blocked else "")

    monkeypatch.setattr(net, "validate_url_target", fake)


class TestFetchFallsBackToTheBrowser:
    """``web_fetch`` riprova in una sessione del browser quando ``fetchUrl`` non basta."""

    def _tool(self) -> AndroidWebFetchTool:
        return AndroidWebFetchTool(android_context=object(), timeout=5, owner="ab12cd34")

    def _browser(self, monkeypatch, result):
        from jenny.agent.tools import browser

        calls = []

        async def fake(context, url, *, owner, cfg, max_chars, timeout):
            calls.append({"url": url, "owner": owner, "max_chars": max_chars,
                          "timeout": timeout, "max_sessions": cfg.max_sessions})
            return result

        monkeypatch.setattr(browser, "read_page_once", fake)
        return calls

    async def test_a_short_page_is_read_again_in_the_browser(self, monkeypatch):
        _allow_public(monkeypatch)
        calls = self._browser(monkeypatch, {
            "url": "https://example.com/a", "text": _BROWSER_TEXT, "chars": 1720,
            "truncated": False,
        })
        with patch.object(android_web, "_bridge_fetch",
                          return_value=("<html><body>Loading…</body></html>", "https://example.com/a")):
            out = json.loads(await self._tool().execute("https://example.com/a", maxChars=9000))
        assert out["extractor"] == "browser"
        assert out["fallback_reason"] == "short_text"
        assert "letto dal browser" in out["text"]
        assert out["text"].startswith(android_web._UNTRUSTED_BANNER)
        assert set(out) >= {"url", "finalUrl", "status", "truncated", "length", "untrusted"}
        assert calls == [{"url": "https://example.com/a", "owner": "ab12cd34",
                          "max_chars": 9000, "timeout": 5, "max_sessions": 3}]

    async def test_a_cookie_wall_is_read_again_in_the_browser(self, monkeypatch):
        _allow_public(monkeypatch)
        wall = ("<p>We use cookies to improve your experience. Accept all or manage "
                "your consent preferences.</p>" * 12)
        self._browser(monkeypatch, {
            "url": "https://example.com/b", "text": "Articolo. " * 70, "chars": 700,
            "truncated": False,
        })
        with patch.object(android_web, "_bridge_fetch",
                          return_value=(f"<html><body>{wall}</body></html>", "https://example.com/b")):
            out = json.loads(await self._tool().execute("https://example.com/b"))
        assert out["extractor"] == "browser"
        assert out["fallback_reason"] == "cookie_wall"

    async def test_a_full_page_does_not_open_the_browser(self, monkeypatch):
        _allow_public(monkeypatch)
        calls = self._browser(monkeypatch, {"skipped": "should not be asked"})
        with patch.object(android_web, "_bridge_fetch",
                          return_value=(_ARTICLE, "https://example.com/c")):
            out = json.loads(await self._tool().execute("https://example.com/c"))
        assert calls == []
        assert out["extractor"] == "webview"
        assert "fallback_reason" not in out

    async def test_a_browser_that_finds_less_leaves_the_first_result(self, monkeypatch):
        _allow_public(monkeypatch)
        self._browser(monkeypatch, {"url": "https://example.com/d", "text": "x",
                                    "chars": 1, "truncated": False})
        with patch.object(android_web, "_bridge_fetch",
                          return_value=("<html><body>Breve ma vero.</body></html>",
                                        "https://example.com/d")):
            out = json.loads(await self._tool().execute("https://example.com/d"))
        assert out["extractor"] == "webview"
        assert "Breve ma vero." in out["text"]

    async def test_a_bridge_error_is_rescued_by_the_browser(self, monkeypatch):
        _allow_public(monkeypatch)
        self._browser(monkeypatch, {"url": "https://example.com/e", "text": _BROWSER_TEXT,
                                    "chars": 1720, "truncated": False})
        with patch.object(android_web, "_bridge_fetch",
                          side_effect=ValueError("WebView returned no HTML document")):
            out = json.loads(await self._tool().execute("https://example.com/e"))
        assert "error" not in out
        assert out["extractor"] == "browser"
        assert out["fallback_reason"] == "no_document"

    async def test_a_timeout_is_rescued_by_the_browser(self, monkeypatch):
        _allow_public(monkeypatch)
        self._browser(monkeypatch, {"url": "https://example.com/f", "text": _BROWSER_TEXT,
                                    "chars": 1720, "truncated": True})
        with patch.object(android_web, "_bridge_fetch", side_effect=asyncio.TimeoutError):
            with patch.object(android_web, "destroy_bridge"):
                out = json.loads(await self._tool().execute("https://example.com/f"))
        assert out["fallback_reason"] == "timeout"
        assert out["truncated"] is True

    async def test_when_the_browser_fails_too_the_error_says_so(self, monkeypatch):
        _allow_public(monkeypatch)
        self._browser(monkeypatch, {"error": "browser_open did not answer within 15s"})
        with patch.object(android_web, "_bridge_fetch",
                          side_effect=ValueError("WebView returned no HTML document")):
            out = json.loads(await self._tool().execute("https://example.com/g"))
        assert out["error"].startswith("WebView returned no HTML document")
        assert "tried the browser too" in out["error"]
        assert "did not answer" in out["error"]
        assert "http_get" in out["hint"]

    async def test_a_skipped_fallback_leaves_the_error_as_it_was(self, monkeypatch):
        _allow_public(monkeypatch)
        self._browser(monkeypatch, {"skipped": "no free browser session"})
        with patch.object(android_web, "_bridge_fetch",
                          side_effect=ValueError("WebView returned no HTML document")):
            out = json.loads(await self._tool().execute("https://example.com/h"))
        assert out["error"] == "WebView returned no HTML document"

    async def test_the_browser_landing_on_a_private_address_is_withheld(self, monkeypatch):
        _allow_public(monkeypatch)
        self._browser(monkeypatch, {"url": "http://192.168.1.1/admin", "text": "router admin " * 80,
                                    "chars": 1040, "truncated": False})
        with patch.object(android_web, "_bridge_fetch",
                          side_effect=ValueError("WebView returned no HTML document")):
            out = json.loads(await self._tool().execute("https://example.com/i"))
        assert "text" not in out
        assert "router admin" not in json.dumps(out)
        assert "tried the browser too" in out["error"]
        assert "blocked address" in out["error"]

    async def test_a_blocked_redirect_does_not_try_the_browser(self, monkeypatch):
        _allow_public(monkeypatch)
        calls = self._browser(monkeypatch, {"skipped": "should not be asked"})
        with patch.object(android_web, "_bridge_fetch",
                          return_value=("<html>admin</html>", "http://127.0.0.1:9111/admin")):
            out = json.loads(await self._tool().execute("https://example.com/j"))
        assert calls == []
        assert "blocked" in out["error"].lower()

    async def test_the_owner_comes_from_the_context(self):
        ctx = SimpleNamespace(android_context=object(), config=ToolsConfig(),
                              subagent_lineage_id="ab12cd34")
        assert AndroidWebFetchTool.create(ctx).owner == "ab12cd34"
        ctx.subagent_lineage_id = None
        assert AndroidWebFetchTool.create(ctx).owner == "main"

    async def test_the_fetch_has_its_own_shorter_timeout(self):
        """La lettura non aspetta il timeout della ricerca: oltre i 15 s passa
        al ripiego nel browser."""
        cfg = ToolsConfig()
        cfg.android_web.search.timeout = 40
        ctx = SimpleNamespace(android_context=object(), config=cfg, subagent_lineage_id=None)
        assert AndroidWebFetchTool.create(ctx).timeout == 15
        cfg.android_web.fetch.timeout = 25
        assert AndroidWebFetchTool.create(ctx).timeout == 25

    async def test_without_android_the_fallback_is_silent(self, monkeypatch):
        """Fuori da Chaquopy la classe Kotlin non c'e': il ripiego si salta e
        l'errore resta quello di prima, senza «tried the browser too»."""
        _allow_public(monkeypatch)
        with patch.object(android_web, "_bridge_fetch",
                          side_effect=ValueError("WebView returned no HTML document")):
            out = json.loads(await self._tool().execute("https://example.com/k"))
        assert out["error"] == "WebView returned no HTML document"


class TestLooksLikeCookieWall:
    @pytest.mark.parametrize("text", [
        "Questo sito usa i cookie. Accetta tutti / Rifiuta",
        "We value your privacy. Cookie consent: Accept",
    ])
    def test_detects(self, text):
        assert android_web._looks_like_cookie_wall(text)

    @pytest.mark.parametrize("text", [
        "Ricetta dei biscotti: farina, burro, zucchero.",
        "Accept-Language header explained",
        "cookie " + "testo lungo " * 400 + " accept",   # pagina lunga: non e' un muro
    ])
    def test_does_not_fire(self, text):
        assert not android_web._looks_like_cookie_wall(text)


_LONG_ARTICLE = (
    "<html><body><article>" + ("<p>Calathea: luce filtrata, terriccio umido.</p>" * 80)
    + "</article></body></html>"
)


class TestFetchReadsDirectlyFirst:
    """Una pagina HTML con un testo vero arriva da una GET, senza la WebView.

    La WebView è una sola, sotto ``_BRIDGE_LOCK``: le pagine chieste insieme si
    leggevano una alla volta. Tutto ciò che la GET non porta va alla WebView.
    """

    def _serve(self, monkeypatch, handler):
        import httpx

        _allow_public(monkeypatch)
        monkeypatch.setattr(android_web, "_direct_fetch", _REAL_DIRECT_FETCH)
        transport = httpx.MockTransport(handler)
        fake_httpx = SimpleNamespace(
            AsyncClient=lambda **kw: httpx.AsyncClient(transport=transport, **kw)
        )
        monkeypatch.setattr(android_web, "httpx", fake_httpx)

    def _tool(self) -> AndroidWebFetchTool:
        return AndroidWebFetchTool(android_context=object(), timeout=5)

    async def test_a_page_with_real_text_skips_the_webview(self, monkeypatch):
        import httpx

        self._serve(monkeypatch, lambda req: httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=_LONG_ARTICLE))
        bridge = Mock(side_effect=AssertionError("the WebView should not be asked"))
        with patch.object(android_web, "_bridge_fetch", bridge):
            out = json.loads(await self._tool().execute("https://example.com/calathea"))
        assert out["extractor"] == "direct"
        assert "luce filtrata" in out["text"]
        assert out["text"].startswith(android_web._UNTRUSTED_BANNER)

    async def test_it_does_not_wait_for_the_webview(self, monkeypatch):
        """La WebView occupata da un'altra lettura non ferma una pagina che la GET legge."""
        import httpx

        self._serve(monkeypatch, lambda req: httpx.Response(
            200, headers={"content-type": "text/html"}, text=_LONG_ARTICLE))
        async with android_web._BRIDGE_LOCK:
            out = json.loads(await asyncio.wait_for(
                self._tool().execute("https://example.com/calathea"), timeout=2))
        assert out["extractor"] == "direct"

    @pytest.mark.parametrize("response", [
        {"status_code": 200, "headers": {"content-type": "text/html"},
         "text": "<html><body><div id=app>Loading…</div></body></html>"},
        {"status_code": 403, "headers": {"content-type": "text/html"}, "text": "robot policy"},
        {"status_code": 200, "headers": {"content-type": "application/pdf"}, "content": b"%PDF"},
        {"status_code": 200, "headers": {"content-type": "text/html"},
         "text": "<p>We use cookies. Accept all or manage your consent.</p>" * 40},
    ], ids=["javascript-page", "refused", "not-html", "cookie-wall"])
    async def test_what_the_get_cannot_read_goes_to_the_webview(self, monkeypatch, response):
        import httpx

        self._serve(monkeypatch, lambda req: httpx.Response(**response))
        with patch.object(android_web, "_bridge_fetch",
                          return_value=(_LONG_ARTICLE, "https://example.com/p")) as bridge:
            out = json.loads(await self._tool().execute("https://example.com/p"))
        bridge.assert_called_once()
        assert out["extractor"] == "webview"

    async def test_a_redirect_to_the_local_network_is_not_followed(self, monkeypatch):
        import httpx

        asked = []

        def handler(req):
            asked.append(str(req.url))
            return httpx.Response(302, headers={"location": "http://192.168.1.1/admin"})

        self._serve(monkeypatch, handler)
        with patch.object(android_web, "_bridge_fetch",
                          return_value=(_LONG_ARTICLE, "https://example.com/r")):
            out = json.loads(await self._tool().execute("https://example.com/r"))
        assert asked == ["https://example.com/r"]
        assert out["extractor"] == "webview"


_KOTLIN_TIMEOUT = json.dumps({"error": "WebView timeout after 30s for https://www.bing.com/search?q=x"})


class TestASearchEngineThatDoesNotAnswer:
    """Dopo un timeout le ricerche rinunciano subito per un minuto.

    Su s22 il motore non rispondeva per minuti, e ogni ricerca aspettava il
    tetto pieno; quelle in coda dietro al lock lo aspettavano in fila (30, 60, 90 s).
    """

    def _tool(self) -> AndroidWebSearchTool:
        return AndroidWebSearchTool(android_context=object(), timeout=30)

    async def test_the_next_search_gives_up_at_once_and_says_why(self):
        bridge = Mock()
        bridge.searchBing.return_value = _KOTLIN_TIMEOUT
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            first = await self._tool().execute("calathea")
            with patch.object(android_web, "destroy_bridge") as destroy:
                second = await self._tool().execute("calathea care")
        assert "WebView timeout" in first
        assert bridge.searchBing.call_count == 1
        assert "did not answer a moment ago" in second
        assert "web_fetch" in second
        destroy.assert_not_called()

    async def test_searches_queued_behind_the_timeout_do_not_wait_in_turn(self):
        calls = []

        def search(query, max_results, timeout):
            calls.append(query)
            return _KOTLIN_TIMEOUT

        bridge = Mock()
        bridge.searchBing = search
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            out = await asyncio.gather(*(self._tool().execute(q) for q in ("a", "b", "c")))
        assert calls == ["a"]
        assert sum("did not answer a moment ago" in o for o in out) == 2

    async def test_a_minute_later_it_searches_again(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(android_web.time, "monotonic", lambda: now[0])
        bridge = Mock()
        bridge.searchBing.side_effect = [
            _KOTLIN_TIMEOUT, json.dumps({"results": RESULTS, "pageText": ""}),
        ]
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            await self._tool().execute("a")
            now[0] += 61
            out = await self._tool().execute("b")
        assert "python.org" in out

    async def test_another_error_does_not_stop_the_searches(self):
        bridge = Mock()
        bridge.searchBing.side_effect = [
            json.dumps({"error": "WebView HTTP error: 500"}),
            json.dumps({"results": RESULTS, "pageText": ""}),
        ]
        with patch.object(android_web, "_get_bridge", return_value=bridge):
            await self._tool().execute("a")
            out = await self._tool().execute("b")
        assert "python.org" in out
