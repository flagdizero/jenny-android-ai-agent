"""Ogni variabile CSS che i fogli leggono esiste da qualche parte.

`var(--x, ripiego)` con una `--x` che nessuno definisce non e' un errore: vale
il ripiego, in silenzio e per sempre. Cosi' `--danger`, `--danger-bg` e
`--code-bg` hanno dato per mesi lo stesso rosso e lo stesso grigio in tutti e
sette i temi (revisione del 25/09/2026), mentre chi leggeva il foglio credeva
di vedere dei token.

Una variabile e' definita se un foglio la dichiara (`--x:` in una regola) o se
il JS/HTML la scrive per nome (`setProperty('--x', …)`, `style="--x: …"`):
`--vv-height`, `--jenny-size` e compagnia vengono da li'.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"
SHEETS = ("mobile-style.css", "casa-style.css")


def _senza_commenti(testo: str) -> str:
    return re.sub(r"/\*.*?\*/", "", testo, flags=re.S)


def test_every_custom_property_read_by_the_sheets_is_defined() -> None:
    css = "".join(_senza_commenti((ASSETS / f).read_text(encoding="utf-8")) for f in SHEETS)
    lette = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    assert len(lette) > 30, f"la grep sulle variabili non morde piu' ({len(lette)})"
    dichiarate = set(re.findall(r"(--[\w-]+)\s*:", css))

    sorgenti = [
        f.read_text(encoding="utf-8")
        for f in ASSETS.rglob("*.js")
        if "vendor" not in f.relative_to(ASSETS).parts
    ]
    sorgenti += [f.read_text(encoding="utf-8") for f in UI.glob("*.html")]
    testo = "".join(sorgenti)
    scritte = set(re.findall(r"""['"`](--[\w-]+)['"`]""", testo))
    scritte |= set(re.findall(r"(--[\w-]+)\s*:", testo))

    mai = sorted(lette - dichiarate - scritte)
    assert not mai, (
        f"variabili lette e mai definite: {mai}. Vale sempre il ripiego: usa un "
        f"token del tema (--error, --overlay-strong, ...) o definiscila per tema"
    )
