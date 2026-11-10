"""Le richieste oltre il limite del provider aspettano un posto (``request_slots``).

Il 04/11/2026 il gateway della Play reggeva 2 richieste insieme, gli aiutanti
erano 3 più Jenny, e un aiutante è morto su un 429 dopo 14 passi.
"""

import asyncio

import pytest
from support.aio import wait_until

from jenny.providers.base import LLMProvider, LLMResponse
from jenny.providers.request_slots import RequestSlots


class SlowProvider(LLMProvider):
    """Ogni richiesta dura finché il test non la lascia andare."""

    def __init__(self, limit: int | None) -> None:
        super().__init__()
        self.max_concurrent_requests = limit
        self.in_flight = 0
        self.peak = 0
        self.started: list[str] = []
        self.release = asyncio.Event()

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.started.append(kwargs["messages"][0]["content"])
        await self.release.wait()
        self.in_flight -= 1
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "test-model"


def _ask(provider: SlowProvider, who: str, mode: str):
    return asyncio.create_task(provider.chat_with_retry(
        messages=[{"role": "user", "content": who}], retry_mode=mode))


def _waiting(provider: SlowProvider) -> int:
    """Le richieste ferme in coda sulla condizione dei posti."""
    slots = provider._request_slots
    cond = getattr(slots, "_cond", None)
    # ``Condition._waiters`` è privato ma stabile: un ``wait`` vi lascia il suo future.
    return len(cond._waiters) if cond is not None else 0


async def _settle(provider: SlowProvider, expected: int) -> None:
    """Aspetta che ognuna delle *expected* richieste sia arrivata dove resterà:
    dentro ``chat``, ferma su ``release``, o in coda per un posto. Da lì nessuna
    si muove finché il test non lascia andare qualcosa, quindi anche le
    asserzioni negative che seguono («non è partita») guardano uno stato fermo,
    invece di un numero di giri del loop che poteva non bastare."""
    await wait_until(lambda: provider.in_flight + _waiting(provider) == expected,
                     msg="le richieste non si sono fermate né in volo né in coda")


@pytest.mark.asyncio
async def test_never_more_requests_than_the_provider_takes() -> None:
    provider = SlowProvider(limit=2)
    tasks = [_ask(provider, f"helper {i}", "background") for i in range(4)]
    tasks.append(_ask(provider, "jenny", "standard"))
    await _settle(provider, 5)
    assert provider.in_flight == 2
    provider.release.set()
    results = await asyncio.gather(*tasks)
    assert all(r.content == "ok" for r in results)
    assert provider.peak == 2


@pytest.mark.asyncio
async def test_helpers_leave_a_slot_for_jenny() -> None:
    provider = SlowProvider(limit=2)
    helpers = [_ask(provider, f"helper {i}", "background") for i in range(3)]
    await _settle(provider, 3)
    # Due posti: gli aiutanti ne prendono uno, l'altro resta libero.
    assert provider.started == ["helper 0"]
    jenny = _ask(provider, "jenny", "standard")
    await _settle(provider, 4)
    assert provider.started == ["helper 0", "jenny"]
    provider.release.set()
    await asyncio.gather(jenny, *helpers)


@pytest.mark.asyncio
async def test_without_a_limit_nothing_waits() -> None:
    provider = SlowProvider(limit=None)
    tasks = [_ask(provider, f"helper {i}", "background") for i in range(5)]
    await _settle(provider, 5)
    assert provider.in_flight == 5
    provider.release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_a_failed_attempt_gives_its_slot_back() -> None:
    slots = RequestSlots(1)
    with pytest.raises(RuntimeError):
        async with slots.hold(background=False):
            raise RuntimeError("boom")
    async with slots.hold(background=False):
        assert slots.in_flight == 1
    assert slots.in_flight == 0
