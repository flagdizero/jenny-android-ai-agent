"""Lo slash command ``/gardener``.

Esiste per due ragioni, e la seconda è la vera: **senza di lui il passo T4 non è
collaudabile.** I tre orologi dell'innesco — delta, trenta minuti di fermo, sei
ore di distanza dall'ultima passata — rendono la strada naturale impossibile da
percorrere in una sessione di prova.

Qui si verifica il routing, il modo in cui il comando capisce *su quale progetto*
lavorare, e che ogni esito dica davvero cosa è successo: i modi di non fare
niente sono tre, e vogliono dire cose molto diverse.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jenny.agent.gardener import GardenerOutcome
from jenny.bus.events import InboundMessage
from jenny.command.builtin import (
    _format_gardener_outcome,
    cmd_gardener,
    register_builtin_commands,
)
from jenny.command.router import CommandContext, CommandRouter


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


def _ctx(key: str, args: str = "") -> CommandContext:
    msg = InboundMessage(
        channel="websocket", sender_id="user", chat_id="c1",
        content=f"/gardener {args}".strip(),
    )
    return CommandContext(
        msg=msg, session=None, key=key, raw=msg.content, args=args,
        # ``_schedule_background`` come nell'``AgentLoop`` vero: passa da
        # ``asyncio.create_task``, che la fixture ``started`` inghiotte.
        loop=SimpleNamespace(bus=None, _schedule_background=asyncio.create_task),
    )


class TestRegistration:
    def test_the_prefix_stays_dispatchable_on_purpose(self, router):
        """Il comando non prende argomenti, ma il prefisso resta registrato.

        Serve a **intercettare** le forme vecchie (`/gardener settings`, un nome
        di progetto): togliendolo, quelle righe non sarebbero più comandi e il
        router le lascerebbe passare al modello come messaggi.
        """
        assert router.is_dispatchable_command("/gardener")
        assert router.is_dispatchable_command("/gardener viaggio")

    def test_is_listed_among_builtin_commands(self):
        from jenny.command.specs import BUILTIN_COMMAND_SPECS

        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}

        assert "/gardener" in specs
        # Niente suggerimento di argomento: il bersaglio è la conversazione in cui
        # si è, e le manopole della passata periodica stanno in Impostazioni.
        assert specs["/gardener"].arg_hint == ""
        assert specs["/gardener"].scope == "project"


@pytest.fixture()
def no_background(monkeypatch):
    """Il comando avvia la passata in una task e risponde subito.

    Nei test che guardano *solo* la scelta del bersaglio quella task non ha un
    bus dove pubblicare: lasciarla partire vuol dire un'eccezione raccolta da
    nessuno e un event loop che si chiude sotto una coroutine viva — il tipo di
    rumore che diventa flakiness in un altro file. Qui si chiude, e resta la sola
    domanda che il test pone.
    """
    started: list[str] = []

    def _swallow(coro):
        started.append(getattr(coro, "__name__", "coro"))
        coro.close()
        return None

    monkeypatch.setattr(asyncio, "create_task", _swallow)
    return started


class TestPickingTheProject:
    async def test_outside_a_project_it_refuses_and_says_how(self):
        """Un rifiuto che dice **dove**, non solo che qui non si può: la lezione
        dei rifiuti del passo 6, e la stessa forma del rifiuto di
        ``journal_append`` fuori da un progetto. Un rifiuto che manda via a mani
        vuote costa un altro turno.

        Non offre più di nominare un progetto (31/08/2026): il lavoro su un
        progetto si fa da dentro, quindi la strada che indica è aprirlo.
        """
        out = await cmd_gardener(_ctx("unified:default"))

        assert "not a project" in out.content
        assert "/gardener <project>" not in out.content
        assert "Open the project" in out.content

    async def test_inside_a_project_the_target_is_that_project(self, no_background):
        """Nessun argomento da scrivere: la conversazione in cui si è **è** il
        bersaglio. Il nome viene dalla chiave, come per lo scope di un turno."""
        out = await cmd_gardener(_ctx("project:viaggio-lento"))

        assert "viaggio-lento" in out.content
        assert no_background, "la passata deve partire, non solo essere annunciata"

    async def test_naming_another_project_is_not_a_way_in(self, no_background):
        """Era `/gardener <progetto>`: il telecomando dalla chat personale.

        Il lavoro su un progetto si fa da dentro il progetto — la regola che il
        layer dei tool aveva già (``journal_append`` fuori da uno rifiuce e non
        ha un argomento con cui aggirarsi) e che questo comando era l'unico a
        rompere. Da dentro un progetto, un nome non dirotta la passata su un
        altro: non parte niente.
        """
        out = await cmd_gardener(_ctx("project:viaggio", args="altro"))

        assert "altro" not in out.content
        assert not no_background, "nessuna passata deve partire"

    async def test_the_settings_words_say_where_the_knobs_went(self, no_background):
        """Sette parole al posto di un nome taravano la passata periodica.

        Con quelle manopole in Impostazioni resta un solo significato per il
        comando, e chi digita la forma vecchia trova la strada invece del
        silenzio.
        """
        out = await cmd_gardener(_ctx("project:viaggio", args="settings"))

        assert "Settings \u2192 Workshop \u2192 Memory" in out.content
        assert not no_background


class TestOutcomeMessages:
    def test_every_status_says_something_specific(self):
        messages = {
            status: _format_gardener_outcome(
                "viaggio",
                GardenerOutcome(status=status, elapsed=1.25, lines=4, writes=2, detail="boom"),
            )
            for status in (
                "skipped_no_delta",
                "written",
                "nothing_to_promote",
                "partial_write",
                "commit_failed",
                "no_write",
                "incomplete",
                "failed",
                "aborted_user_active",
                "already_running",
            )
        }

        assert len(set(messages.values())) == len(messages)
        assert all(text.strip() for text in messages.values())

    def test_nothing_new_says_it_cost_nothing(self):
        text = _format_gardener_outcome("viaggio", GardenerOutcome(status="skipped_no_delta"))

        assert "no tokens spent" in text

    def test_a_board_pass_on_an_empty_journal_does_not_claim_it_cost_nothing(self):
        text = _format_gardener_outcome(
            "viaggio", GardenerOutcome(status="skipped_no_delta", board="done")
        )

        assert "no tokens spent" not in text
        assert "envelope" in text

    def test_the_board_sentence_follows_the_wiki_one(self):
        text = _format_gardener_outcome(
            "viaggio", GardenerOutcome(status="written", lines=4, writes=2, board="failed")
        )

        assert text.startswith("The gardener read 4 journal lines")
        assert text.endswith("the next pass retries.")

    def test_the_two_ways_of_writing_nothing_are_not_the_same_sentence(self):
        """«non c'era niente che meritasse una pagina» e «ho provato e sono stato
        bloccato» sono lo stesso silenzio visto da fuori e due fatti opposti: il
        primo chiude il materiale, il secondo lo lascia da rileggere. Se le due
        frasi si somigliassero, la differenza si perderebbe proprio dove serve."""
        promoted = _format_gardener_outcome(
            "viaggio", GardenerOutcome(status="nothing_to_promote", lines=4, elapsed=2.0)
        )
        blocked = _format_gardener_outcome(
            "viaggio", GardenerOutcome(status="no_write", lines=4, elapsed=2.0)
        )

        assert "marked as read" in promoted
        assert "will try again" in blocked

    def test_a_half_done_pass_is_not_reported_as_a_failure(self):
        """I due esiti a metà — scritture rifiutate, cursore non registrato —
        cadevano nel fondo, cioè si raccontavano come «the gardener failed»: falso
        due volte, perché le pagine sono su disco. E non possono nemmeno prendere
        in prestito la frase di ``no_write``: «finished without writing» è la
        stessa bugia al contrario."""
        partial = _format_gardener_outcome(
            "viaggio",
            GardenerOutcome(
                status="partial_write", elapsed=2.0, lines=4, writes=2,
                detail="2 of 3 writes landed; 1 refused",
            ),
        )
        stuck = _format_gardener_outcome(
            "viaggio",
            GardenerOutcome(
                status="commit_failed", elapsed=2.0, lines=4, writes=2,
                detail="no space left on device",
            ),
        )

        for text in (partial, stuck):
            assert "The gardener failed" not in text
            assert "without writing" not in text
            # Le pagine scritte si nominano: è il fatto che l'esito rischiava di
            # perdere.
            assert "wrote 2 pages" in text
            assert "see those lines again" in text
        assert "refused" in partial
        assert "no space left on device" in stuck
        assert "on disk" in stuck

    def test_a_pass_that_gave_way_to_the_user_says_so_and_not_that_it_finished(self):
        """Una passata ceduta può aver lasciato pagine su disco, quindi non può
        prendere in prestito né la frase di ``no_write`` («finished without
        writing») né quella del fondo («the gardener failed»): non ha finito e non
        è fallita, si è spostata. E il motivo va detto, perché è l'unico esito che
        parla di **chi altro c'era** e non del lavoro."""
        text = _format_gardener_outcome(
            "viaggio",
            GardenerOutcome(status="aborted_user_active", elapsed=18.0, lines=4, writes=2),
        )

        assert "The gardener failed" not in text
        assert "without writing" not in text
        assert "came back to viaggio" in text
        assert "2 pages had already landed" in text
        assert "see those lines again" in text

    def test_a_refused_second_pass_says_one_at_a_time(self):
        """``already_running`` non è un fallimento e non è «niente da fare»: è
        «lo sta già facendo qualcuno». Se si raccontasse come uno dei due, un
        utente che rilancia ``/gardener`` due volte crederebbe di aver rotto
        qualcosa, o di aver ottenuto due passate."""
        text = _format_gardener_outcome(
            "viaggio", GardenerOutcome(status="already_running")
        )

        assert "The gardener failed" not in text
        assert "already running" in text
        assert "one pass at a time" in text


