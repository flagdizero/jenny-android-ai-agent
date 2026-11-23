"""Le impostazioni di «Usa il telefono» (``tools.phone``) per la stanza di Jenny.

Tre cose, e sono i tre modi in cui l'utente la spegne o la tiene a bada:

* ``mode`` — ``off``, ``confirm`` (chiede prima di usare un'app), ``free``
  (non chiede più; i tasti che costano chiedono lo stesso);
* le app su «Sempre», con il nome che l'utente vede, da togliere una alla volta;
* lo stato del servizio di accessibilità «Jenny — usa il telefono», che si
  accende solo da Android: qui lo si mostra e ci si porta l'utente.
"""

from __future__ import annotations

from typing import Any

from jenny.channels.ws_remote import on_remote_listener
from jenny.config import store
from jenny.config.loader import load_config
from jenny.config.schema import Config
from jenny.runtime import phone as runtime

MODES = ("off", "confirm", "free")


async def phone_section(config: Config | None = None) -> dict[str, Any]:
    """La sezione ``phone`` del payload delle impostazioni."""
    phone = (config or load_config()).tools.phone
    service = await runtime.phone_state()
    from jenny.phone.consent import can_be_trusted

    trusted = []
    for target in phone.trusted_apps:
        # Una voce messa prima che «Sempre» valesse solo per app e siti (una
        # finestra di sistema, un link ``market://``) non conta più: non si
        # mostra, e il primo «Rimuovi» su un'altra non la tocca.
        if not can_be_trusted(target):
            continue
        label = target[5:] if target.startswith("link:") else await runtime.app_label(target)
        trusted.append({"target": target, "label": label})
    return {
        # Fuori da Android non c'è un telefono da usare, e da lontano non lo
        # si accende: la sezione non compare.
        "available": service != "unavailable" and not on_remote_listener(),
        "mode": phone.mode,
        # «on», «off» (servizio spento in Accessibilità) o «unsupported».
        "service": service,
        "trusted": trusted,
    }


async def update_phone(*, mode: str | None = None, forget: str | None = None) -> dict[str, Any]:
    """Cambia ``mode`` e/o toglie un'app da «Sempre»; torna la sezione nuova.

    Chi chiama ha già controllato i valori (``phone_commands.phone_update``).
    """

    def _apply(config: Config) -> bool:
        phone = config.tools.phone
        changed = False
        if mode is not None and phone.mode != mode:
            phone.mode = mode  # type: ignore[assignment]
            changed = True
        if forget and forget in phone.trusted_apps:
            phone.trusted_apps.remove(forget)
            changed = True
        return changed

    config = await store.mutate(_apply)
    return await phone_section(config)
