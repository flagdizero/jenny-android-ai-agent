"""Tool di aggiornamento dell'app Jenny (solo Android).

Affaccio LLM sopra :mod:`jenny.runtime.update_check` (che cosa c'è di nuovo) e
:mod:`jenny.runtime.update_install` (scaricalo e installalo). Qui non c'è
logica: solo la traduzione in JSON e le guardie che rendono difficile
installare per sbaglio.

**Perché due tool e non uno con un parametro ``action``.** Il primo motivo è la
proprietà ``read_only``, che è per-tool e non per-chiamata: ``update_status``
non ha effetti, può girare in parallelo e va bene chiamarlo per curiosità,
mentre ``install_update`` uccide il processo. Un tool solo dovrebbe dichiararsi
non-read-only anche per la lettura, e nessuna delle due semantiche resterebbe
vera. Il secondo è la distanza: con un ``action`` enum "installa" è un token di
distanza da "stato", mentre un nome diverso, una descrizione diversa e un
``confirm`` obbligatorio sono tre cose che devono andare storte insieme. Il
costo — un tool in più nella grammatica — è due schemi minuscoli, uno vuoto e
uno con un booleano.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import BooleanSchema, tool_parameters_schema
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
)

_ANDROID_ONLY = "The in-app updater only exists in the Android app."


class _AppUpdateTool(Tool):
    """Base comune: entrambi i tool esistono solo dentro l'app Android.

    Fuori da Android il layer di runtime degrada già in un errore leggibile, ma
    un tool che c'è e risponde sempre "non qui" è peggio di un tool che non c'è:
    occupa spazio nel prompt e invita a provarci.
    """

    # Mai ai subagent: sostituire l'APK è una decisione dell'utente presa nella
    # sua conversazione, non un passo che un task delegato possa compiere.
    _scopes = {"core", "orchestrator"}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return bool(getattr(ctx, "android_context", None))

    @classmethod
    def disabled_reason(cls, ctx: Any) -> str | None:
        return _ANDROID_ONLY


@tool_parameters(tool_parameters_schema(required=[]))
class UpdateStatusTool(_AppUpdateTool):
    """Che versione c'è di nuovo e a che punto è un'eventuale installazione."""

    name = "update_status"
    description = (
        "Report whether a newer version of the Jenny app is available for this "
        "device, and how a started installation is progressing. Reads local "
        "state only: no network call, no side effects, safe to call whenever "
        "the user asks 'is there an update?' or 'is it installing?'. The "
        "available version comes from the last periodic update check, so it can "
        "be up to a day old. Call this before install_update."
    )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.update_check import cached_update, installed_version_code
        from jenny.runtime.update_install import install_status

        info = cached_update()
        payload: dict[str, Any] = {
            "installedVersionCode": installed_version_code(),
            "install": install_status(),
        }
        if info is None:
            payload["updateAvailable"] = False
            payload["update"] = None
        else:
            payload["updateAvailable"] = True
            payload["update"] = {
                "versionName": info.version_name,
                "versionCode": info.version_code,
                "summary": info.summary,
                "sizeBytes": info.size,
                "critical": info.critical,
                "notesUrl": info.notes_url or None,
            }
        return json.dumps(payload, ensure_ascii=False)


@tool_parameters(
    tool_parameters_schema(
        confirm=BooleanSchema(
            description=(
                "Must be true. Set it only after the user has asked, in this "
                "conversation, for the update to be installed now."
            ),
        ),
        required=["confirm"],
    )
)
class InstallUpdateTool(_AppUpdateTool):
    """Scarica e installa l'aggiornamento in sospeso. Riavvia l'app."""

    name = "install_update"
    description = (
        "Download and install the pending Jenny app update on this device. "
        "DESTRUCTIVE AND FINAL: Android replaces the app and kills the process, "
        "so Jenny restarts and the conversation is cut off mid-turn — you will "
        "not get to reply after calling this, and anything you still owe the "
        "user must be said before. Call it ONLY when the user has explicitly "
        "asked, in this conversation and just now, to install the update; never "
        "on your own initiative, and never just because an update was "
        "mentioned. Check update_status first. The download can take several "
        "minutes over mobile data. If Android refuses an unattended install, "
        "the system installer is shown instead (or posted as a notification "
        "when the screen is off) and the user has to tap Install to finish."
    )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def exclusive(self) -> bool:
        # Non ha senso far girare altro accanto a un tool che sta per far
        # sparire il processo sotto i piedi degli altri.
        return True

    def outside_undo(self, params: dict[str, Any]) -> dict[str, str] | None:
        """Un aggiornamento installato non si toglie da qui."""
        return {"what": "update", "label": "Jenny"} if params.get("confirm") else None

    async def execute(self, confirm: bool = False, **kwargs: Any) -> str:
        from jenny.runtime.update_install import start_install

        # Il piu' irreversibile di tutti: sostituisce l'app sotto i piedi del
        # processo. Se l'interruttore e' giu', non parte nemmeno con `confirm`.
        if current_turn_is_readonly():
            return json.dumps({"ok": False, "state": "error", "detail": READONLY_TOOL_REFUSAL})

        if not bool(kwargs.pop("confirm", confirm)):
            return json.dumps(
                {
                    "ok": False,
                    "state": "error",
                    "detail": (
                        "Refused: confirm must be true, and only after the user "
                        "explicitly asked for the update to be installed."
                    ),
                },
                ensure_ascii=False,
            )

        result = await start_install()
        return json.dumps(
            {"ok": result.ok, "state": result.state, "detail": result.detail},
            ensure_ascii=False,
        )


# Registrazione esplicita dei tool di questo modulo (letta da loader.py).
TOOLS = [UpdateStatusTool, InstallUpdateTool]
