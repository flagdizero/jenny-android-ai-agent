"""Le mini-app prendono i colori del tema attivo, non una palette di riserva.

Il difetto: `jenny-kit.css` porta una copia dei token della SPA perché le custom
property non attraversano l'origine opaca dell'iframe — ma era una copia
*statica*, una palette dark e una light, mentre i temi sono 7. L'unico token che
seguiva il tema era `--accent`, l'unico che l'SDK riscrivesse: tutto il resto
restava la riserva, che per giunta era indaco, un colore che nessuno dei 7 temi
ha. Restavano blu per sempre il velo di sfondo del `<body>` (`--bg-pattern`) e il
pressed di `.btn-primary`/`.fab` (`--accent-hover`), e su un tema chiaro colorato
come y2k l'app intera restava grigia mentre la SPA era rosa.

Il contratto è: la SPA serializza i valori *calcolati* del tema attivo
(`APP_TOKEN_MAP` → `themeTokens()`), li manda su entrambi i canali — query string
al primo paint, `jenny:theme` a ogni cambio — e l'SDK li riapplica dopo averne
ripassato nome e formato, perché da dentro il frame non si può sapere chi ha
scritto nella query string.

Asserzioni sul sorgente, come in ``test_mini_app_navigation_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
SDK_JS = ASSETS / "apps" / "jenny-sdk.js"
KIT_CSS = ASSETS / "apps" / "jenny-kit.css"
APPS_JS = ASSETS / "shared" / "apps-actions.js"
THEME_JS = ASSETS / "shared" / "theme.js"
SPA_CSS = ASSETS / "mobile-style.css"

# L'indaco che il kit portava come riserva, in tutte le forme in cui compariva.
INDIGO = ("#6366f1", "#818cf8", "#a5b4fc", "#4f46e5", "99,102,241", "129,140,248")

# Token del kit che non passano dal ponte, ognuno per una ragione sua.
UNBRIDGED = {
    # Derivati dall'accent dall'SDK stesso.
    "--accent", "--accent-rgb", "--accent-subtle", "--on-accent", "--bg-pattern",
    # Il colore *è* il messaggio in `.badge-ok`, e l'`--ok` di alcuni temi vale
    # avorio o lo stesso giallo del loro `--warning`.
    "--green", "--success-bg",
    # Derivati da `--error`/`--warning` dall'SDK.
    "--error-bg", "--warning-bg",
    # Strutturali: non dipendono dal tema.
    "--radius", "--radius-sm", "--radius-pill", "--font-sans", "--font-mono",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _token_map() -> dict[str, str]:
    """`APP_TOKEN_MAP` di shared/theme.js, letta dal sorgente."""
    block = re.search(r"export const APP_TOKEN_MAP = \{(.*?)\n\};", _read(THEME_JS), re.S)
    assert block, "APP_TOKEN_MAP non trovata in shared/theme.js"
    return dict(re.findall(r"'(--[\w-]+)':\s*'(--[\w-]+)'", block.group(1)))


def _kit_tokens() -> set[str]:
    """Token dichiarati nel blocco di riserva scuro di jenny-kit.css."""
    block = re.search(r":root, \[data-theme=\"dark\"\] \{(.*?)\n\}", _read(KIT_CSS), re.S)
    assert block, "blocco di riserva non trovato in jenny-kit.css"
    return set(re.findall(r"^\s*(--[\w-]+):", block.group(1), re.M))


def _spa_root_tokens() -> set[str]:
    block = re.search(r"^:root \{(.*?)\n\}", _read(SPA_CSS), re.S | re.M)
    assert block, ":root non trovato in mobile-style.css"
    return set(re.findall(r"^\s*(--[\w-]+):", block.group(1), re.M))


def test_ogni_token_del_kit_segue_il_tema_o_e_escluso_per_iscritto():
    """Il test che avrebbe preso il bug: nessun token può restare indietro in silenzio.

    Un token nuovo aggiunto al kit e a nessun altro posto resta al valore di
    riserva su tutti e 7 i temi — esattamente com'era `--accent-hover`. Qui o sta
    nella mappa del ponte, o sta in UNBRIDGED con la sua ragione accanto.
    """
    orphans = _kit_tokens() - set(_token_map()) - UNBRIDGED
    assert not orphans, (
        f"token del kit che non seguono il tema: {sorted(orphans)} — aggiungili a "
        "APP_TOKEN_MAP in shared/theme.js, o a UNBRIDGED qui con il motivo"
    )


def test_la_mappa_punta_a_token_che_la_spa_ha_davvero():
    """Un token SPA rinominato deve rompere qui, non sbiadire dentro le app."""
    unknown = {spa for spa in _token_map().values() if spa not in _spa_root_tokens()}
    assert not unknown, f"APP_TOKEN_MAP punta a token assenti da mobile-style.css: {sorted(unknown)}"


def test_sdk_e_spa_sono_daccordo_su_quali_token_attraversano():
    """Il whitelist dell'SDK e la mappa della SPA sono la stessa lista.

    Sono per forza due copie — l'SDK è uno script classico servito nell'iframe e
    non può importare un modulo ES del parent — quindi la coerenza va asserita.
    """
    block = re.search(r"const KIT_TOKENS = new Set\(\[(.*?)\]\);", _read(SDK_JS), re.S)
    assert block, "KIT_TOKENS non trovato in jenny-sdk.js"
    allowed = set(re.findall(r"'([\w-]+)'", block.group(1)))
    expected = {name[2:] for name in _token_map()}
    assert allowed == expected, (
        f"SDK e APP_TOKEN_MAP divergono: solo nell'SDK {sorted(allowed - expected)}, "
        f"solo nella mappa {sorted(expected - allowed)}"
    )


def test_la_palette_viaggia_su_entrambi_i_canali():
    """Il postMessage da solo non basta: l'iframe dipinge prima che arrivi."""
    apps = _read(APPS_JS)
    assert "themeTokens()" in apps, "apps-actions.js non legge mai la palette del tema"
    src = re.search(r"const src = (.*?);\n", apps, re.S)
    assert src and "tokens=" in src.group(1), "la palette manca dalla query string dell'iframe"
    post = re.search(r"type: 'jenny:theme'.*?\}", apps, re.S)
    assert post and "tokens:" in post.group(0), "la palette manca dal messaggio jenny:theme"

    sdk = _read(SDK_JS)
    assert "applyTokens(qs.get('tokens'))" in sdk, "l'SDK non applica la palette al primo paint"
    assert re.search(r"jenny:theme'.*?applyTokens\(msg\.tokens\)", sdk, re.S), (
        "l'SDK non riapplica la palette al cambio tema"
    )


