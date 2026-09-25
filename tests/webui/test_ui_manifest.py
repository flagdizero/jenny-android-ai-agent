"""Guards for the Android UI asset pipeline.

Files not listed in ``_UI_MANIFEST`` are never extracted to the workspace on
Android and 404 silently. These tests keep index.html, the manifest, and the
files on disk mutually consistent, and enforce Phase-1 CSS invariants.
"""

import re
from pathlib import Path

from jenny.utils.android_assets import _UI_MANIFEST

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"

# Tokens removed by the token rationalization: any reference is a regression.
DEAD_TOKENS = [
    "--bg2",
    "--bg3",
    "--bg-secondary",
    "--bg-solid",
    "--bg-pattern",
    "--border2",
    "--border-color",
    "--text2",
    "--text3",
    "--text-secondary",
    "--accent-color",
    "--accent-subtle",
    "--accent-bg",
    "--green",
    "--success-bg",
    "--success-fg",
    "--si-hover",
    "--si-on-bg",
    "--hover-bg",
    "--glass",
    "--blur",
    "--saturate",
]


def _index_asset_refs() -> list[str]:
    html = (UI_DIR / "index.html").read_text()
    refs = re.findall(r'(?:href|src)="/html-mobile/([^"]+)"', html)
    assert refs, "no /html-mobile/ asset references found in index.html"
    return refs


def test_index_html_assets_are_in_manifest():
    manifest = set(_UI_MANIFEST)
    missing = [ref for ref in _index_asset_refs() if ref not in manifest]
    assert not missing, (
        f"index.html references assets missing from _UI_MANIFEST "
        f"(they would 404 on Android): {missing}"
    )


def test_js_module_imports_are_in_manifest():
    """Ogni import ES tra i moduli JS bundlati deve risolvere a una voce del manifest.

    Un modulo importato ma non estratto su Android viene servito come HTML di
    fallback e il caricamento fallisce per MIME type (visto dal vivo con
    backup-flow.js): questa guardia copre gli import statici e dinamici.
    """
    manifest = set(_UI_MANIFEST)
    import_re = re.compile(
        r"""(?:from\s+|import\s*\(\s*)['"](\.{1,2}/[^'"]+\.js)['"]"""
    )
    problems = []
    for entry in _UI_MANIFEST:
        if not entry.endswith(".js"):
            continue
        source = UI_DIR / entry
        if not source.is_file():
            continue
        for spec in import_re.findall(source.read_text()):
            resolved = (source.parent / spec).resolve()
            target = resolved.relative_to(UI_DIR.resolve()).as_posix()
            if target not in manifest:
                problems.append(f"{entry} -> {spec}")
    assert not problems, (
        f"JS module imports not covered by _UI_MANIFEST (they 404 on Android): {problems}"
    )


def test_manifest_entries_exist_on_disk():
    missing = [entry for entry in _UI_MANIFEST if not (UI_DIR / entry).is_file()]
    assert not missing, f"_UI_MANIFEST lists files that do not exist: {missing}"


def test_ui_active_files_on_disk_are_in_manifest():
    """Direzione speculare: ogni HTML/JS/CSS su disco deve essere nel manifest.

    Un file di contenuto attivo bundlato ma fuori dal manifest non passa dalla
    sync d'avvio (che riallinea la copia servita al package). Se un domani
    venisse referenziato, resterebbe uno slot d'iniezione persistente non
    coperto né dall'estrazione né dal serving canonico. Copre il buco lasciato
    dai guard esistenti (solo manifest→disco e ref→manifest per la UI).
    """
    manifest = set(_UI_MANIFEST)
    unlisted = sorted(
        p.relative_to(UI_DIR).as_posix()
        for ext in ("*.js", "*.css", "*.html")
        for p in UI_DIR.rglob(ext)
        if p.relative_to(UI_DIR).as_posix() not in manifest
    )
    assert not unlisted, (
        f"file UI attivi su disco assenti da _UI_MANIFEST "
        f"(fuori da sync ed estrazione): {unlisted}"
    )


