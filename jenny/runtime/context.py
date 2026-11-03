"""RuntimeContext — unica fonte di verità dello stato di runtime del gateway.

Sostituisce i global mutabili sparsi (`_ANDROID_CONTEXT`, `_WORKSPACE_DIR`,
`_current_config_path`) con un unico holder a proprietà esplicita. In produzione
è popolato write-once dal boot (``android_entry``/``GatewayContainer``); nei test
è riconfigurabile via i setter accessor.

Modulo leaf: importa solo stdlib → nessun ciclo. Gli accessor storici
(`get_workspace_path`, `get_android_context`, `get_config_path`,
`set_workspace_dir`, `set_android_context`) restano ai loro moduli e delegano
qui, così i ~20 call-site e i test non cambiano firma.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RuntimeContext:
    """Stato di runtime posseduto dal composition root."""

    workspace_dir: Path | None = None
    android_context: Any = None
    config_path: Path | None = None
    # IANA ID rilevato dal dispositivo (es. "Europe/Rome"), scritto una volta
    # al boot da ``android_entry``; None su host o se il rilevamento fallisce.
    device_timezone: str | None = None
    # Impostato da ``config.loader`` quando `config.json` era illeggibile e si è
    # dovuto ripiegare: "backup" (recuperato dal .bak) o "defaults" (ripartiti
    # da zero). La WebUI lo mostra: ripartire con impostazioni diverse da quelle
    # scelte dall'utente senza dirglielo sarebbe la sorpresa peggiore.
    config_recovered_from: str | None = None
    config_quarantine_path: Path | None = None


_CONTEXT = RuntimeContext()


def get_runtime_context() -> RuntimeContext:
    """Return the process-wide runtime context (single source of truth)."""
    return _CONTEXT


def get_android_context() -> Any:
    """Return the stored Android Context, if any (delegates to the runtime context)."""
    return _CONTEXT.android_context
