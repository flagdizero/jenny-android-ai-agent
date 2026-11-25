"""I comandi ``network.*``: la «Rete di casa» dell'officina.

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


NETWORK_COMMANDS = {
    "network.add": network_add,
    "network.remove": network_remove,
}
