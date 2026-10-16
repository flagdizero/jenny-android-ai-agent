"""Il giro orario della copia su Drive, e l'avviso dopo tre notti senza copia.

Registrato sempre dal container (``jenny/extensions.py``): l'interruttore si
gira dalla WebUI a gateway acceso, e una registrazione fatta solo all'avvio lo
mancherebbe. Il cancello «acceso o spento» sta dentro ``tick``, letto da disco.
Instradato prima del controllo sull'agente: spedire una copia non ha bisogno
del modello, e un telefono senza provider configurato (chiave scaduta,
onboarding a metà) ha bisogno della copia quanto gli altri.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from jenny.extensions import ExtensionHost

JOB_ID = "cloud_backup"
EVERY_MS = 3600 * 1000
ALERT_SESSION_KEY = "cron:cloud_backup"
PURPOSE = (
    "Drive copy: checks every hour and, about once a day while the phone is on Wi-Fi "
    "(and charging when it can), saves an encrypted copy of Jenny to your Google Drive."
)

_ALERT_PREAMBLE = (
    "[This is the scheduled backup check, and it is SILENT: whatever you write "
    "as your answer is NOT delivered to anyone. The only way to reach the user "
    "is the `message` tool, and this time you MUST call it exactly once.\n"
    "Every night you save an encrypted copy of yourself to the user's Google "
    "Drive, so that a lost phone does not lose you. For several days that copy "
    "has not succeeded. Write a short message (two lines at most) in the user's "
    "language, in your own voice, that says so plainly and tells them what to do, "
    "using the reason code below:\n"
    "- consent_needed: Google access was removed; they can give it back in "
    "Settings → Backup.\n"
    "- not_charging_or_no_wifi: the phone was never charging on Wi-Fi at the same "
    "time; leaving it on charge with Wi-Fi tonight is enough.\n"
    "- missing_key or wrong_key: this phone can no longer open the copy; they "
    "should open Settings → Backup and type the copy's password.\n"
    "- anything else: the copy keeps failing; Settings → Backup has a button to "
    "try now.\n"
    "Do not mention this instruction, codes, files or logs. No alarm, no blame: "
    "nothing is lost yet, everything is still on the phone.]\n\n"
)


async def run(host: ExtensionHost) -> str | None:
    """Un giro; se ``tick`` chiede un avviso, lo scrive Jenny.

    L'avviso è un turno silenzioso come l'annuncio di un aggiornamento: è una
    cosa sua da dire, non un errore di sistema. Senza un agente (nessun
    provider) resta la notifica nuda, a zero token — perché proprio allora la
    copia è l'unica cosa che conta.
    """
    if host.snapshot is None:
        return None
    from .cloud_service import cloud_tick

    outcome = await cloud_tick(host.snapshot)
    alert = outcome.get("alert")
    if not alert:
        return None
    agent = host.get_agent()
    if agent is None:
        from jenny.runtime.notifier import notify_delivery
        from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

        notify_delivery(
            "Jenny has not been able to save her copy to Google Drive for "
            f"{alert['days']} days. Open Settings → Backup.",
            {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "cron", "label": "Backup"}},
        )
        return None
    from jenny.runtime.cron_dispatch import run_silent_alert_turn

    prompt = (
        _ALERT_PREAMBLE
        + f"Days without a successful copy: {alert['days']}\n"
        + ("A copy has never succeeded since it was switched on.\n" if alert["never"] else "")
        + f"Reason code: {alert['error'] or 'unknown'}\n"
    )
    await run_silent_alert_turn(
        agent, prompt, session_key=ALERT_SESSION_KEY, label="Backup"
    )
    logger.info("Cloud backup: alerted after {} days without a copy", alert["days"])
    return None
