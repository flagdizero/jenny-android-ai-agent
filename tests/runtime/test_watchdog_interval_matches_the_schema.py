"""L'intervallo del watchdog ha tre copie, e devono dire la stessa cosa.

``Watchdog.kt`` tiene default e limiti suoi (``DEFAULT_INTERVAL_MIN``,
``MIN_INTERVAL_MIN``, ``MAX_INTERVAL_MIN``): valgono finché Python non ha spinto
la sua configurazione, cioè al primo avvio e dopo un wipe. Il commento lì dice
«allineati a ``PowerConfig``», e nessun test lo teneva vero. ``power.py``
ripiega sullo stesso default quando il valore non è utilizzabile.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.kotlin_source import read_code

from jenny.config.schema import PowerConfig

ROOT = Path(__file__).resolve().parents[2]


def _kotlin_int(code: str, name: str) -> int:
    m = re.search(rf"const val {name} = (\d+)\b", code)
    assert m, f"{name} non trovato in Watchdog.kt"
    return int(m.group(1))


def test_the_kotlin_defaults_are_the_schema_s() -> None:
    field = PowerConfig.model_fields["watchdog_interval_min"]
    code = read_code("Watchdog")
    assert _kotlin_int(code, "DEFAULT_INTERVAL_MIN") == field.default
    assert _kotlin_int(code, "MIN_INTERVAL_MIN") == field.ge
    assert _kotlin_int(code, "MAX_INTERVAL_MIN") == field.le


def test_the_python_fallback_is_the_schema_default() -> None:
    field = PowerConfig.model_fields["watchdog_interval_min"]
    source = (ROOT / "jenny" / "runtime" / "power.py").read_text(encoding="utf-8")
    fallbacks = re.findall(r'"watchdog_interval_min", (\d+)\)|interval_min = (\d+)\n', source)
    values = {int(a or b) for a, b in fallbacks}
    assert values == {field.default}, values
