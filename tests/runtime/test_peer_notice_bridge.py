"""Le notifiche dell'altra Jenny: da Python alla tendina, e il tocco di ritorno.

Andata: ``notifier.post_peer_confirm``/``post_peer_done``/``clear_peer_notice``
chiamano il ``NotifierBridge`` Kotlin (qui finto). Ritorno:
``native_input.on_native_peer_action``, che Kotlin chiama da un thread JNI e
che porta il tocco sul loop del gateway. In mezzo c'è Kotlin, che in CI non
gira: il legame per nome si controlla leggendo il sorgente, come in
``test_native_input.py::TestBoundaryWithKotlin``.
"""

from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path
from typing import Any

import pytest
from support.aio import wait_until
from support.bus import FakeBus
from support.kotlin_source import ANDROID_MAIN, ANDROID_SRC, read_source

from jenny.runtime import native_input as ni
from jenny.runtime import notifier

CID = "0123456789abcdef0123456789abcdef"
QID = "fedcba9876543210fedcba9876543210"


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def postPeerConfirm(self, cid: str, title: str, body: str) -> bool:  # noqa: N802
        self.calls.append(("postPeerConfirm", cid, title, body))
        return True

    def postPeerDone(self, qid: str, title: str, body: str) -> bool:  # noqa: N802
        self.calls.append(("postPeerDone", qid, title, body))
        return True

    def postPeerUndone(self, qid: str, title: str, body: str) -> bool:  # noqa: N802
        self.calls.append(("postPeerUndone", qid, title, body))
        return True

    def clearPeerNotice(self, kind: str, ident: str) -> bool:  # noqa: N802
        self.calls.append(("clearPeerNotice", kind, ident))
        return True


@pytest.fixture
def bridge(monkeypatch) -> _FakeBridge:
    fake = _FakeBridge()
    monkeypatch.setattr(notifier, "get_android_context", lambda: object())

    async def fake_get_bridge(context: Any) -> Any:
        return fake

    monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
    return fake


async def _drain() -> None:
    await asyncio.gather(*list(notifier._TASKS))


class TestNoticesWithoutAndroid:
    """Sul Mac e nei test: nessun contesto Android, nessuna chiamata, nessun errore."""

    async def test_all_three_are_noops(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: None)
        assert notifier.post_peer_confirm(CID, "t", "b") is None
        assert notifier.post_peer_done(QID, "t", "b") is None
        assert notifier.clear_peer_notice("confirm", CID) is None
        assert not notifier._TASKS

    def test_noops_without_a_running_loop(self, bridge):
        # Da un contesto sincrono, senza loop: niente da accodare, e non solleva.
        notifier.post_peer_confirm(CID, "t", "b")
        notifier.post_peer_done(QID, "t", "b")
        notifier.clear_peer_notice("done", QID)
        assert not notifier._TASKS
        assert bridge.calls == []

    async def test_a_broken_bridge_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def broken(context: Any) -> Any:
            raise RuntimeError("no bridge")

        monkeypatch.setattr(notifier, "_get_bridge", broken)
        notifier.post_peer_confirm(CID, "t", "b")
        await _drain()


class TestNoticesThroughTheBridge:
    async def test_confirm(self, bridge):
        notifier.post_peer_confirm(CID, "Jenny di lavoro", "Aprire https://example.org?")
        await _drain()
        assert bridge.calls == [
            ("postPeerConfirm", CID, "Jenny di lavoro", "Aprire https://example.org?"),
        ]

    async def test_done(self, bridge):
        notifier.post_peer_done(QID, "Fatto", "Ho aggiunto la nota.")
        await _drain()
        assert bridge.calls == [("postPeerDone", QID, "Fatto", "Ho aggiunto la nota.")]

    async def test_undone_goes_without_its_button(self, bridge):
        notifier.post_peer_undone(QID, "Fatto", "Annullato.")
        await _drain()
        assert bridge.calls == [("postPeerUndone", QID, "Fatto", "Annullato.")]

    @pytest.mark.parametrize("kind, ident", [("confirm", CID), ("done", QID)])
    async def test_clear(self, bridge, kind, ident):
        notifier.clear_peer_notice(kind, ident)
        await _drain()
        assert bridge.calls == [("clearPeerNotice", kind, ident)]

    async def test_title_is_one_line_and_text_is_capped(self, bridge):
        notifier.post_peer_confirm(CID, "  Jenny\n di   lavoro ", "x" * 5000)
        await _drain()
        _, _, title, body = bridge.calls[0]
        assert title == "Jenny di lavoro"
        assert len(body) <= notifier._PEER_BODY_MAX_CHARS

    @pytest.mark.parametrize("ident", ["", "ABCDEF0123456789ABCDEF0123456789", CID[:-1], CID + "0",
                                       "../" + CID[3:], None])
    async def test_malformed_ident_never_reaches_the_bridge(self, bridge, ident):
        notifier.post_peer_confirm(ident, "t", "b")
        notifier.post_peer_done(ident, "t", "b")
        notifier.clear_peer_notice("confirm", ident)
        await _drain()
        assert bridge.calls == []

    async def test_unknown_kind_is_not_cleared(self, bridge):
        notifier.clear_peer_notice("alert", CID)
        await _drain()
        assert bridge.calls == []


@pytest.fixture
def peer_input():
    ni.reset_native_input()
    yield
    ni.reset_native_input()


