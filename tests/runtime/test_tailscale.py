"""I due gesti con cui Jenny riaccende Tailscale (``jenny.runtime.tailscale``).

Sul telefono sono intent veri; qui un ``Intent`` finto registra cosa si
manderebbe e a chi. La politica (quando, quanto spesso) è in
``tests/channels/test_ws_remote_self_heal.py``.
"""

from __future__ import annotations

from typing import Any

from jenny.runtime import tailscale
from jenny.runtime.context import get_runtime_context


class _Intent:
    FLAG_ACTIVITY_NEW_TASK = 0x10000000
    FLAG_ACTIVITY_NO_ANIMATION = 0x00010000
    FLAG_ACTIVITY_REORDER_TO_FRONT = 0x00020000

    def __init__(self, action: str | None = None) -> None:
        self.action = action
        self.component: tuple[str, str] | None = None
        self.flags = 0

    def setClassName(self, package: str, cls: str) -> None:  # noqa: N802 - API Android
        self.component = (package, cls)

    def addFlags(self, flags: int) -> None:  # noqa: N802 - API Android
        self.flags |= flags


class _PackageManager:
    def __init__(self, installed: bool) -> None:
        self.installed = installed

    def getPackageInfo(self, package: str, flags: int) -> Any:  # noqa: N802 - API Android
        if not self.installed:
            raise RuntimeError("NameNotFoundException")
        return object()


class _Context:
    def __init__(self, *, installed: bool = True) -> None:
        self.pm = _PackageManager(installed)
        self.broadcasts: list[_Intent] = []
        self.started: list[_Intent] = []

    def getPackageManager(self) -> _PackageManager:  # noqa: N802 - API Android
        return self.pm

    def getPackageName(self) -> str:  # noqa: N802 - API Android
        return "com.flagdizero.jenny"

    def sendBroadcast(self, intent: _Intent) -> None:  # noqa: N802 - API Android
        self.broadcasts.append(intent)

    def startActivity(self, intent: _Intent) -> None:  # noqa: N802 - API Android
        self.started.append(intent)


def _on_phone(monkeypatch, context: _Context) -> None:
    monkeypatch.setattr(get_runtime_context(), "android_context", context)
    monkeypatch.setattr(tailscale, "_intent_class", lambda: _Intent)
    monkeypatch.setattr(tailscale, "_BACK_AFTER_S", 0.0)
    monkeypatch.setattr(tailscale, "_can_come_back", lambda: True)


async def test_off_the_phone_it_does_nothing(monkeypatch) -> None:
    monkeypatch.setattr(get_runtime_context(), "android_context", None)
    assert await tailscale.revive(open_app=True) is None


async def test_the_broadcast_goes_to_tailscale_receiver(monkeypatch) -> None:
    ctx = _Context()
    _on_phone(monkeypatch, ctx)
    assert await tailscale.revive(open_app=False) == tailscale.BROADCAST
    [intent] = ctx.broadcasts
    assert intent.action == "com.tailscale.ipn.CONNECT_VPN"
    assert intent.component == ("com.tailscale.ipn", "com.tailscale.ipn.IPNReceiver")
    assert ctx.started == []


async def test_open_app_shows_tailscale_then_brings_jenny_back(monkeypatch) -> None:
    ctx = _Context()
    _on_phone(monkeypatch, ctx)
    assert await tailscale.revive(open_app=True) == tailscale.OPENED_APP
    assert len(ctx.broadcasts) == 1
    first, back = ctx.started
    assert first.component == ("com.tailscale.ipn", "com.tailscale.ipn.MainActivity")
    assert first.flags & _Intent.FLAG_ACTIVITY_NEW_TASK
    assert back.component == ("com.flagdizero.jenny", "com.flagdizero.jenny.MainActivity")
    assert back.flags & _Intent.FLAG_ACTIVITY_REORDER_TO_FRONT


async def test_if_jenny_could_not_come_back_the_app_is_not_opened(monkeypatch) -> None:
    """Senza la mascotte a schermo Android blocca il ritorno (visto su
    jenny_s22): il gesto si dice solo messaggio, e l'ascoltatore non lo conta
    come app vista."""
    ctx = _Context()
    _on_phone(monkeypatch, ctx)
    monkeypatch.setattr(tailscale, "_can_come_back", lambda: False)
    assert await tailscale.revive(open_app=True) == tailscale.BROADCAST
    assert len(ctx.broadcasts) == 1 and ctx.started == []


def test_when_it_cannot_tell_jenny_does_not_open_tailscale() -> None:
    """Senza Chaquopy (o con una classe sparita) non si sa: niente apertura."""
    assert tailscale._can_come_back() is False


async def test_not_installed_sends_nothing(monkeypatch) -> None:
    ctx = _Context(installed=False)
    _on_phone(monkeypatch, ctx)
    assert await tailscale.revive(open_app=True) is None
    assert ctx.broadcasts == [] and ctx.started == []


async def test_a_java_error_is_swallowed(monkeypatch) -> None:
    ctx = _Context()
    _on_phone(monkeypatch, ctx)

    def boom(_intent: Any) -> None:
        raise RuntimeError("SecurityException")

    ctx.sendBroadcast = boom  # type: ignore[method-assign]
    assert await tailscale.revive(open_app=False) is None