def test_i_valori_dal_parent_sono_ripassati_prima_di_finire_in_uno_stile():
    """Chi scrive nel frame non è verificabile da dentro: nomi e valori si validano.

    Il rischio concreto non è un colore brutto, è `url(...)`: una richiesta di
    rete pilotabile da fuori dentro una custom property applicata al documento.
    """
    body = re.search(r"function applyTokens\(spec\) \{(.*?)\n  \}", _read(SDK_JS), re.S)
    assert body, "applyTokens non trovata in jenny-sdk.js"
    assert "KIT_TOKENS.has(name)" in body.group(1), "applyTokens non filtra i nomi"
    assert "COLOR.test(value)" in body.group(1), "applyTokens non filtra i valori"

    color = re.search(r"const COLOR = (/.*?/i);", _read(SDK_JS))
    assert color, "il formato dei valori non è dichiarato"
    pattern = color.group(1)
    for hostile in ("url(", "var(", "expression", "image-set("):
        assert hostile not in pattern, f"il formato dei valori ammette {hostile}"


def test_niente_indaco_rimasto_nel_kit():
    """La riserva è chanel/pietra, i due temi di default — non un colore inventato."""
    for path in (KIT_CSS, ASSETS / "apps" / "jenny-charts.js"):
        source = _read(path).lower().replace(" ", "")
        leftovers = [ink for ink in INDIGO if ink in source]
        assert not leftovers, f"{path.name} contiene ancora l'indaco di riserva: {leftovers}"


def test_il_velo_di_sfondo_e_il_pressed_seguono_laccent():
    """I due punti che l'utente vedeva blu, presi uno per uno."""
    sdk = _read(SDK_JS)
    accent = re.search(r"function applyAccent\(accent, onAccent\) \{(.*?)\n  \}", sdk, re.S)
    assert accent, "applyAccent non trovata"
    assert "--bg-pattern" in accent.group(1), (
        "il velo di sfondo del <body> non segue l'accent: resta quello della riserva"
    )
    assert "--accent-hover" in _token_map(), (
        "il pressed di .btn-primary/.fab non segue il tema"
    )
