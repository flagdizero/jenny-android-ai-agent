"""Gateway entry point for jenny."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.config.bootstrap import ensure_minimal_config

# Re-exported for backward compatibility; canonical definition lives in
# ``jenny.runtime.context`` (leaf module, no dependency on this entry-point).
from jenny.runtime.context import get_android_context as get_android_context

MAX_RETRIES = 3
RETRY_DELAY_S = 5


def set_android_context(context: Any) -> None:
    """Store the Android Context passed from Kotlin/Chaquopy.

    This is used by Android-only tools (e.g. the WebView-backed web tools)
    to instantiate native Android objects such as a hidden WebView. Lo stato
    vive nel ``RuntimeContext`` (unica fonte di verità).
    """
    from jenny.runtime.context import get_runtime_context

    get_runtime_context().android_context = context


def run_gateway(
    data_dir: str,
    android_context: Any = None,
    *,
    host: str = "127.0.0.1",
    port: int = 18790,
) -> None:
    """Start the jenny gateway.

    This is the single entry point for the Android runtime (called from
    Java/Kotlin via Chaquopy). The same function can be invoked manually for
    local testing, but the execution path is identical to the Android runtime.
    The WebSocket and HTTP surfaces share the same port so the WebView can
    reach both from one origin.

    Args:
        data_dir: Runtime data directory. The workspace is created at
            ``<data_dir>/workspace``.
        android_context: Optional Android Context object passed from Kotlin.
            When provided, Android-only tools can use native Android APIs.

    Raises:
        Exception: If the gateway fails to start after all retries.
    """
    if android_context is not None:
        set_android_context(android_context)

    # Rileva la timezone del device (best-effort) prima di ogni load_config:
    # il loader la usa come default quando la config non ne fissa una.
    try:
        from jenny.runtime.context import get_runtime_context
        from jenny.utils.device_timezone import detect_device_timezone
        from jenny.utils.helpers import tzdata_available

        device_tz = detect_device_timezone()
        get_runtime_context().device_timezone = device_tz
        logger.info(
            "Device timezone: {} (tzdata available: {})",
            device_tz or "unknown",
            tzdata_available(),
        )
    except Exception:
        logger.opt(exception=True).debug("Could not detect device timezone")

    # Capture logs in-memory so the get_recent_logs tool can surface them
    # without adb/logcat access.
    try:
        from jenny.agent.tools.diagnostics import install_log_buffer

        install_log_buffer()
    except Exception:
        # Non-fatale: la cattura log in-memory è best-effort (il tool
        # get_recent_logs resta degradato). Logghiamo invece di ingoiare muto.
        logger.opt(exception=True).debug("Could not install in-memory log buffer")

    # Reset Android-only bridge state so a fresh gateway start cannot inherit
    # a stale bridge or locked asyncio state from a previous crashed loop.
    # Tutti i bridge (web-search + installed-apps + notifier + location + power
    # + ssh + updater) vengono resettati qui, simmetricamente, insieme alle
    # altre primitive asyncio tenute in globali di modulo (config store, lock
    # del controllo aggiornamenti, registro dei job SSH).
    try:
        from jenny.agent.tools.android_web import reset_android_web_state
        from jenny.agent.tools.browser import reset_browser_state
        from jenny.agent.tools.ssh_jobs import reset_job_store
        from jenny.agent.tools.ssh_transport import reset_ssh_backend
        from jenny.config.store import reset_config_store_state
        from jenny.runtime.floating import reset_floating_state
        from jenny.runtime.location import reset_location_state
        from jenny.runtime.native_input import reset_native_input
        from jenny.runtime.notifier import reset_notifier_state
        from jenny.runtime.power import reset_power_state
        from jenny.runtime.update_install import reset_install_state
        from jenny.webui.android_apps_api import reset_installed_apps_state
        from jenny.webui.settings_api import reset_update_check_state

        reset_android_web_state()
        reset_browser_state()
        reset_installed_apps_state()
        reset_notifier_state()
        reset_native_input()
        # Gemello di reset_notifier_state: il FloatingBridge cachato punta al
        # contesto del giro precedente, e il lock dentro BridgeCache è legato a
        # un loop morto.
        reset_floating_state()
        reset_location_state()
        # L'updater tiene una fase *sticky* e un ``UpdateBridge`` in cache: senza
        # questo reset un gateway che riparte nello stesso processo mostrerebbe
        # la fase del run precedente (e rifiuterebbe di installare, credendo di
        # aver già committato) parlando per giunta a un context ormai morto.
        reset_install_state()
        # Il power manager tiene refcount e wakelock: ereditare la contabilità
        # di un loop morto farebbe credere di tenere un lock che non c'è più.
        reset_power_state()
        # Il backend SSH tiene il pool di sessioni: ereditarlo da un loop morto
        # lascerebbe connessioni legate a un event loop che non esiste più.
        reset_ssh_backend()
        # Il registro dei job SSH è un singleton di modulo il cui lock resta
        # preso *durante* l'exec remoto: due poll concorrenti si accodano
        # davvero, e questo lega il lock al loop. Senza reset, dopo un restart
        # in-process ogni operazione sui job SSH morirebbe con "bound to a
        # different event loop"; lo stato vero sta su file, qui si scorda solo
        # la cache.
        reset_job_store()
        # Il lock delle scritture di config.json vive in una globale di modulo
        # e tutte le ~16 scritture ci passano: se resta legato al loop
        # precedente, config.json diventa di sola lettura per il resto della
        # vita del processo.
        reset_config_store_state()
        # Il controllo aggiornamenti tiene il suo lock attraverso la rete: se
        # il loop muore lì in mezzo, la guardia ``locked()`` risponde ``busy``
        # per sempre e il bottone resta morto.
        reset_update_check_state()
    except Exception:
        # Non-fatale: al peggio si eredita un bridge stale (verrà ricreato).
        logger.opt(exception=True).debug("Could not reset Android bridge state")

    data_path = Path(data_dir)
    workspace_path = data_path / "workspace"
    workspace_dir = str(workspace_path)

    # Applica un eventuale ripristino pendente (backup/snapshot) PRIMA che
    # qualunque componente tocchi il workspace: lo swap atomico deve avvenire
    # a workspace freddo. Mai solleva; nel dubbio lascia il workspace attuale.
    from jenny.snapshot.restore_marker import apply_pending_restore

    apply_pending_restore(data_path)

    # Ensure workspace directory exists
    try:
        workspace_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Cannot create workspace directory {}: {}", workspace_dir, exc)
        raise

    from jenny.config.paths import set_workspace_dir
    from jenny.gateway_runtime import _run_gateway
    from jenny.utils.helpers import sync_workspace_templates

    # Set global workspace dir for path resolution
    set_workspace_dir(workspace_dir)

    # Sync templates, skills, UI assets from package to writable storage
    try:
        sync_workspace_templates(workspace_path)
    except Exception:
        logger.opt(exception=True).warning(
            "Failed to extract package assets to {} — gateway may lack WebUI or prompts",
            workspace_dir,
        )
        # Continue anyway — API-based interactions still work

    # Extract jenny's readable .py sources (bundled as APK assets) so the
    # agent can inspect its own code via file tools / get_source on-device.
    if android_context is not None:
        try:
            from jenny.utils.android_assets import extract_jenny_source

            extract_jenny_source(data_path / "jenny_src")
        except Exception:
            logger.opt(exception=True).debug("Could not extract jenny source assets")

    # Ensure a minimal config exists (idempotent)
    try:
        ensure_minimal_config(workspace_path)
    except Exception:
        logger.opt(exception=True).warning(
            "Could not ensure default config — relying on existing config or defaults"
        )

    # Run the gateway with retry loop
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            asyncio.run(
                _run_gateway(
                    config=None,
                    host=host,
                    port=port,
                    ws_port=port,
                )
            )
            return  # clean exit
        except KeyboardInterrupt:
            # Unico caso non ritentabile: è un'interruzione voluta. Su Android
            # non viene mai generata (l'interrupt di python_exec usa la sua
            # PythonExecInterrupted via PyThreadState_SetAsyncExc, mai
            # KeyboardInterrupt, e GatewayContainer.run la assorbe già per lo
            # shutdown pulito); qui resta solo il Ctrl-C dell'esecuzione
            # manuale, che non va combattuto con tre restart.
            logger.info("Gateway interrupted, not restarting")
            raise
        except BaseException as exc:
            # BaseException e non Exception: SystemExit — sollevata dal codice
            # dell'agente dentro python_exec — non è una Exception, quindi con
            # `except Exception` i retry venivano saltati e run_gateway tornava
            # a Kotlin lasciando il servizio senza agente dietro (vedi B1/B2).
            logger.opt(exception=True).error(
                "Gateway crashed (attempt {}/{}): {}: {}",
                attempt,
                MAX_RETRIES,
                type(exc).__name__,
                exc,
            )
            if attempt < MAX_RETRIES:
                logger.info("Restarting in {} seconds...", RETRY_DELAY_S)
                time.sleep(RETRY_DELAY_S)
            else:
                logger.error("Gateway failed after {} attempts, giving up", MAX_RETRIES)
                raise
