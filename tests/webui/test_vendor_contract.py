"""Il patto fra chi chiama una libreria e la libreria spedita.

Il 21/09/2026 KaTeX e Mermaid sono stati cancellati con questa motivazione,
scritta nel commit **e in un banco**: «avevano un solo lettore ciascuno, ed era
la wiki». Per KaTeX era falso — la chat dell'officina la chiamava da quattro
punti — e il banco non se n'e' accorto perche' chiedeva che i **file** fossero
spariti, non che non fosse rimasto un **chiamante**.

Il difetto che ne e' uscito e' invisibile per costruzione: ogni chiamata era
protetta da «se la libreria c'e'», quindi togliendo la libreria non e' successo
niente. Nessun errore, nessuna riga di log, nessun test rosso. Solo le formule
che da quel giorno si vedono come `$...$`.

Qui si misura la regola nei due versi, che sono due difetti diversi:

* **un chiamante senza libreria** — la funzione si spegne in silenzio, e la cosa
  che doveva succedere non succede piu';
* **una libreria senza chiamanti** — peso morto nell'APK e una licenza da tenere
  aggiornata per niente, che e' quel che lo sfoltimento cercava.

Il banco guarda il sorgente: non c'e' un runner con DOM, e queste sono proprieta'
del codice.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
VENDOR = ASSETS / "vendor"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"

# Per ogni libreria: come si riconosce chi la usa, e il file che deve arrivare
# sul telefono perche' quella chiamata voglia dire qualcosa.
#
# I segni sono quelli **d'uso**, non il nome della cartella: `katex` compare
# anche nell'URL che la carica, e cercare quello farebbe passare per «chiamante»
# il caricatore stesso — cioe' il banco si autoconferma.
LIBRARIES = {
    "katex": {
        "segni": (r"\brenderMathInElement\s*\(", r"\bkatex\.render\w*\s*\("),
        "spedito": "assets/vendor/katex@0.16.10/dist/katex.min.js",
    },
    "mermaid": {
        "segni": (r"\bmermaid\.render\s*\(", r"\bmermaid\.initialize\s*\("),
        "spedito": "assets/vendor/mermaid@10/dist/mermaid.min.js",
    },
    "d3": {
        "segni": (r"\bd3\.\w+\s*\(",),
        "spedito": "assets/vendor/d3@7/d3.min.js",
    },
    "marked": {
        "segni": (r"\bmarked\.parse\s*\(", r"\bmarked\.setOptions\s*\("),
        "spedito": "assets/vendor/marked@15.0.7/marked.min.js",
    },
    "dompurify": {
        "segni": (r"\bDOMPurify\.sanitize\s*\(",),
        "spedito": "assets/vendor/dompurify@3/purify.min.js",
    },
}


def _sources() -> dict[str, str]:
    """Il JS del prodotto, senza i vendor: dentro una libreria minificata ci sono
    le sue stesse chiamate, e contarle vorrebbe dire che ogni libreria e' sempre
    usata da se' medesima."""
    return {
        str(p.relative_to(ASSETS)): p.read_text(encoding="utf-8", errors="replace")
        for p in ASSETS.rglob("*.js")
        if "vendor" not in p.relative_to(ASSETS).parts
    }


def _callers(segni: tuple[str, ...]) -> list[str]:
    return sorted(
        name
        for name, src in _sources().items()
        # I commenti raccontano il prima: qui contano solo le chiamate vere.
        if any(re.search(s, re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S)))
               for s in segni)
    )


def test_a_library_with_callers_is_actually_shipped() -> None:
    """Il difetto del 21/09, nella sua forma generale.

    Non basta che il file stia su disco: se non e' nel manifesto non viene
    estratto dall'APK, e sul telefono e' un 404 silenzioso che in locale non si
    vede mai (stessa trappola, altro verso).
    """
    from jenny.utils.android_assets import _UI_MANIFEST

    for name, data in LIBRARIES.items():
        callers = _callers(data["segni"])
        if not callers:
            continue
        path = data["spedito"]
        assert (ASSETS.parent / path).exists(), (
            f"{name} la chiamano {callers} e il file non c'e': quelle chiamate "
            f"sono protette da un «se la libreria c'e'», quindi non falliscono — "
            f"si spengono in silenzio"
        )
        assert path in _UI_MANIFEST, (
            f"{name} e' su disco ma non nel manifesto: sul telefono non arriva, "
            f"e in locale il difetto non si vede"
        )
        assert name in NOTICES.read_text(encoding="utf-8").lower(), (
            f"{name} e' spedito e le note di licenza non lo dicono"
        )


def test_a_shipped_library_has_someone_who_calls_it() -> None:
    """Il verso opposto: un bundle spedito e mai eseguito e' peso nell'APK e una
    licenza da tenere aggiornata per niente. E' la ragione per cui lo
    sfoltimento cercava questi file — la ragione era buona, la misura no."""
    for name, data in LIBRARIES.items():
        if not (ASSETS.parent / data["spedito"]).exists():
            continue
        assert _callers(data["segni"]), (
            f"{name} e' nel pacchetto e non lo chiama nessuno: e' peso morto"
        )


def test_the_heavy_ones_are_fetched_on_demand() -> None:
    """Quanto pesa nel pacchetto e quanto pesa **all'avvio** sono due domande
    diverse, e la seconda e' quella che si sente.

    KaTeX stava in due `<script defer>` dentro `workshop.html`: 275 kB di codice
    piu' il foglio di stile a ogni singola partenza, anche solo per aprire la
    chat — per una cosa che compare in un messaggio su cento. Mermaid invece e'
    sempre stato pigro. Adesso lo sono tutte e due, e nessuno dei due guscia le
    nomina.
    """
    for shell in ("workshop.html", "index.html"):
        html = (ASSETS.parent / shell).read_text(encoding="utf-8")
        for heavy in ("katex", "mermaid", "d3"):
            assert f"vendor/{heavy}" not in html, (
                f"{shell} carica {heavy} all'avvio invece che quando serve"
            )

    # E chi le carica lo fa dal caricatore pigro condiviso, non con un <script>
    # scritto a mano che si riporterebbe dietro la cache dei fallimenti.
    rich = (ASSETS / "shared" / "rich-content.js").read_text(encoding="utf-8")
    assert "ensureVendor(" in rich and "ensureVendorStyle(" in rich, (
        "rich-content si e' fatto un caricatore suo"
    )
