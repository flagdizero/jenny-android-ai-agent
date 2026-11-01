"""Dove vale un comando, e chi lo fa rispettare.

La domanda «questo comando ha senso qui?» aveva tre risposte in tre punti: un
cancello nel loop per ``/tidy`` e ``/init``, una frase a mano dentro
``cmd_gardener``, e **niente** per ``/dream``, ``/model`` e
``/skill``, che dentro un progetto partivano. Il filtro della tendina esisteva ma
era lato client, e senza autocomplete sullo ``/`` un filtro nel client nasconde
una voce a chi guarda il menu e non dice niente a chi digita.

Qui si tiene chiuso che la regola sia **una**, che stia nel dispatch, e che il
rifiuto dica *dove*.
"""

from __future__ import annotations

import pytest

from jenny.bus.events import InboundMessage
from jenny.command.builtin import build_help_text, register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.command.scope import available, refusal, spec_for_line, visible_specs
from jenny.command.specs import BUILTIN_COMMAND_SPECS
from jenny.session.keys import UNIFIED_SESSION_KEY

_PROJECT = "project:palestra"


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


def _ctx(raw: str, key: str) -> CommandContext:
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="default", content=raw)
    return CommandContext(msg=msg, session=None, key=key, raw=raw, loop=None)


def _spec(command: str):
    spec = spec_for_line(command)
    assert spec is not None, f"{command} non e' nella tabella"
    return spec


# ── La regola ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["/new", "/stop", "/status", "/history", "/goal", "/help"])
def test_a_command_about_this_conversation_works_in_both(command: str) -> None:
    assert available(_spec(command), UNIFIED_SESSION_KEY)
    assert available(_spec(command), _PROJECT)


@pytest.mark.parametrize("command", ["/dream", "/model", "/skill"])
def test_a_command_about_the_person_or_the_install_stops_at_a_project(command: str) -> None:
    """*Chi sei viaggia, dove altro lavori no*: la riga di confine dei prompt.

    ``/dream`` consolida ``MEMORY.md``, che una sessione di progetto per
    costruzione non alimenta; ``/model`` e
    ``/skill`` sono stato dell'installazione.
    """
    assert available(_spec(command), UNIFIED_SESSION_KEY)
    assert not available(_spec(command), _PROJECT)


@pytest.mark.parametrize("command", ["/gardener", "/tidy", "/init"])
def test_a_command_about_this_project_needs_one(command: str) -> None:
    assert available(_spec(command), _PROJECT)
    assert not available(_spec(command), UNIFIED_SESSION_KEY)


def test_an_internal_key_is_not_refused_anything() -> None:
    """Il residuo cade su «disponibile», e non e' distrazione.

    Una chiave che non e' progetto ne' personale e' interna (cron,
    Dream): rifiutare li' trasformerebbe una domanda di classificazione in un
    job che non gira.
    """
    for spec in BUILTIN_COMMAND_SPECS:
        assert available(spec, "cron:job-42") or spec.scope == "project"


def test_the_two_scopes_are_not_one_inside_the_other() -> None:
    """Entrare in un progetto **toglie** voci, non solo ne aggiunge.

    Era il difetto: la superficie di progetto era un sovrainsieme di quella
    personale — dentro `wikis/foo` venivano offerti anche i comandi della memoria
    personale, che li' non hanno soggetto.
    """
    personal = {spec.command for spec in visible_specs(UNIFIED_SESSION_KEY)}
    project = {spec.command for spec in visible_specs(_PROJECT)}

    assert personal - project == {"/dream", "/model", "/skill"}
    assert project - personal == {"/gardener", "/tidy", "/init"}


def test_an_unknown_scope_is_not_reachable_from_the_table() -> None:
    for spec in BUILTIN_COMMAND_SPECS:
        assert spec.scope in ("any", "personal", "project")


def test_visible_specs_without_a_key_means_i_do_not_know() -> None:
    """``None`` non e' «chat personale»: e' «non lo so», e allora si elenca tutto."""
    assert visible_specs(None) == BUILTIN_COMMAND_SPECS


@pytest.mark.parametrize("line", ["/dream", "/dream budget", "/DREAM", "  /dream  "])
def test_the_spec_of_a_line_ignores_the_arguments(line: str) -> None:
    assert spec_for_line(line) is _spec("/dream")


