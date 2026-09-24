"""La cache delle impostazioni della casa non rimette indietro la finestra flottante.

``CasaApp._askSettings`` chiede ``/api/settings`` una volta e ne tiene la
promessa: ogni apertura delle Impostazioni la rilegge e passa ``floating`` a
``CasaJenny.setFloating``. Toccato l'interruttore, la stanza sapeva il valore
nuovo e la cache quello vecchio, e alla riapertura vinceva la cache:
l'interruttore si ridisegnava spento con la finestra accesa (visto sul telefono
il 25/09). ``_keepFloating`` scrive nella cache com'e' finita.

I metodi si ritagliano dal sorgente e girano in node, come negli altri banchi
della casa.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "casa-app.js"

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(rf"\n  ({re.escape(name)}\([^)]*\)\s*\{{.*?\n  \}})", source, re.S)
    assert m, f"{name} non trovato"
    return m.group(1)


def _run_js(script: str) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    harness = f"""
import assert from 'node:assert/strict';
let letture = 0;
const api = {{
  getSettings: async () => {{
    letture += 1;
    return {{ floating: {{ available: true, enabled: false, active: false }} }};
  }},
}};
class Casa {{
  constructor() {{ this._settings = null; }}
  {_member(src, "_askSettings")}
  {_member(src, "_keepFloating")}
}}
const casa = new Casa();
const giro = () => new Promise((r) => setTimeout(r, 0));
"""
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", harness + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_reopening_settings_reads_the_switch_as_it_ended() -> None:
    _run_js("""
      const prima = await casa._askSettings();
      assert.equal(prima.floating.enabled, false);
      casa._keepFloating({ available: true, enabled: true, active: true });
      await giro();
      const dopo = await casa._askSettings();
      assert.equal(dopo.floating.enabled, true, 'la cache ha rimesso lo stato di prima');
      assert.equal(letture, 1, 'resta una lettura sola: la cache non si butta');
    """)


def test_nothing_to_keep_when_nothing_was_read() -> None:
    """Senza cache non c'e' niente da correggere: la prossima apertura legge
    dal server, che sa gia' il valore nuovo."""
    _run_js("""
      casa._keepFloating({ enabled: true });
      assert.equal(casa._settings, null);
      await casa._askSettings();
      assert.equal(letture, 1);
    """)


def test_the_room_is_wired_to_the_cache() -> None:
    """Grep, non comportamento: il comportamento dei due capi sta qui sopra e in
    ``test_casa_jenny_client.py``; qui si tiene fermo il filo fra i due."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "onFloating: (floating) => this._keepFloating(floating)" in src
