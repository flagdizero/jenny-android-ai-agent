"""Test per jenny/runtime/notifier.py (alert di sistema Android).

Il bridge Chaquopy non esiste nei test desktop: si verifica la derivazione
pura dei campi (``alert_fields``), il no-op senza contesto Android e il
percorso completo con un bridge finto.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jenny.runtime import notifier, user_text
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.replies: list[tuple[str, str, str]] = []
        self.result = True

    def postAlert(self, title: str, body: str, tag: str) -> bool:  # noqa: N802
        self.calls.append((title, body, tag))
        return self.result

    def postReply(self, title: str, body: str, tag: str) -> bool:  # noqa: N802
        self.replies.append((title, body, tag))
        return self.result


def _meta(kind: str, label: str | None = None) -> dict[str, Any]:
    source: dict[str, str] = {"kind": kind}
    if label is not None:
        source["label"] = label
    return {WEBUI_MESSAGE_SOURCE_METADATA_KEY: source}


@pytest.fixture(autouse=True)
def _italian_jenny(monkeypatch):
    """Lingua e nome fissati: i titoli dipendono da ``config.json``, e qui no."""
    monkeypatch.setattr(user_text, "current_language", lambda: "it")
    monkeypatch.setattr(notifier, "current_bot_name", lambda: "Jenny")


class TestAlertFields:
    def test_cron_with_label(self):
        title, body, tag = notifier.alert_fields("ricordati il pane", _meta("cron", "spesa"))
        assert title == "Jenny · ⏰ Promemoria · spesa"
        assert tag == "cron:spesa"
        assert body == "ricordati il pane"

    def test_cron_without_label(self):
        title, _, tag = notifier.alert_fields("ping", _meta("cron"))
        assert title == "Jenny · ⏰ Promemoria"
        assert tag == "cron"

    def test_cron_blank_label_falls_back(self):
        title, _, tag = notifier.alert_fields("ping", _meta("cron", "   "))
        assert title == "Jenny · ⏰ Promemoria"
        assert tag == "cron"

    def test_heartbeat(self):
        title, _, tag = notifier.alert_fields("evento X accaduto", _meta("heartbeat"))
        assert title == "Jenny · Monitoraggio"
        assert tag == "heartbeat"

    def test_update(self):
        # Tag dedicato: l'annuncio in chat e l'alert esplicito di un update
        # critico coalizzano invece di suonare due volte.
        title, _, tag = notifier.alert_fields("nuova versione 0.7.0", _meta("update"))
        assert title == "Jenny · Aggiornamento"
        assert tag == "update"

    def test_plain_message_defaults(self):
        for metadata in (None, {}, {"latency_ms": 3}):
            title, _, tag = notifier.alert_fields("ciao", metadata)
            assert title == "Jenny"
            assert tag == "message"

    def test_malformed_source_is_ignored(self):
        title, _, tag = notifier.alert_fields(
            "ciao", {WEBUI_MESSAGE_SOURCE_METADATA_KEY: "cron"}
        )
        assert title == "Jenny"
        assert tag == "message"

    def test_body_collapses_whitespace_and_truncates(self):
        _, body, _ = notifier.alert_fields("riga1\n\n  riga2\t fine", None)
        assert body == "riga1 riga2 fine"
        _, long_body, _ = notifier.alert_fields("x" * 500, None)
        assert len(long_body) == notifier._BODY_MAX_CHARS
        assert long_body.endswith("…")

    def test_empty_body_placeholder(self):
        _, body, _ = notifier.alert_fields("   \n  ", None)
        assert body == "Nuovo messaggio"

    def test_english_titles_follow_the_language(self, monkeypatch):
        monkeypatch.setattr(user_text, "current_language", lambda: "en")
        assert notifier.alert_fields("x", _meta("cron"))[0] == "Jenny · ⏰ Reminder"
        assert notifier.alert_fields("x", _meta("heartbeat"))[0] == "Jenny · Monitoring"
        assert notifier.alert_fields("x", _meta("update"))[0] == "Jenny · Update"
        assert notifier.alert_fields("   ", None)[1] == "New message"

    def test_title_uses_the_configured_bot_name(self, monkeypatch):
        monkeypatch.setattr(notifier, "current_bot_name", lambda: "Ada")
        assert notifier.alert_fields("x", _meta("cron", "pane"))[0] == "Ada · ⏰ Promemoria · pane"
        assert notifier.alert_fields("x", None)[0] == "Ada"

    def test_body_is_plain_text_before_truncation(self):
        """Il markdown si toglie prima di tagliare: i 200 caratteri vanno al testo."""
        md = "## Fatto\n\n**Pronto!** Vedi [il file](https://example.com/" + "a" * 300 + ")"
        _, body, _ = notifier.alert_fields(md, None)
        assert body == "Fatto Pronto! Vedi il file"


class TestPostAlert:
    async def test_noop_without_android_context(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: None)
        assert await notifier.post_alert("ciao", None) is False

    async def test_posts_via_bridge(self, monkeypatch):
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        ok = await notifier.post_alert("ricordati il pane", _meta("cron", "spesa"))
        assert ok is True
        assert bridge.calls == [("Jenny · ⏰ Promemoria · spesa", "ricordati il pane", "cron:spesa")]

    async def test_thread_overrides_the_tag_and_leaves_the_title(self, monkeypatch):
        """Il canale della tendina passa un thread unico.

        Il tag dice *dove* va la notifica — e le risposte dell'agente devono
        coalizzare su una voce sola invece di impilarsi una per messaggio — ma
        il titolo continua a dire *di cosa parla*, quindi resta quello che
        ``alert_fields`` deriva dai metadata.
        """
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        await notifier.post_alert("ecco", _meta("cron", "spesa"), thread="chat")
        assert bridge.calls == [("Jenny · ⏰ Promemoria · spesa", "ecco", "chat")]

    async def test_without_thread_nothing_changes(self, monkeypatch):
        """Gli avvisi proattivi tengono i loro tag distinti."""
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        await notifier.post_alert("ecco", _meta("heartbeat"), thread=None)
        assert bridge.calls[0][2] == "heartbeat"

    async def test_a_reply_to_the_webui_goes_through_post_reply(self, monkeypatch):
        """Una risposta a quel che l'utente ha scritto nell'app tace con l'app
        davanti (``postReply``); un avviso no (``postAlert``). Dentro un
        quaderno la chat personale non è mai a schermo, e col solo
        ``postAlert`` ogni risposta lì squillava (10/10/2026)."""
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        await notifier.post_alert("ecco la risposta", {"webui": True, "conn_id": "c1"})
        await notifier.post_alert("ricordati il pane", _meta("cron", "spesa"))
        assert bridge.replies == [("Jenny", "ecco la risposta", "message")]
        assert bridge.calls == [("Jenny · ⏰ Promemoria · spesa", "ricordati il pane", "cron:spesa")]

    def test_only_a_true_webui_flag_is_a_reply(self):
        assert notifier.is_app_reply({"webui": True}) is True
        assert notifier.is_app_reply({"webui": "true"}) is False
        assert notifier.is_app_reply({}) is False
        assert notifier.is_app_reply(None) is False

    async def test_bridge_error_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def broken_get_bridge(context: Any) -> Any:
            raise RuntimeError("no chaquopy here")

        monkeypatch.setattr(notifier, "_get_bridge", broken_get_bridge)
        assert await notifier.post_alert("ciao", None) is False


class TestNotifyDelivery:
    async def test_noop_without_android_context(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: None)
        notifier.notify_delivery("ciao", None)
        assert not notifier._TASKS

    async def test_schedules_fire_and_forget_task(self, monkeypatch):
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        notifier.notify_delivery("evento X", _meta("heartbeat"))
        assert notifier._TASKS
        await asyncio.gather(*notifier._TASKS)
        assert bridge.calls == [("Jenny · Monitoraggio", "evento X", "heartbeat")]
        assert not notifier._TASKS


class TestNotebookConversation:
    """Un messaggio consegnato nella chat di un quaderno porta il quaderno nel
    tag, da cui Kotlin ricava dove portare il tocco: fino al 10/10/2026 ogni
    notifica apriva la chat personale, anche la risposta di un quaderno."""

    def test_a_project_chat_id_is_the_conversation(self):
        assert notifier.alert_conversation("project:Casa") == "project:Casa"

    def test_the_personal_chat_has_no_conversation(self):
        assert notifier.alert_conversation("default") is None
        assert notifier.alert_conversation(None) is None
        assert notifier.alert_conversation("") is None

    def test_an_invalid_project_name_falls_back_to_the_personal_chat(self):
        """Stessa regola della sessione (``session_key_for_channel``)."""
        assert notifier.alert_conversation("project:../fuori") is None
        assert notifier.alert_conversation("project:") is None

    def test_a_reply_in_a_notebook_names_it_in_tag_and_title(self):
        title, _, tag = notifier.alert_fields("ecco", None, conversation="project:Casa")
        assert tag == "project:Casa|message"
        assert title == "Jenny · Casa"

    def test_a_notebook_cron_keeps_its_title_and_gains_the_notebook(self):
        """Un cron del quaderno consegna nel quaderno: il tocco deve portare lì."""
        title, _, tag = notifier.alert_fields(
            "annaffia", _meta("cron", "Piante"), conversation="project:Casa"
        )
        assert tag == "project:Casa|cron:Piante"
        assert title == "Jenny · ⏰ Promemoria · Piante"

    def test_without_a_conversation_the_tags_do_not_change(self):
        assert notifier.alert_fields("ciao", None)[2] == "message"
        assert notifier.alert_fields("x", _meta("cron", "spesa"))[2] == "cron:spesa"

    async def test_notify_delivery_carries_the_chat_id_to_the_bridge(self, monkeypatch):
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        notifier.notify_delivery("fatto", {"webui": True}, chat_id="project:Casa")
        notifier.notify_delivery("ciao", {"webui": True}, chat_id="default")
        await asyncio.gather(*notifier._TASKS)
        assert bridge.replies == [
            ("Jenny · Casa", "fatto", "project:Casa|message"),
            ("Jenny", "ciao", "message"),
        ]

    async def test_the_shade_thread_still_wins(self, monkeypatch):
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        await notifier.post_alert("ecco", None, thread="chat", chat_id="project:Casa")
        assert bridge.calls[0][2] == "chat"