@pytest.mark.parametrize("line", ["/dreamy", "/newx", "dream", "", "/", "hello /dream"])
def test_a_line_that_does_not_name_a_command_has_no_spec(line: str) -> None:
    """Match sulla parola e non sul prefisso: ``/newx`` non e' ``/new``."""
    assert spec_for_line(line) is None


# ── Il rifiuto ──────────────────────────────────────────────────────────────


def test_a_project_command_outside_says_how_to_get_in() -> None:
    text = refusal(_spec("/tidy"))

    assert "not a project" in text
    assert "chip above the composer" in text
    assert "/tidy" in text


def test_a_personal_command_inside_a_project_says_where_to_send_it() -> None:
    text = refusal(_spec("/dream"))

    assert "personal chat" in text
    assert "chip above the composer" in text


def test_the_refusal_carries_the_note_of_that_command() -> None:
    """La riga che dice *cosa* fa il comando: senza, «qui no» resta senza perche'."""
    assert "personal memory" in refusal(_spec("/dream"))
    assert "Settings" in refusal(_spec("/gardener"))


# ── Il cancello nel dispatch ────────────────────────────────────────────────


async def test_a_personal_command_in_a_project_is_refused_not_run(router) -> None:
    out = await router.dispatch(_ctx("/dream", _PROJECT))

    assert out is not None
    assert "personal chat" in out.content


async def test_a_project_command_outside_is_refused_not_run(router) -> None:
    out = await router.dispatch(_ctx("/gardener", UNIFIED_SESSION_KEY))

    assert out is not None
    assert "not a project" in out.content


async def test_the_prefix_tier_is_gated_before_the_arguments_are_prepared(router) -> None:
    """Un rifiuto non deve lasciare il contesto mezzo preparato per un handler
    che non verra' chiamato."""
    ctx = _ctx("/dream budget", _PROJECT)

    out = await router.dispatch(ctx)

    assert out is not None and "personal chat" in out.content
    assert ctx.args == ""


async def test_the_priority_tier_is_gated_too() -> None:
    """Oggi ``/stop`` e ``/status`` sono ``any``, quindi la' il cancello e' un
    no-op — ma un comando prioritario nuovo con uno scope non deve scoprirsi
    scoperto, e questo e' l'unico posto in cui quel cablaggio si vede."""
    router = CommandRouter()

    async def _never(_ctx):
        raise AssertionError("il cancello doveva fermarlo")

    router.priority("/probe", _never)
    router.availability = lambda ctx: _reply_stub("no")

    out = await router.dispatch_priority(_ctx("/probe", _PROJECT))

    assert out is not None and out.content == "no"


def _reply_stub(text: str):
    from jenny.bus.events import OutboundMessage

    return OutboundMessage(channel="websocket", chat_id="default", content=text)


async def test_an_any_command_runs_in_a_project(router, monkeypatch) -> None:
    called: list[str] = []

    async def _fake(ctx):
        called.append(ctx.raw)
        return None

    router.exact("/probe", _fake)

    await router.dispatch(_ctx("/probe", _PROJECT))

    assert called == ["/probe"]


def test_a_command_outside_its_scope_is_still_dispatchable(router) -> None:
    """**Deve** essere intercettato: se non fosse un comando, la riga passerebbe
    al modello come messaggio — il modo peggiore di dire "qui no"."""
    assert router.is_dispatchable_command("/dream")
    assert router.is_dispatchable_command("/gardener")


# ── /help ───────────────────────────────────────────────────────────────────


def test_help_lists_what_this_conversation_can_do() -> None:
    inside = build_help_text(_PROJECT)
    outside = build_help_text(UNIFIED_SESSION_KEY)

    assert "/tidy" in inside and "/dream" not in inside
    assert "/dream" in outside and "/tidy" not in outside


def test_help_on_telegram_never_advertises_a_project_command() -> None:
    """Quel canale e' **sempre** la sessione personale (``session_key_for_channel``).

    Prima ``/help`` li' elencava ``/tidy`` e ``/init``, che su Telegram non
    possono funzionare mai: la tendina della WebUI filtrava, ``/help`` no.
    """
    from jenny.session.keys import session_key_for_channel

    key = session_key_for_channel("telegram", "project:palestra")
    text = build_help_text(key)

    assert "/tidy" not in text and "/init" not in text and "/gardener" not in text


def test_help_without_a_key_still_lists_everything() -> None:
    text = build_help_text()

    for spec in BUILTIN_COMMAND_SPECS:
        assert spec.command in text
