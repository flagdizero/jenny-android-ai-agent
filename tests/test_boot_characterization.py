"""Fase 0.6 — Caratterizzazione: boot wiring & baseline del composition root.

Il wiring `run_gateway` → `_run_gateway` (kwargs esatti) è già fissato da
`test_gateway_entry.py::test_run_gateway_prepares_workspace_and_passes_overrides`.
Qui ancoriamo la BASELINE che Fase 6 modifica:
- il wiring reale vive tutto dentro `gateway_runtime._run_gateway` (closure
  annidate, inclusa la god-function `on_cron_job`);
- i moduli target del composition root NON esistono ancora.

La caratterizzazione profonda del dispatch `on_cron_job`
(dream/heartbeat/bound/unbound) arriva con Fase 6.4, quando l'estrazione in
`runtime/cron_dispatch.py` lo rende unit-testabile in isolamento.
"""

from __future__ import annotations

import importlib.util


def test_gateway_wiring_entrypoints_exist() -> None:
    from jenny import android_entry, gateway_runtime

    assert callable(android_entry.run_gateway)
    assert callable(gateway_runtime._run_gateway)


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def test_cron_dispatch_extracted_from_gateway() -> None:
    """Fase 6.4: il dispatch cron è estratto in ``runtime/cron_dispatch.py``
    (era la god-function ``on_cron_job`` dentro ``_run_gateway``)."""
    assert _module_present("jenny.runtime.cron_dispatch")
    from jenny.runtime.cron_dispatch import CronDispatcher

    assert hasattr(CronDispatcher, "dispatch")


def test_gateway_container_is_the_composition_root() -> None:
    """Fase 6.5: il wiring del gateway è estratto in
    ``runtime/container.py::GatewayContainer`` (ex closure/nonlocal di
    ``_run_gateway``). ``_run_gateway`` ora vi delega."""
    assert _module_present("jenny.runtime.container")
    from jenny.runtime.container import GatewayContainer

    for attr in ("build", "run", "set_agent"):
        assert hasattr(GatewayContainer, attr), attr