def test_manifest_has_no_duplicates():
    duplicates = sorted({e for e in _UI_MANIFEST if _UI_MANIFEST.count(e) > 1})
    assert not duplicates, f"voci duplicate in _UI_MANIFEST: {duplicates}"


def test_css_url_refs_are_in_manifest():
    """Every url(...) in first-party CSS must resolve to a bundled file."""
    manifest = set(_UI_MANIFEST)
    css_files = ["assets/mobile-style.css", "assets/vendor/fonts/google-fonts.css"]
    theme_fonts = UI_DIR / "assets/vendor/fonts/theme-fonts.css"
    if theme_fonts.is_file():
        css_files.append("assets/vendor/fonts/theme-fonts.css")
    problems = []
    for rel in css_files:
        css_path = UI_DIR / rel
        for url in re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", css_path.read_text()):
            if url.startswith(("data:", "http:", "https:", "#")):
                continue
            resolved = (css_path.parent / url.split("?")[0]).resolve()
            entry = resolved.relative_to(UI_DIR.resolve()).as_posix()
            if entry not in manifest:
                problems.append(f"{rel} -> {url}")
    assert not problems, f"CSS url() references not covered by _UI_MANIFEST: {problems}"


def test_backdrop_filter_only_on_the_drawer_scrim():
    """Il divieto resta, con **una** eccezione dichiarata.

    Il divieto totale e\' del 01/08/2026 (`8833b94`, "Jenny 0.3.0"), e la
    ragione scritta allora era «Android WebView performance». Accanto, su
    `.swipe-scrim`, un commento della stessa data ne da\' un\'altra: il WebView
    rendeva **nere** le zone trasparenti, cioe\' le icone.

    Il 20/09/2026 l\'eccezione: lo scrim del cassetto. Le due ragioni di allora
    non lo riguardano allo stesso modo —

    * `.swipe-scrim` anima l\'opacita\' a **ogni frame** durante il gesto di
      cambio vista; questo sfuma una volta e poi sta fermo. E\' il caso facile,
      non quello che aveva motivato il divieto.
    * sul Titan 2 gira oggi **WebView 143.0.7499.192** (misurato): il difetto
      delle zone trasparenti rese nere e\' di versioni molto piu\' vecchie.

    Quindi il banco non sparisce e non si allarga: **elenca**. Un
    `backdrop-filter` nuovo su un altro selettore torna rosso e obbliga chi lo
    aggiunge a dire perche\', che e\' esattamente cio\' che il divieto proteggeva.

    **Resta da confermare sul telefono**: le icone delle app nella lista e la
    mascotte — il nodo trasparente piu\' grosso della casa — a cassetto aperto.
    Se tornassero nere, si toglie la riga e si rimette il divieto tondo.
    """
    css = (UI_DIR / "assets/mobile-style.css").read_text()
    # I selettori a cui e\' concesso. Si dichiarano uno per uno.
    concessi = {".launcher-scrim"}

    colpevoli = []
    for block in css.split("}"):
        if not re.search(r"backdrop-filter\s*:", block):
            continue
        head = block.split("{")[0].strip()
        names = {s.strip().splitlines()[-1].strip() for s in head.split(",") if s.strip()}
        if not (names & concessi):
            colpevoli.append(head.splitlines()[-1].strip() if head else "?")

    assert not colpevoli, (
        f"backdrop-filter su selettori non concessi: {colpevoli}. "
        f"E\' vietato per le prestazioni del WebView Android (usa superfici "
        f"opache); le eccezioni si dichiarano in `concessi` con il motivo."
    )


def test_the_drawer_scrim_actually_blurs():
    """E l\'eccezione deve esserci davvero: toglierla e\' una decisione, non una
    svista. Senza questa riga, il banco qui sopra passerebbe anche a
    sfocatura sparita."""
    css = (UI_DIR / "assets/mobile-style.css").read_text()
    block = next(b for b in css.split("}") if ".launcher-scrim" in b.split("{")[0])
    assert re.search(r"[^-]backdrop-filter:\s*blur\(", block), "lo scrim non sfoca piu\'"
    assert "-webkit-backdrop-filter" in block, "manca il prefisso: il WebView usa quello"


