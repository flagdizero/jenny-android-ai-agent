"""Le chiamate a loguru non usano le forme della ``logging`` di stdlib.

loguru ignora ``exc_info=True`` (lo prende per un argomento di formato e lo
butta), quindi la traccia sparisce senza un errore; e non formatta ``%s``: il
valore resta fuori dal messaggio. La forma giusta è ``logger.opt(exception=True)``
e le graffe ``{}``. I moduli che usano la ``logging`` di stdlib
(``logging.getLogger``) restano fuori dal controllo: lì quelle forme sono giuste.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LEVELS = {"trace", "debug", "info", "success", "warning", "error", "critical", "exception", "log"}


def _loguru_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            if node.func.attr in _LEVELS and (
                receiver.endswith("logger") or receiver.startswith("logger.")
            ):
                yield node


def test_no_stdlib_shapes_reach_loguru() -> None:
    wrong: list[str] = []
    for base in ("jenny", "jenny_extensions"):
        for path in sorted((ROOT / base).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "from loguru import logger" not in text or "logging.getLogger" in text:
                continue
            for call in _loguru_calls(path):
                where = f"{path.relative_to(ROOT)}:{call.lineno}"
                if any(k.arg == "exc_info" for k in call.keywords):
                    wrong.append(f"{where} exc_info=")
                fmt = call.args[0] if call.args else None
                if (isinstance(fmt, ast.Constant) and isinstance(fmt.value, str)
                        and len(call.args) > 1 and re.search(r"%[sdr]", fmt.value)):
                    wrong.append(f"{where} %-format")
    assert not wrong, "\n".join(wrong)
