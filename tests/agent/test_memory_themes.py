"""Il tema di ogni frase di ``USER.md`` (``agent/memory_themes.py``).

Il tema sta in ``state.sqlite``, non nel file: Dream non lo puo' cancellare
riordinando. Lo assegna una chiamata piccola al modello, solo per le frasi che
non l'hanno; una frase riscritta lo eredita da quella che ha sostituito.

I dati sono inventati (il repo e' pubblico).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent import memory_themes as mt
from jenny.agent.memory_archive import ArchivedEntry, archive_entry
from jenny.agent.tools.memory_entries import entry_id

USER = """# User

- Name: Alex Doe
- Has a sister, Marta, in Turin
- Allergic to grass pollen
"""
NAME = entry_id("- Name: Alex Doe")
SISTER = entry_id("- Has a sister, Marta, in Turin")
POLLEN = entry_id("- Allergic to grass pollen")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "USER.md").write_text(USER, encoding="utf-8")
    (tmp_path / "memory").mkdir()
    mt._RETRY_AT.clear()
    mt._MISSES.clear()
    mt._IN_FLIGHT.clear()
    return tmp_path


class FakeProvider:
    """Risponde con *answer*, e tiene quel che gli e' stato chiesto."""

    def __init__(self, answer: str = "", *, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.asked: list[str] = []

    async def chat_with_retry(self, *, model, messages, tools=None, tool_choice=None):
        self.asked.append(messages[-1]["content"])
        if self.fail:
            raise RuntimeError("no network")
        return SimpleNamespace(content=self.answer, finish_reason="stop")


def test_the_answer_is_read_leniently() -> None:
    text = 'Sure:\n```json\n{"1": "people", "2": "Health", "3": "sports", "9": "home", "x": "home"}\n```'
    assert mt.parse_answer(text, 3) == {1: "people", 2: "health", 3: mt.OTHER}
    assert mt.parse_answer("no json here", 3) == {}
    assert mt.parse_answer('["people"]', 3) == {}
    assert mt.parse_answer(None, 3) == {}


async def test_the_model_sorts_what_has_no_theme(ws) -> None:
    provider = FakeProvider('{"1": "identity", "2": "people", "3": "health"}')

    assert await mt.sort_unsorted(ws, (provider, "m")) == 3

    assert mt.MemoryThemes(ws).all() == {NAME: "identity", SISTER: "people", POLLEN: "health"}
    assert mt.unsorted_entries(ws) == []
    # La lista numerata porta le frasi, senza il trattino del file.
    assert provider.asked == ["1. Name: Alex Doe\n2. Has a sister, Marta, in Turin\n"
                              "3. Allergic to grass pollen"]


async def test_only_the_new_sentences_are_asked(ws) -> None:
    mt.MemoryThemes(ws).set_many({NAME: "identity", SISTER: "people"})
    provider = FakeProvider('{"1": "health"}')

    assert await mt.sort_unsorted(ws, (provider, "m")) == 1
    assert provider.asked == ["1. Allergic to grass pollen"]
    # Niente da fare: il modello non viene chiamato.
    assert await mt.sort_unsorted(ws, (provider, "m")) == 0
    assert len(provider.asked) == 1


async def test_a_rewritten_sentence_keeps_the_theme_of_the_one_it_replaced(ws) -> None:
    old = "- Has a sister called Marta who lives in Turin and fears flying"
    archive_entry(ws / "memory", ArchivedEntry(
        id=entry_id(old), text=old, source="USER.md", superseded_by=SISTER))
    mt.MemoryThemes(ws).set_many({entry_id(old): "people", NAME: "identity", POLLEN: "health"})
    provider = FakeProvider('{"1": "tastes"}')

    assert await mt.sort_unsorted(ws, (provider, "m")) == 1
    assert mt.MemoryThemes(ws).all()[SISTER] == "people"
    assert provider.asked == [], "una frase con un'eredita' non si chiede al modello"


async def test_a_sentence_the_model_keeps_skipping_ends_in_other(ws) -> None:
    provider = FakeProvider('{"1": "identity", "2": "people"}')

    await mt.sort_unsorted(ws, (provider, "m"))
    assert [e.id for e in mt.unsorted_entries(ws)] == [POLLEN]
    provider.answer = "{}"
    await mt.sort_unsorted(ws, (provider, "m"))

    assert mt.MemoryThemes(ws).all()[POLLEN] == mt.OTHER


async def test_without_a_model_nothing_is_invented(ws) -> None:
    assert await mt.sort_unsorted(ws, None) == 0
    assert mt.MemoryThemes(ws).all() == {}


async def test_the_background_run_is_one_at_a_time_and_backs_off_after_a_failure(ws) -> None:
    provider = FakeProvider(fail=True)

    assert mt.sort_in_background(ws, (provider, "m")) is True
    assert mt.sorting(ws) is True
    assert mt.sort_in_background(ws, (provider, "m")) is False, "due giri insieme"
    await asyncio.gather(*mt._TASKS)
    assert mt.sorting(ws) is False
    # Appena fallito: non si riprova a ogni rilettura della stanza.
    assert mt.sort_in_background(ws, (provider, "m")) is False
    assert len(provider.asked) == 1

    mt._RETRY_AT.clear()
    provider.fail = False
    provider.answer = '{"1": "identity", "2": "people", "3": "health"}'
    assert mt.sort_in_background(ws, (provider, "m")) is True
    await asyncio.gather(*mt._TASKS)
    assert len(mt.MemoryThemes(ws).all()) == 3


def test_the_prompt_names_every_theme() -> None:
    from jenny.utils.prompt_templates import render_template

    prompt = render_template("agent/memory_themes.md", strip=True)
    for theme in (*mt.THEMES, mt.OTHER):
        assert f"`{theme}`" in prompt, theme