class TestAMapPassSaysWhatItMoved:
    """Una passata girata **per la mappa** (T3.5) non ha righe di diario, e ogni
    frase che le conta diventa falsa: «read 0 journal lines and judged that none of
    them earned a page» è un comando che ha smesso di dire la verità.

    E il freno va detto qui, non nei log di un telefono: un utente che rilancia il
    comando e non vede partire niente deve poter sapere perché.
    """

    def _line(self, **kw) -> str:
        return _format_gardener_outcome(
            "viaggio", GardenerOutcome(map_pass=True, elapsed=12.5, **kw)
        )

    def test_it_does_not_report_zero_journal_lines(self):
        text = self._line(status="nothing_to_promote", map_before=9000, map_after=9000)

        assert "0 journal lines" not in text
        assert "the map alone" in text

    def test_a_map_brought_under_the_ceiling_says_so_with_both_numbers(self):
        text = self._line(status="written", writes=4, map_before=9000, map_after=1500)

        assert "9000" in text and "1500" in text
        assert "fits now" in text

    def test_a_map_still_over_the_ceiling_says_it_will_not_be_retried(self):
        text = self._line(status="written", writes=4, map_before=9000, map_after=5000)

        assert "still over" in text
        assert "until the map grows" in text

    def test_a_map_nothing_was_moved_out_of_says_that_too(self):
        text = self._line(status="no_write", map_before=9000, map_after=9000)

        assert "nothing was moved out of it" in text
        assert "until the map grows" in text

    def test_a_provider_that_fell_over_keeps_its_own_sentence(self):
        """Il ramo della mappa non si mangia i due esiti che parlano d'altro: su
        ``failed`` la misura «dopo» non esiste, e raccontarla come «la mappa è
        ancora 0 caratteri» sarebbe peggio del silenzio."""
        text = self._line(status="failed", detail="provider is down", map_before=9000)

        assert "The gardener failed" in text and "provider is down" in text