def test_accent_backgrounds_use_on_accent():
    """Testo hardcoded bianco su sfondo accent = illeggibile coi temi chiari (Chanel).

    Ogni regola con ``background: var(--accent)`` deve usare ``var(--on-accent)``
    (o un token) per il colore del testo, mai #fff/#ffffff/white — visto dal vivo
    sul bottone export della sezione backup. Copre anche gli stili inline nei JS.
    """
    white_re = re.compile(r"color\s*:\s*(#fff\b|#ffffff\b|white\b)", re.IGNORECASE)
    offenders = []

    css = (UI_DIR / "assets/mobile-style.css").read_text()
    for block in css.split("}"):
        if "background: var(--accent)" in block and white_re.search(block):
            selector = block.split("{")[0].strip().splitlines()[-1].strip()
            offenders.append(f"mobile-style.css: {selector}")

    for path in sorted((UI_DIR / "assets").rglob("*.js")):
        if "vendor" in path.parts:
            continue
        for match in re.finditer(r'style="([^"]*)"', path.read_text()):
            style = match.group(1)
            if "var(--accent)" in style and white_re.search(style):
                offenders.append(f"{path.name}: {style[:60]}…")

    assert not offenders, (
        f"hardcoded white text on accent background (use var(--on-accent)): {offenders}"
    )


def test_provider_dialog_never_prefills_masked_api_key():
    """La maschera della chiave API sta nel placeholder, mai nel ``value``.

    Pre-compilare il campo con ``api_key_hint`` faceva salvare il segnaposto
    (`sk-a...j8f9`) come chiave vera, distruggendo quella configurata.
    """
    source = (UI_DIR / "assets/mobile-settings.js").read_text()
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "api_key_hint" in line and re.search(r"\bvalue\s*=", line)
    ]
    assert not offenders, f"api_key_hint bound to an input value: {offenders}"


def test_no_dead_token_references():
    pattern = re.compile(
        r"var\((" + "|".join(re.escape(tok) for tok in DEAD_TOKENS) + r")[,)]"
    )
    offenders = []
    for path in [UI_DIR / "assets/mobile-style.css", *sorted((UI_DIR / "assets").glob("mobile-*.js"))]:
        for match in pattern.finditer(path.read_text()):
            offenders.append(f"{path.name}: {match.group(0)}")
    assert not offenders, f"references to removed CSS tokens: {offenders}"


def test_third_party_licenses_are_actually_shipped():
    """Ogni licenza che THIRD_PARTY_NOTICES.md promette deve finire nell'APK.

    Il buco che questo chiude: la licenza di DOMPurify stava su disco e nel file
    delle note, ma non nel manifest — quindi l'APK spediva il codice senza il
    testo di licenza a cui le note rimandano. Le altre otto librerie
    vendorizzate ce l'avevano. Il guard speculare qui sopra non l'ha visto
    perché guarda solo ``.js``/``.css``/``.html``, e un ``LICENSE`` non ha
    estensione.
    """
    notices = (UI_DIR.parents[2] / "THIRD_PARTY_NOTICES.md").read_text()
    promised = sorted(set(re.findall(r"`(vendor/[^`]+/LICENSE(?:\.md|\.txt)?)`", notices)))
    assert promised, "nessun percorso di licenza trovato in THIRD_PARTY_NOTICES.md"

    manifest = set(_UI_MANIFEST)
    missing_on_disk = [p for p in promised if not (UI_DIR / "assets" / p).is_file()]
    unshipped = [p for p in promised if f"assets/{p}" not in manifest]

    assert not missing_on_disk, f"licenze promesse dalle note e assenti su disco: {missing_on_disk}"
    assert not unshipped, (
        f"licenze presenti su disco ma fuori da _UI_MANIFEST, quindi non spedite: {unshipped}"
    )
