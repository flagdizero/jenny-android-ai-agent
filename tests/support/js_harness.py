"""Il banco node dei test della WebUI: trovare node, eseguire, ritagliare il sorgente.

La WebUI non ha un runner con DOM: i test ``*_client.py`` eseguono in node il
codice vero — un modulo importato, o i metodi ritagliati dal sorgente e messi
in una classe finta — e ``assert`` di node fa fallire il processo. Le stesse
righe per trovare node, lanciarlo e ritagliare un metodo stavano ricopiate in
una settantina di file; qui una volta.

- :data:`NODE` e :data:`requires_node`: senza node i test si saltano con lo
  stesso motivo di sempre, ``"node non disponibile"``.
- :func:`run_js`: esegue *script* come modulo ES (``--input-type=module``, lo
  script da stdin) e ne ritorna lo stdout; un codice d'uscita diverso da zero è un fallimento
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
    """Esegue *script* come modulo ES e ne ritorna lo stdout.

    Lo script arriva a node da stdin, non con ``-e``: Linux rifiuta un singolo
    argomento oltre i 128 KB (``E2BIG``, «Argument list too long»), macOS no, e il
    banco di ``test_home_switch_client.py`` li ha superati il 30/10/2026 — verde
    sul Mac, 89 rossi sulla CI.
    """
    proc = subprocess.run(
        [str(NODE), "--input-type=module"],
        input=script,
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


# Il finto elemento delle stanze della casa (``home-*.js``): un oggetto con
# attributi, listener per tipo, figli e un ``classList`` che regge
# ``add``/``remove``/``contains``/``toggle``. Era ricopiato in ogni banco con un
# campo in più o in meno; qui ha l'unione dei campi, tutti passivi: un banco
# che non li legge non se ne accorge. Un ``#fragment`` versa i suoi figli in
# chi lo appende, come un ``DocumentFragment``. Si mette nello script al posto
# del segnaposto ``__MAKE_EL__``.
FAKE_ELEMENT = r"""
function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    value: '',
    placeholder: '',
    href: '',
    hidden: false,
    disabled: false,
    dataset: {},
    attrs: {},
    style: {},
    children: [],
    listeners: {},
    isFragment: tag === '#fragment',
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { (el.listeners[type] ||= []).push(fn); },
    appendChild(child) {
      if (child.isFragment) { el.children.push(...child.children); child.children = []; }
      else el.children.push(child);
      return child;
    },
    replaceChildren(...fresh) { el.children = fresh; },
    focus() { el.focused = true; },
    classList: {
      add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
      remove(n) {
        el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
      },
      contains(n) { return String(el.className).split(' ').includes(n); },
      toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
    },
  };
  return el;
}
"""

# Il finto elemento delle tendine (la scelta del quaderno, i comandi): una riga
# ricorda gli attributi scritti, i *tipi* dei listener montati (``listeners``:
# una riga che non ascolta niente non è una scelta) e l'ultimo gestore per tipo
# (``handlers``). ``innerHTML = ''`` svuota i figli come nel DOM vero, e
# ``querySelector`` dà sempre uno ``span`` nuovo. Segnaposto ``__MAKE_EL__``.
FAKE_MENU_ELEMENT = r"""
function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    children: [],
    dataset: {},
    attrs: {},
    listeners: [],
    handlers: {},
    appendChild(child) { el.children.push(child); return child; },
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { el.listeners.push(type); el.handlers[type] = fn; },
    scrollIntoView() {},
    querySelector() { return makeEl('span'); },
  };
  el.classList = {
    add(c) { el.className = (el.className + ' ' + c).trim(); },
    remove() {},
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { if (v === '') el.children.length = 0; },
  });
  return el;
}
"""


def locale(name: str) -> dict:
    """Il dizionario i18n *name* (``it``, ``en``) com'è nel pacchetto."""
    return json.loads((I18N_DIR / f"{name}.json").read_text(encoding="utf-8"))
