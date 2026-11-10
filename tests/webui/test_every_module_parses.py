"""Ogni modulo della WebUI deve **parsare come modulo ES**.

Il 21/09/2026 ho spedito sul telefono un `shared/apps-actions.js` con dentro
cinque code di metodo duplicate — un'estrazione che risaliva al commento e si
portava dietro la fine del metodo precedente. Risultato: un `SyntaxError` al
caricamento, l'intero grafo dei moduli che non parte, e la casa a schermo
**senza conversazione e senza mascotte**. La suite era verde: 10.084 banchi, e
nessuno apriva quel file per leggerlo come lo legge un browser.

**Perche' nessuno se n'era accorto, ed e' la parte che vale.** Avevo controllato
con `node --check`, che su quel file **esce zero e non stampa niente**: senza
un'estensione `.mjs` lo parsa come CommonJS, dove quelle righe stanno in piedi.
E rinominandolo `.mjs` l'errore compare a schermo ma **l'uscita resta zero** —
quindi anche uno script che ne leggesse il codice di ritorno direbbe «a posto».
Due modi diversi di mentire, misurati.

L'unico controllo che regge e' costruire davvero un modulo. `vm.SourceTextModule`
lo **parsa senza eseguirlo**, che e' esattamente cio' che serve qui: questi file
al caricamento toccano `localStorage`, `document` e la rete, e importarli
davvero vorrebbe dire provare il browser invece della sintassi.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support.js_harness import ASSETS, NODE, requires_node

pytestmark = requires_node

# Il parser sta in una stringa e non in un file del repo: e' un attrezzo di
# questo banco, e tenerlo qui significa che non puo' divergere da chi lo usa.
_PARSER = """
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const broken = [];
for (const f of process.argv.slice(1)) {
  try {
    new vm.SourceTextModule(readFileSync(f, 'utf8'), { identifier: f });
  } catch (e) {
    broken.push(f + ': ' + e.constructor.name + ': ' + e.message);
  }
}
if (broken.length) { console.log(broken.join('\\n')); process.exit(1); }
"""


def _sources() -> list[Path]:
    """I moduli di prima parte. `vendor/` no: e' roba di altri, spesso minificata
    e a volte in un dialetto che non e' un modulo."""
    return sorted(
        p for p in ASSETS.rglob("*.js")
        if "vendor" not in p.parts and "apps" not in p.relative_to(ASSETS).parts[:1]
    )


def test_every_ui_module_parses_as_an_es_module() -> None:
    sources = _sources()
    assert len(sources) > 30, f"trovati solo {len(sources)} moduli: il banco guarda male"

    proc = subprocess.run(
        [str(NODE), "--experimental-vm-modules", "--input-type=module",
         "--eval", _PARSER, "--", *[str(p) for p in sources]],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        "questi file non parsano come modulo ES — il browser non carica nulla "
        f"e la schermata resta vuota:\n{proc.stdout}{proc.stderr}"
    )


def test_the_check_catches_the_one_that_shipped(tmp_path: Path) -> None:
    """La prova che il banco non e' una formalita'.

    Si ricostruisce la forma del difetto vero — la coda di un metodo duplicata
    **fuori** da qualunque metodo, dentro il corpo della classe — e si chiede al
    controllo di vederla. Un banco scritto dopo il fatto e' credibile solo se
    fallisce sul fatto.
    """
    broken = """
export class Try {
  open() {
    document.body.appendChild(overlay);
  }

    document.body.appendChild(overlay);
    this._openApp = { depth: 1 };
  }
}
"""
    fake = tmp_path / "jenny-parse-rotto.js"
    fake.write_text(broken, encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(NODE), "--experimental-vm-modules", "--input-type=module",
             "--eval", _PARSER, "--", str(fake)],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 1, "il controllo non ha visto il file rotto"
        assert "SyntaxError" in proc.stdout
    finally:
        fake.unlink(missing_ok=True)


def test_node_check_would_not_have_caught_it(tmp_path: Path) -> None:
    """**Il motivo per cui questo file esiste.**

    `node --check` era il controllo che usavo, ed e' quello che mi ha lasciato
    spedire. Qui si misura la sua bugia invece di ricordarsela: sullo stesso
    file rotto esce **zero**.

    Dipende dalla versione di node, ed e' misurato: la 26.0 lo lascia passare,
    la 24.21 della CI lo vede. Quindi `node --check` non puo' sostituire il
    parser qui sopra finche' una versione che si usa lo lascia passare. Dove
    lo vede il banco si salta dicendolo, invece di fallire: il controllo che
    conta e' ``test_the_check_catches_the_one_that_shipped``, e resta obbligatorio.
    """
    fake = tmp_path / "jenny-parse-rotto2.js"
    fake.write_text(
        "export class P {\n  a() {\n    document.body.x();\n  }\n\n"
        "    document.body.x();\n  }\n}\n",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [str(NODE), "--check", str(fake)], capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            version = subprocess.run(
                [str(NODE), "--version"], capture_output=True, text=True, timeout=60,
            ).stdout.strip()
            pytest.skip(
                f"node {version} --check vede il file rotto; altre versioni no "
                "(la 26.0 esce zero): il parser del banco resta"
            )
    finally:
        fake.unlink(missing_ok=True)
