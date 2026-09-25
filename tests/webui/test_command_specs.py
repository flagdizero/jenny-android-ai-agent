"""La tabella dei comandi e l'alfabeto con cui la WebUI la disegna.

``BUILTIN_COMMAND_SPECS`` porta un'icona per comando da sempre, ma fino alla
0.9.0 nessuno la disegnava: ``as_dict()`` non aveva un consumatore in tutto il
repo. Con la tendina del composer quel campo diventa visibile, e si e' scoperto
che cinque nomi su tredici erano di **Lucide** (``square-pen``, ``sprout``,
``brush-cleaning``, ``file-pen``, ``circle-help``) mentre il font impacchettato e'
**Tabler**: in interfaccia sarebbero stati cinque quadrati vuoti.

Un nome sbagliato non rompe niente e non logga niente — e' esattamente il tipo di
difetto che sopravvive a una review e si vede solo sul telefono. Questo file e'
il posto dove si nota: confronta la tabella con il CSS del font vero, quello che
finisce nell'APK.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.gateway_http import make_handler

from jenny.command.specs import BUILTIN_COMMAND_SPECS, SCOPES

_UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
_TABLER_CSS = (
    _UI / "assets" / "vendor" / "@tabler" / "icons-webfont@3.19.0" / "dist" / "tabler-icons.min.css"
)


def _tabler_icon_names() -> set[str]:
    css = _TABLER_CSS.read_text(encoding="utf-8")
    # Le classi del webfont sono `.ti-<name>:before{content:"\\xxxx"}`.
    return set(re.findall(r"\.ti-([a-z0-9-]+):before", css))


def test_every_command_icon_exists_in_the_bundled_font() -> None:
    available = _tabler_icon_names()
    assert available, "il CSS del font Tabler non e' stato letto: path cambiato?"

    missing = {
        spec.command: spec.icon
        for spec in BUILTIN_COMMAND_SPECS
        if spec.icon not in available
    }

    assert not missing, (
        f"icone non presenti nel font Tabler impacchettato: {missing}. "
        "I nomi vanno presi da Tabler (senza il prefisso 'ti-'), non da Lucide: "
        "un nome inesistente rende un quadrato vuoto, in silenzio."
    )


def test_specs_are_serializable_for_the_webui() -> None:
    """La rotta ``/api/webui/commands`` serve esattamente questo dizionario."""
    for spec in BUILTIN_COMMAND_SPECS:
        payload = spec.as_dict()
        assert payload["command"].startswith("/")
        assert payload["title"]
        assert payload["description"]
        assert payload["icon"]
        # Il dispatch decide su questo valore: uno sconosciuto passerebbe come
        # ``any``, cioe' aprirebbe il comando in ogni scope in silenzio.
        assert payload["scope"] in SCOPES


def test_project_only_commands_are_the_ones_whose_subject_is_this_project() -> None:
    """``/tidy``, ``/init`` e ``/gardener``, e nessun altro.

    Il criterio e' il **soggetto**, non il meccanismo: fuori da un progetto
    questi tre non hanno su cosa agire, e offrirli comunque vorrebbe dire
    proporre voci che non fanno niente.

    ``/gardener`` e' entrato il 31/08/2026, quando ha smesso di accettare il nome
    di un progetto: prendeva il bersaglio da un argomento — il telecomando dalla
    chat personale, che il layer dei tool non ha mai avuto (``journal_append``
    fuori da un progetto rifiuta e non ha un argomento con cui aggirarsi) — e con
    quello via il suo soggetto e' la conversazione in cui si e'.
    """
    project_only = {spec.command for spec in BUILTIN_COMMAND_SPECS if spec.scope == "project"}
    assert project_only == {"/tidy", "/init", "/gardener"}


def test_the_two_that_expand_in_the_turn_do_not_pass_through_the_router() -> None:
    """Invariante diversa dalla precedente, e va tenuta separata.

    ``/tidy`` e ``/init`` non sono comandi del router: si espandono in un turno
    normale (``agent/loop.py::PROJECT_INIT_COMMAND``), quindi la tendina e' la
    loro sola superficie di scoperta. ``/gardener`` invece e' un comando vero e
    proprio con lo stesso scope: confondere le due cose e' come l'invariante
    precedente e' diventata falsa.
    """
    from jenny.agent.loop import PROJECT_INIT_COMMAND, PROJECT_TIDY_COMMAND
    from jenny.command.builtin import register_builtin_commands
    from jenny.command.router import CommandRouter

    router = CommandRouter()
    register_builtin_commands(router)

    assert {PROJECT_INIT_COMMAND, PROJECT_TIDY_COMMAND} == {"/init", "/tidy"}
    assert not router.is_dispatchable_command(PROJECT_INIT_COMMAND)
    assert not router.is_dispatchable_command(PROJECT_TIDY_COMMAND)
    assert router.is_dispatchable_command("/gardener")


# ── La rotta che alimenta la tendina ────────────────────────────────────────


def _commands_route_response(session_key: str | None):
    """La rotta vera, con le dipendenze minime che tocca."""
    import json
    import urllib.parse

    from websockets.http11 import Headers
    from websockets.http11 import Request as WsRequest

    secret = "test-secret"
    handler = make_handler(Path("/tmp/skills-does-not-matter"))
    path = "/api/webui/commands"
    if session_key is not None:
        path = f"{path}?key={urllib.parse.quote(session_key)}"
    sep = "&" if "?" in path else "?"
    request = WsRequest(path=f"{path}{sep}token={secret}", headers=Headers())
    response = handler._handle_webui_commands(request)
    # Un rifiuto ha un corpo di testo, non JSON (``http_utils.http_error``).
    payload = json.loads(response.body) if response.status_code == 200 else None
    return response, payload


def test_the_route_serves_the_commands_of_that_conversation() -> None:
    """Il filtro sta qui e non nel client (31/08/2026).

    La tendina teneva due righe di ``if (spec.scope === 'project' && !inProject)``,
    cioe' una seconda copia della regola; e un filtro lato client e' comunque solo
    cosmetica, perche' non c'e' autocomplete sullo ``/``.
    """
    _, personal = _commands_route_response("websocket:default")
    _, project = _commands_route_response("project:patreon")

    names = lambda payload: {row["command"] for row in payload["commands"]}  # noqa: E731

    assert "/dream" in names(personal) and "/tidy" not in names(personal)
    assert "/tidy" in names(project) and "/dream" not in names(project)


def test_the_route_without_a_key_serves_everything() -> None:
    """Senza ``key`` e' "scope non noto", non "chat personale": un client vecchio
    continua a vedere quel che vedeva, e il dispatch lo fermerebbe comunque."""
    _, payload = _commands_route_response(None)

    assert {row["command"] for row in payload["commands"]} == {
        spec.command for spec in BUILTIN_COMMAND_SPECS
    }


def test_the_route_refuses_a_key_that_is_not_one() -> None:
    response, _ = _commands_route_response("../../etc/passwd")

    assert response.status_code == 400
