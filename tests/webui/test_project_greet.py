"""Il saluto di un quaderno appena nato (``project.greet``).

Le regole stanno nella docstring di ``jenny/webui/project_greet.py``: qui si
misura che valgano — solo su una chat vuota, mai sotto un turno, mai una chat
vuota per colpa del modello, e un messaggio che arriva per la strada degli
avvisi di Jenny (sessione, frame, ``turn_end``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.session.manager import Session
from jenny.webui import commands
from jenny.webui import project_greet as module
from jenny.webui.commands import CommandError
from jenny.webui.metadata import WEBUI_TURN_METADATA_KEY


class _Sessions:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.sessions: dict[str, Session] = {}
        self.preset = messages or []
        self.saved: list[str] = []

    def get_or_create(self, key: str) -> Session:
        if key not in self.sessions:
            self.sessions[key] = Session(key=key, messages=list(self.preset))
        return self.sessions[key]

    def save(self, session: Session) -> None:
        self.saved.append(session.key)


class _Bus:
    def __init__(self) -> None:
        self.out: list[Any] = []

    async def publish_outbound(self, msg: Any) -> None:
        self.out.append(msg)


class _Provider:
    def __init__(self, reply: Any = "Ciao Anna! Qui parliamo dell'orto. Da dove partiamo?") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def chat_with_retry(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, BaseException):
            raise self.reply
        if self.reply == "slow":
            await asyncio.sleep(5)
        return SimpleNamespace(content=self.reply, finish_reason="stop")


@pytest.fixture(autouse=True)
def _no_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_transcript_lines", lambda key: [])


async def _greet(
    tmp_path: Path,
    *,
    provider: _Provider | None,
    sessions: _Sessions | None = None,
    busy: tuple[str, ...] = (),
    scope: str = "l'orto sul balcone: semine, raccolti, parassiti",
    language: str = "it",
) -> tuple[dict[str, Any], _Sessions, _Bus]:
    sessions = sessions or _Sessions()
    bus = _Bus()
    out = await module.greet_project(
        name="orto",
        wiki_root=tmp_path / "wikis" / "orto",
        workspace=tmp_path,
        language=language,
        bot_name="Jenny",
        session_manager=sessions,
        bus=bus,
        llm=(provider, "model-x") if provider is not None else None,
        busy_session_keys=lambda: busy,
        read_scope=lambda root: scope,
    )
    return out, sessions, bus


async def test_the_model_greeting_goes_into_the_session_and_to_the_chat(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text("# User\n\nName: Anna\n", encoding="utf-8")
    provider = _Provider()
    out, sessions, bus = await _greet(tmp_path, provider=provider)

    text = "Ciao Anna! Qui parliamo dell'orto. Da dove partiamo?"
    assert out == {"greeted": True, "fallback": False, "text": text}
    messages = sessions.sessions["project:orto"].messages
    assert [(m["role"], m["content"]) for m in messages] == [("assistant", text)]
    assert sessions.saved == ["project:orto"]

    message, end = bus.out
    assert (message.channel, message.chat_id, message.content) == ("websocket", "project:orto", text)
    assert end.metadata.get("_turn_end") is True, "senza turn_end la mascotte resta a pensare"
    assert message.metadata[WEBUI_TURN_METADATA_KEY] == end.metadata[WEBUI_TURN_METADATA_KEY]

    prompt = provider.calls[0]["messages"][0]["content"]
    assert "orto" in prompt and "semine, raccolti, parassiti" in prompt
    assert "Name: Anna" in prompt and "Italian" in prompt
    assert provider.calls[0]["tools"] is None, "il saluto non e' un turno dell'agente"


@pytest.mark.parametrize("failure", [RuntimeError("rete giu'"), "", "x" * 2000])
async def test_a_model_that_fails_still_leaves_a_greeting(tmp_path: Path, failure: Any) -> None:
    """Errore, risposta vuota, muro di testo: la frase fissa."""
    out, sessions, bus = await _greet(tmp_path, provider=_Provider(failure))
    assert out["fallback"] is True
    assert out["text"] == (
        "Ciao! Questo è il nostro spazio per parlare di: "
        "l'orto sul balcone: semine, raccolti, parassiti. Cominciamo?"
    )
    assert sessions.sessions["project:orto"].messages[0]["content"] == out["text"]
    assert len(bus.out) == 2


async def test_no_model_at_all_is_the_fixed_sentence_in_english(tmp_path: Path) -> None:
    out, _, _ = await _greet(tmp_path, provider=None, language="en")
    assert out["text"].startswith("Hi! This is our space to talk about: l'orto sul balcone")


async def test_a_slow_model_gives_way_to_the_fixed_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "GREETING_TIMEOUT_S", 0.05)
    out, _, _ = await _greet(tmp_path, provider=_Provider("slow"))
    assert out["fallback"] is True


async def test_a_notebook_without_a_scope_line_is_named(tmp_path: Path) -> None:
    out, _, _ = await _greet(tmp_path, provider=None, scope="(no scope set)")
    assert out["text"] == "Ciao! Questo è il nostro spazio per parlare di: orto. Cominciamo?"


async def test_a_conversation_picked_up_is_not_greeted(tmp_path: Path) -> None:
    """«Riprendila»: la chat ha gia' una storia, e un saluto in fondo sarebbe
    un «piacere di conoscerti» a meta' discorso."""
    provider = _Provider()
    sessions = _Sessions([{"role": "user", "content": "dove eravamo?"}])
    out, sessions, bus = await _greet(tmp_path, provider=provider, sessions=sessions)
    assert out == {"greeted": False, "reason": "not_empty"}
    assert provider.calls == [] and bus.out == [] and sessions.saved == []