@pytest.mark.usefixtures("peer_input")
class TestPeerActionIn:
    async def test_without_bind_or_handler_says_no_and_does_not_raise(self):
        assert ni.on_native_peer_action("allow", CID) is False
        assert ni.bind_native_input(FakeBus()) is True
        # Agganciato ma senza gestore: ancora un no, da riprovare.
        assert ni.on_native_peer_action("allow", CID) is False

    @pytest.mark.parametrize("action", ["", "ALLOW", "approve", "allow ", None])
    async def test_unknown_action_is_rejected(self, action):
        ni.bind_native_input(FakeBus())
        seen: list[tuple[str, str]] = []
        ni.set_peer_action_handler(lambda a, i: seen.append((a, i)))
        assert ni.on_native_peer_action(action, CID) is False
        await asyncio.sleep(0)
        assert seen == []

    @pytest.mark.parametrize("ident", ["", CID.upper(), CID[:-1], CID + "a", f" {CID}", None, 42])
    async def test_malformed_ident_is_rejected(self, ident):
        ni.bind_native_input(FakeBus())
        seen: list[tuple[str, str]] = []
        ni.set_peer_action_handler(lambda a, i: seen.append((a, i)))
        assert ni.on_native_peer_action("deny", ident) is False
        await asyncio.sleep(0)
        assert seen == []

    @pytest.mark.parametrize("action, ident", [("allow", CID), ("deny", CID), ("undo", QID)])
    async def test_from_a_jni_thread_the_handler_runs_on_the_loop(self, action, ident):
        ni.bind_native_input(FakeBus())
        loop_thread = threading.get_ident()
        seen: list[tuple[str, str, int]] = []
        ni.set_peer_action_handler(lambda a, i: seen.append((a, i, threading.get_ident())))

        result: list[bool] = []
        caller = threading.Thread(
            target=lambda: result.append(ni.on_native_peer_action(action, ident))
        )
        caller.start()
        caller.join()

        assert result == [True]
        await wait_until(lambda: seen, msg="peer action never reached the handler")
        assert seen == [(action, ident, loop_thread)]

    async def test_a_failing_handler_stays_on_the_loop(self):
        ni.bind_native_input(FakeBus())
        calls: list[str] = []

        def boom(action: str, ident: str) -> None:
            calls.append(action)
            raise ValueError("boom")

        ni.set_peer_action_handler(boom)
        assert ni.on_native_peer_action("undo", QID) is True
        await wait_until(lambda: calls)
        # Il loop è ancora vivo e accetta il tocco successivo.
        assert ni.on_native_peer_action("undo", QID) is True
        await wait_until(lambda: len(calls) == 2)

    async def test_reset_drops_the_handler(self):
        ni.bind_native_input(FakeBus())
        ni.set_peer_action_handler(lambda a, i: None)
        ni.reset_native_input()
        ni.bind_native_input(FakeBus())
        assert ni.on_native_peer_action("allow", CID) is False

    async def test_a_closed_loop_says_no(self):
        ni.set_peer_action_handler(lambda a, i: None)
        dead = asyncio.new_event_loop()
        dead.close()
        ni._LOOP = dead
        assert ni.on_native_peer_action("allow", CID) is False


class TestBoundaryWithKotlin:
    """Kotlin e Python si parlano per nome: un rename rompe solo sul telefono."""

    def test_python_calls_bridge_methods_that_exist(self):
        src = read_source(ANDROID_SRC / "NotifierBridge.kt")
        assert "fun postPeerConfirm(cid: String, title: String, body: String): Boolean" in src
        assert "fun postPeerDone(qid: String, title: String, body: String): Boolean" in src
        assert "fun postPeerUndone(qid: String, title: String, body: String): Boolean" in src
        assert "fun clearPeerNotice(kind: String, ident: String): Boolean" in src

    def test_notice_kinds_match(self):
        src = read_source(ANDROID_SRC / "NotifierBridge.kt")
        kinds = set(re.findall(r'const val PEER_KIND_\w+ = "(\w+)"', src))
        assert kinds == set(notifier.PEER_NOTICE_KINDS)

    def test_receiver_actions_match(self):
        src = read_source(ANDROID_SRC / "PeerActionReceiver.kt")
        actions = set(re.findall(r'const val ACTION_\w+ = "(\w+)"', src))
        assert actions == set(ni.PEER_ACTIONS)
        assert 'Regex("[0-9a-f]{32}")' in src
        assert ni._PEER_IDENT.pattern == "[0-9a-f]{32}"
        assert notifier._PEER_IDENT.pattern == "[0-9a-f]{32}"

    def test_gateway_service_calls_this_function(self):
        src = read_source(ANDROID_SRC / "GatewayService.kt")
        assert '.callAttr("on_native_peer_action", action, ident)' in src
        assert callable(ni.on_native_peer_action)

    def test_peer_buttons_are_immutable_broadcasts(self):
        src = read_source(ANDROID_SRC / "NotifierBridge.kt")
        start = src.index("fun peerActionIntent(")
        body = src[start:src.index("fun peerAction(", start)]
        assert "PendingIntent.getBroadcast(" in body
        assert "FLAG_IMMUTABLE" in body and "FLAG_MUTABLE" not in body.replace("FLAG_IMMUTABLE", "")
        assert ".setData(" in body

    def test_receiver_is_not_exported(self):
        manifest = (ANDROID_MAIN / "AndroidManifest.xml").read_text("utf-8")
        block = re.search(r'<receiver\s+android:name="\.PeerActionReceiver"[^>]*/>', manifest)
        assert block, "PeerActionReceiver non dichiarato nel manifest"
        assert 'android:exported="false"' in block.group(0)
        assert "intent-filter" not in block.group(0)

    @pytest.mark.parametrize("folder", ["values", "values-it"])
    def test_button_labels_are_localized(self, folder):
        strings = (Path(ANDROID_MAIN) / "res" / folder / "strings.xml").read_text("utf-8")
        for key in ("peer_allow_label", "peer_deny_label", "peer_undo_label"):
            assert re.search(rf'<string name="{key}">[^<]+</string>', strings), (folder, key)
