"""Chi scrive l'aggancio al progetto, e quando.

Passo **5** di ``roadmap/progetti-passi.md``.

**Questo file era il doppio.** Fino al 21/09/2026 teneva anche la meta' che si
vedeva: dentro un progetto le viste wiki e grafo dell'officina dovevano
mostrare quel progetto e basta — la Home della wiki *e'* l'elenco degli altri
progetti, e i nodi del grafo home *sono* le altre wiki. Quelle due viste sono
uscite dall'officina: elenco, mappa e lettore vivono in casa
(``casa-pages.js``, ``casa-map.js``, ``casa-reader.js``), e la' l'isolamento non
e' un aggancio da mantenere ma la forma stessa della stanza — si aprono **dal**
quaderno aperto, e non esiste una Home da cui vedere gli altri.

Resta la regola che quelle viste rendevano necessaria, e che non dipende da
loro: l'aggancio ha **un solo scrittore**, lo scope chip, che e' anche l'unico a
sapere in che conversazione siamo. Due scrittori sarebbero due risposte alla
stessa domanda.

**Nota, misurata il 21/09/2026:** dopo l'uscita delle due viste ``pinnedWiki``
non ha piu' nessun lettore in tutto il prodotto — si scrive e non si legge. La
regola del singolo scrittore resta vera e questi banchi restano verdi; ma
toglierlo del tutto e' un giro suo, non una coda di questo.

Asserzioni sul sorgente, come ``test_scope_menu_contract.py``: la WebUI non ha
un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
ASSETS = UI_DIR / "assets"
APP_JS = ASSETS / "mobile-app.js"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _method(path: Path, name: str) -> str:
    """Il corpo di un metodo di classe, indentato di due spazi."""
    body = re.search(rf"\n  (?:async |get )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", _src(path), re.S)
    assert body, f"{name} non trovato in {path.name}"
    return body.group(1)


# ── Un solo scrittore ─────────────────────────────────────────────────────


def test_only_the_scope_chip_publishes_the_pin() -> None:
    """Due scrittori sono due risposte a «in che progetto siamo», e divergono."""
    writers = [
        path.name
        for path in sorted(ASSETS.rglob("*.js"))
        if re.search(r"AppState\.set\(\s*['\"]pinnedWiki['\"]", _src(path))
    ]
    assert writers == ["scope-chip.js"], (
        "l'aggancio lo pubblica il chip, che è l'unico a sapere in che conversazione "
        f"siamo; trovati invece: {writers}"
    )


def test_the_personal_chat_dissolves_the_pin() -> None:
    """Tornare alla personale rimette la Home: ``null``, non l'ultimo progetto."""
    body = _method(CHIP_JS, "_publishPin")
    assert "kind === 'project'" in body and "null" in body