async def test_a_transcript_without_a_session_still_counts_as_a_story(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "read_transcript_lines", lambda key: [{"event": "user"}])
    out, _, bus = await _greet(tmp_path, provider=_Provider())
    assert out["greeted"] is False and bus.out == []


async def test_a_user_who_wrote_meanwhile_is_not_interrupted(tmp_path: Path) -> None:
    """Il modello ha pensato per secondi e l'utente ha gia' scritto: il saluto
    arriverebbe dopo la sua domanda, e si butta."""
    out, sessions, bus = await _greet(tmp_path, provider=_Provider(), busy=("project:orto",))
    assert out["greeted"] is False
    assert bus.out == [] and sessions.saved == []


async def test_the_command_refuses_a_bad_name_and_a_missing_notebook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    monkeypatch.setattr(commands, "_wikis_dir", lambda ctx: tmp_path / "wikis")
    ctx = SimpleNamespace(
        get_workspace_root=lambda: tmp_path,
        busy_session_keys=lambda: (),
        session_manager=_Sessions(),
        bus=_Bus(),
        get_llm=lambda: None,
    )
    with pytest.raises(CommandError) as err:
        await commands.project_greet(ctx, {"name": "../fuori"})
    assert err.value.code == "bad_request"
    with pytest.raises(CommandError) as err:
        await commands.project_greet(ctx, {"name": "nessuno"})
    assert err.value.code == "not_found"


async def test_the_command_reads_the_scope_line_the_user_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dalla radice del quaderno, dove sta ``AGENTS.md`` — non dalla sua
    ``wiki/``. Visto sull'emulatore il 10/10/2026: il modello riceveva il nome
    al posto della riga, perche' il comando cercava lo scope nella pages-dir."""
    root = tmp_path / "wikis" / "grasse"
    (root / "wiki").mkdir(parents=True)
    (root / "AGENTS.md").write_text(
        '---\nid: 979d3946307a\nsummary: "piante grasse di casa"\n---\n\n# Grasse\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    monkeypatch.setattr(commands, "_wikis_dir", lambda ctx: tmp_path / "wikis")
    ctx = SimpleNamespace(
        get_workspace_root=lambda: tmp_path,
        busy_session_keys=lambda: (),
        session_manager=_Sessions(),
        bus=_Bus(),
        get_llm=lambda: None,
    )
    out = await commands.project_greet(ctx, {"name": "grasse", "lang": "it"})
    assert out["text"] == "Ciao! Questo è il nostro spazio per parlare di: piante grasse di casa. Cominciamo?"
