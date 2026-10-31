"""La presa che tiene Dream a un ciclo per volta, da sola.

Due proprietà, e la seconda è quella che conta: una presa che resta presa è
peggio di nessuna presa, perché spegne Dream fino al riavvio del processo e non
lo dice a nessuno.
"""

from __future__ import annotations

import asyncio

import pytest

from jenny.agent import dream_cycle
from jenny.agent.dream_cycle import claim_dream_cycle, release_dream_cycle
from jenny.runtime.user_text import say


@pytest.fixture(autouse=True)
def _clean_guard():
    release_dream_cycle()
    yield
    release_dream_cycle()


class TestTheClaim:
    @pytest.mark.asyncio
    async def test_the_first_claim_wins(self):
        assert claim_dream_cycle() is True

    @pytest.mark.asyncio
    async def test_the_second_claim_on_the_same_loop_is_refused(self):
        assert claim_dream_cycle() is True
        assert claim_dream_cycle() is False

    @pytest.mark.asyncio
    async def test_release_makes_it_available_again(self):
        assert claim_dream_cycle() is True
        release_dream_cycle()
        assert claim_dream_cycle() is True

    @pytest.mark.asyncio
    async def test_release_without_a_claim_is_not_an_error(self):
        release_dream_cycle()
        release_dream_cycle()
        assert claim_dream_cycle() is True


class TestAClaimFromADeadEventLoop:
    """L'unico cammino che nessun ``finally`` copre, e la sua uscita.

    Un task cancellato *prima* del suo primo passo non entra nella coroutine, e
    quindi il suo ``finally`` non gira: succede quando l'event loop viene
    smontato. Un loop che non c'è più non può avere un ciclo in volo, quindi la
    presa che gli appartiene è morta e va recuperata — altrimenti Dream resta
    spento e la causa è invisibile.
    """

    def test_a_new_event_loop_recovers_the_leaked_claim(self):
        async def _leak() -> None:
            assert claim_dream_cycle() is True  # di proposito: nessun rilascio

        asyncio.run(_leak())
        assert dream_cycle._CYCLE_IN_FLIGHT  # la presa è rimasta

        async def _again() -> bool:
            return claim_dream_cycle()

        assert asyncio.run(_again()) is True

    def test_the_recovery_does_not_let_two_cycles_through_on_one_loop(self):
        """Il recupero guarda l'identità del loop, non un timeout."""

        async def _both() -> tuple[bool, bool]:
            return claim_dream_cycle(), claim_dream_cycle()

        assert asyncio.run(_both()) == (True, False)


class TestTheRefusalText:
    def test_it_names_the_cost_that_is_actually_paid(self):
        """Token, non fatti.

        Da T2.4b ``make_entry_archiver`` archivia al confine del file ogni voce
        che esce da USER/MEMORY/SOUL, verificato sul device a
        ``reviewEveryRuns: 1``. Due passate di review consecutive costano una
        seconda bolletta, non informazione, e un rifiuto che spaventa più del
        dovuto è un rifiuto che mente.
        """
        text = say("cmd.dream.already_running", "en")
        assert "already running" in text
        assert "spent twice" in text
        assert "Nothing would be lost" in text
        # Nessuna parola da perdita di dati.
        for scary in ("delete", "destroy", "overwrite", "corrupt"):
            assert scary not in text.lower()
