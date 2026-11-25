"""I comandi ``network.*``: la «Rete di casa» dell'officina, e l'interruttore del web.

Scritture, quindi comandi del WebSocket e non route ``/api/``
(``CONTRIBUTING.md``). Le regole stanno in ``home_network.py``; qui la porta,
la scrittura con ``store.mutate`` e il blocco SSRF riallineato al processo.

**Solo dal telefono**, come le mani di Jenny (``phone_commands.py``): chi e'
collegato da lontano non apre la rete di casa agli strumenti.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jenny.webui.commands import CommandContext, CommandError


def _refuse_remote() -> None:
    from jenny.channels.ws_remote import on_remote_listener

    if on_remote_listener():
        raise CommandError("phone_only", "this is changed from the phone itself")


async def _write(apply) -> dict[str, Any]:
    from jenny.config.store import mutate
    from jenny.security.network import configure_ssrf_whitelist
    from jenny.webui.home_network import HomeNetworkError, home_network_payload

    try:
        config = await mutate(apply)
    except HomeNetworkError as exc:
        raise CommandError("bad_request", exc.code) from exc
    configure_ssrf_whitelist(list(config.security.ssrf_whitelist or []))
    return {"ok": True, "home_network": home_network_payload(config)}


async def network_add(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Aggiunge un indirizzo di casa (``address``), con un nome (``name``)."""
    from jenny.webui.home_network import HomeNetworkError, add_entry, clean_name, parse_home_address

    _refuse_remote()
    try:
        entry = parse_home_address(params.get("address"))
    except HomeNetworkError as exc:
        raise CommandError("bad_request", exc.code) from exc
    name = clean_name(params.get("name"))
    return await _write(lambda config: add_entry(config, entry, name))


async def network_remove(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Toglie una voce, com'e' scritta nell'elenco (``entry``): anche un intervallo
    scritto a mano, che da qui non si crea."""
    from jenny.webui.home_network import remove_entry

    _refuse_remote()
    entry = params.get("entry")
    if not isinstance(entry, str) or not entry or len(entry) > 64:
        raise CommandError("bad_request", "entry required")
    return await _write(lambda config: remove_entry(config, entry))


async def network_web_access(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Accende o spegne il web di Jenny (``enabled``): ``tools.androidWeb.enable``.

    Un interruttore solo per ``web_search``, ``web_fetch`` e i ``browser_*``
    (``AndroidWebGateMixin``). Fino al 25/11/2026 viveva solo in ``config.json``.

    **Vale dal prossimo avvio per la chat**: gli strumenti si registrano
    all'avvio e la lista di una chat non cambia da un turno all'altro (la cache
    del provider comincia da lì, v. ``AgentLoop._turn_tool_view``). Gli aiutanti
    nuovi invece rileggono la config viva (``subagent_toolbox``) e lo seguono
    subito. ``requires_restart`` dice alla casa se c'è stato un cambio.

    Non è «solo dal telefono» come la rete di casa: spegnere o riaccendere il web
    non apre niente che il gateway non apra già di suo.
    """
    from jenny.config.store import mutate
    from jenny.webui.settings_api import settings_payload

    enabled = params.get("enabled")
    if not isinstance(enabled, bool):
        raise CommandError("bad_request", "enabled must be true or false")
    changed = False

    def _apply(config: Any) -> bool:
        nonlocal changed
        web = config.tools.android_web
        if web.enable == enabled:
            return False
        web.enable = enabled
        changed = True
        return True

    await mutate(_apply)
    return {"ok": True, "web_search": settings_payload()["web_search"],
            "requires_restart": changed}


NETWORK_COMMANDS = {
    "network.add": network_add,
    "network.remove": network_remove,
    "network.web_access": network_web_access,
}
