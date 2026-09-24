"""Il banco node dei test della WebUI: trovare node, eseguire, ritagliare il sorgente.

La WebUI non ha un runner con DOM: i test ``*_client.py`` eseguono in node il
codice vero — un modulo importato, o i metodi ritagliati dal sorgente e messi
in una classe finta — e ``assert`` di node fa fallire il processo. Le stesse
righe per trovare node, lanciarlo e ritagliare un metodo stavano ricopiate in
una settantina di file; qui una volta.

- :data:`NODE` e :data:`requires_node`: senza node i test si saltano con lo
  stesso motivo di sempre, ``"node non disponibile"``.
- :func:`run_js`: esegue *script* come modulo ES (``--input-type=module -e``)
  e ne ritorna lo stdout; un codice d'uscita diverso da zero è un fallimento
  con lo stderr di node come messaggio.
- :func:`run_module`: la stessa cosa per chi importa moduli veri da una
  cartella temporanea, dove lo script deve stare in un file accanto a loro.
- :func:`member`, :func:`function`: ritagliano dal sorgente un metodo di classe
  o una funzione di modulo, come faceva ogni file per conto suo.
- :func:`locale`: il file i18n vero di una lingua, per confrontare le stringhe.

*env* **sostituisce** l'ambiente di node, non si fonde con quello del test: è
così che i test che fissano ``TZ`` o passano l'URL di un modulo lo usavano.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node non disponibile")

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
I18N_DIR = ASSETS / "i18n"


def _check(proc: subprocess.CompletedProcess[str]) -> str:
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout


def run_js(script: str, *, timeout: float = 60, env: dict[str, str] | None = None) -> str:
    """Esegue *script* come modulo ES e ne ritorna lo stdout."""
    proc = subprocess.run(
        [str(NODE), "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return _check(proc)


def run_module(entry: Path, *, timeout: float = 60) -> str:
    """Esegue il file *entry* (un ``.mjs`` accanto ai moduli che importa)."""
    proc = subprocess.run([str(NODE), str(entry)], capture_output=True, text=True, timeout=timeout)
    return _check(proc)


def member(
    source: str,
    name: str,
    *,
    prefixes: tuple[str, ...] = ("async ", "get "),
    body_only: bool = False,
) -> str:
    """Il metodo *name* di una classe, indentato di due spazi, com'è scritto.

    *prefixes* sono le parole che possono precederne il nome: chi ha un getter
    omonimo di un metodo passa solo ``("async ",)``. Con *body_only* torna solo
    il corpo, senza firma né graffe.
    """
    words = "|".join(re.escape(p) for p in prefixes)
    lead = f"(?:{words})?" if words else ""
    m = re.search(
        rf"\n  ({lead}{re.escape(name)}\(([^)]*)\)\s*\{{(.*?))\n  \}}", source, re.S
    )
    assert m, f"{name} non trovato"
    if body_only:
        return m.group(3)
    return m.group(1) + "\n  }"


def function(source: str, name: str, *, strip_export: bool = True) -> str:
    """La funzione di modulo *name*, dalla firma alla graffa di chiusura in
    colonna zero. ``export`` si toglie, così si incolla in uno script."""
    m = re.search(rf"(?ms)^(?:export )?(?:async )?function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    text = m.group(0)
    return text.removeprefix("export ") if strip_export else text


def locale(name: str) -> dict:
    """Il dizionario i18n *name* (``it``, ``en``) com'è nel pacchetto."""
    return json.loads((I18N_DIR / f"{name}.json").read_text(encoding="utf-8"))
