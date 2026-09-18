"""Le bolle flottanti **renderizzano** il markdown, non lo mostrano.

Fino al 18/09/2026 la finestra disegnava il sorgente — ``1. Scegli **una** cosa``
— mentre la WebUI renderizza (marked 15, ``gfm: true``, ``breaks: true``): la
stessa risposta si leggeva in due modi a seconda di dove la guardavi, e quello
sbagliato era proprio la finestra che serve a leggere al volo.

Adesso le sue bolle passano per Markwon (CommonMark reso in ``Spanned``, che è
ciò che una ``TextView`` sa disegnare) con gli `ext-*` di GFM. Le regole che
questo file tiene in piedi non le controlla nessun compilatore:

1. il markdown è **solo suo** — quello che l'utente ha scritto si mostra com'è;
2. le interruzioni di riga singole restano righe, come in chat;
3. i link non si aprono a caso, e non si linkificano i numeri di telefono;
4. il renderer porta i colori del tema, quindi muore quando il tema cambia.

Come i test-fratelli (``test_floating_chat_contract``), non c'è un runner
Kotlin: le asserzioni sono sul sorgente.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "android/app/src/main/java/com/flagdizero/jenny/FloatingOverlayController.kt"
GRADLE = ROOT / "android/app/build.gradle.kts"
CHAT_JS = ROOT / "jenny/templates/ui/assets/mobile-chat.js"


def _read() -> str:
    return CONTROLLER.read_text(encoding="utf-8")


def _fun(source: str, signature: str) -> str:
    """Il corpo di una funzione, da *signature* alla dichiarazione successiva."""
    start = source.find(signature)
    assert start >= 0, f"{signature} non è più leggibile"
    rest = source[start + len(signature):]
    ends = [
        m.start()
        for m in re.finditer(r"\n    (?:/\*\*|@|(?:private |internal )?fun |private val )", rest)
    ]
    return rest[: ends[0]] if ends else rest


class TestLaLibreria:
    def test_ci_sono_tutti_gli_artefatti(self):
        """Il `core` da solo è CommonMark: barrato, tabelle e task list — cioè
        il pezzo che il modello scrive davvero — stanno negli `ext-*`."""
        gradle = GRADLE.read_text(encoding="utf-8")
        for artifact in (
            "io.noties.markwon:core",
            "io.noties.markwon:ext-strikethrough",
            "io.noties.markwon:ext-tables",
            "io.noties.markwon:ext-tasklist",
            "io.noties.markwon:linkify",
            "io.noties.markwon:html",
        ):
            assert artifact in gradle, f"{artifact} non è più fra le dipendenze"


class TestIlMarkdownESoloSuo:
    def test_la_bolla_utente_resta_testo(self):
        """Se l'utente digita `*ciao*` intendeva gli asterischi. È anche quel
        che fa la SPA: `textContent` per la bolla utente, `renderMarkdown` per
        quella dell'assistente."""
        body = _fun(_read(), "private fun bubbleView(ctx: Context, line: Line): TextView")
        assert "if (line.mine) {" in body
        assert re.search(r"if \(line\.mine\) \{\s*\n\s*text = line\.text", body), (
            "la bolla utente non è più testo semplice: il markdown la reinterpreta"
        )
        assert "markdownRenderer(ctx).setMarkdown(this, line.text)" in body

    def test_la_spa_fa_lo_stesso(self):
        """La cucitura fra i due lati: se un giorno la SPA renderizzasse anche
        le bolle utente, questa regola andrebbe rivista insieme."""
        js = CHAT_JS.read_text(encoding="utf-8")
        assert "content.textContent = text;" in js, (
            "la SPA non riempie più la bolla utente con testo semplice: "
            "la finestra flottante la sta seguendo e va rivista con lei"
        )


class TestLeRigheSingole:
    def test_il_soft_break_segue_la_chat(self):
        """`breaks: true` in chat e `SoftBreakAddsNewLinePlugin` qui sono la
        stessa decisione. Senza, le sue liste scritte a righe singole si fondono
        in un paragrafo: è la differenza più visibile fra le due viste."""
        js = CHAT_JS.read_text(encoding="utf-8")
        breaks = re.search(r"breaks:\s*true", js)
        source = _read()
        if breaks:
            assert "SoftBreakAddsNewLinePlugin.create()" in source, (
                "la chat manda a capo le righe singole e la finestra no: "
                "la stessa risposta si legge in due modi"
            )
        else:
            assert "SoftBreakAddsNewLinePlugin.create()" not in source


class TestILink:
    def test_non_si_linkifica_tutto(self):
        """`Linkify.ALL` prende anche numeri e indirizzi: un promemoria con
        un'ora dentro diventerebbe un link al telefono."""
        source = _read()
        assert "LinkifyPlugin.create(Linkify.WEB_URLS or Linkify.EMAIL_ADDRESSES)" in source
        # Sulla *chiamata*, non sul testo: il commento lì accanto nomina
        # `Linkify.ALL` proprio per dire di non usarlo.
        assert "LinkifyPlugin.create(Linkify.ALL" not in source

    def test_l_intent_parte_da_un_contesto_applicativo(self):
        """Il resolver di Markwon farebbe `startActivity` con il contesto che
        gli si dà, e qui è quello applicativo: senza il flag solleva."""
        body = _fun(_read(), "private fun openLink(ctx: Context, url: String)")
        assert "Intent.FLAG_ACTIVITY_NEW_TASK" in body
        assert "collapse()" in body, (
            "la finestra resta aperta sopra il browser che ha appena aperto"
        )
        assert "catch" in body, "un link storto fa cadere l'overlay"

    def test_solo_gli_schemi_del_web(self):
        """Il testo delle bolle lo scrive il modello, e il modello legge pagine
        web: senza filtro, una pagina può indurlo a scrivere un link con uno
        schema d'app e il tocco lo consegna a chi lo dichiara. La WebUI quel
        link non lo mostra nemmeno (DOMPurify), e le due viste dello stesso
        testo non possono avere due soglie diverse."""
        source = _read()
        assert 'private val LINK_SCHEMES = setOf("http", "https", "mailto")' in source, (
            "l'elenco degli schemi permessi non c'è più: la bolla apre qualsiasi cosa"
        )
        body = _fun(source, "private fun openLink(ctx: Context, url: String)")
        assert "if (scheme !in LINK_SCHEMES) {" in body
        # Prima di `collapse()`: un link rifiutato non deve nemmeno far sparire
        # la conversazione.
        assert body.index("scheme !in LINK_SCHEMES") < body.index("collapse()")

    def test_il_resolver_e_agganciato(self):
        source = _read()
        assert "builder.linkResolver { _, link -> openLink(ctx, link) }" in source


class TestIlTema:
    def test_il_renderer_muore_col_tema(self):
        """I colori di codice, citazioni e link sono cotti dentro l'istanza:
        riusarla dopo un cambio tema li lascia vecchi dentro una bolla nuova."""
        body = _fun(_read(), "private fun applyPalette()")
        assert "markdown = null" in body
        source = _read()
        assert "private var markdown: Markwon? = null" in source

    def test_i_colori_vengono_dalla_palette(self):
        source = _read()
        for call in (
            "codeBlockBackgroundColor(",
            "blockQuoteColor(palette.accent)",
            "linkColor(palette.accent)",
            "tableBorderColor(palette.border)",
        ):
            assert call in source, f"{call} non c'è più: quel pezzo non segue il tema"
