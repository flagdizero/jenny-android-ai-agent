"""Il composer flottante tiene una **conversazione corta**, non un fumetto solo.

Fino al 18/09/2026 la finestra mostrava l'ultima risposta e basta: una
``TextView`` che ogni messaggio sovrascriveva. Adesso sopra la pillola c'è una
lista di bolle, e le regole che la governano sono quattro — nessuna delle quali
un compilatore può tenere in piedi da sé:

1. tiene gli **ultimi quattro scambi**, poi i più vecchi cadono;
2. si azzera **solo quando la chiudi tu**, e per questo il timer di inattività
   è sospeso finché ci sono bolle;
3. **non si riapre da sola**: una risposta che arriva a finestra chiusa resta
   nella conversazione dell'app;
4. il tuo messaggio sta dal lato dove lei **non** sta.

Come i test-fratelli (``test_floating_pill_contract``), non c'è un runner
Kotlin: le asserzioni sono sul sorgente.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "android/app/src/main/java/com/flagdizero/jenny/FloatingOverlayController.kt"
STRINGS_EN = ROOT / "android/app/src/main/res/values/strings.xml"
STRINGS_IT = ROOT / "android/app/src/main/res/values-it/strings.xml"


def _read() -> str:
    return CONTROLLER.read_text(encoding="utf-8")


def _fun(source: str, signature: str) -> str:
    """Il corpo di una funzione, da *signature* alla dichiarazione successiva.

    Non si ferma alla prima graffa chiusa a colonna 4: metà di queste funzioni
    hanno il corpo a espressione (``= TextView(ctx).apply { … }``), e un
    ritaglio che le manca fallisce senza dire perché.
    """
    start = source.find(signature)
    assert start >= 0, f"{signature} non è più leggibile"
    rest = source[start + len(signature):]
    ends = [
        m.start()
        for m in re.finditer(r"\n    (?:/\*\*|@|(?:private |internal )?fun |private val )", rest)
    ]
    return rest[: ends[0]] if ends else rest


class TestQuantoTiene:
    """Quattro scambi, e la potatura cade dalla testa."""

    def test_il_tetto_e_quattro_scambi(self):
        source = _read()
        m = re.search(r"const val HISTORY_MAX_TURNS\s*=\s*(\d+)", source)
        assert m, "HISTORY_MAX_TURNS non è più leggibile"
        assert m.group(1) == "4"

    def test_la_potatura_conta_due_bolle_per_scambio(self):
        """Il modello è piatto (una riga per bolla), quindi il tetto va
        moltiplicato: contarlo in righe terrebbe due scambi invece di quattro."""
        body = _fun(_read(), "private fun appendLine(mine: Boolean, text: String)")
        assert "history.size > HISTORY_MAX_TURNS * 2" in body
        assert "removeAt(0)" in body, "la potatura non cade più dalla testa"

    def test_dopo_un_messaggio_si_scende_in_fondo(self):
        body = _fun(_read(), "private fun appendLine(mine: Boolean, text: String)")
        assert "fullScroll(View.FOCUS_DOWN)" in body
        assert "historyScroll?.post" in body, (
            "lo scroll parte prima del layout: non sa ancora dove sia il fondo"
        )


class TestNonSiRiapreDaSola:
    """La regola che l'utente ha chiesto per prima."""

    def test_show_reply_esce_a_finestra_chiusa(self):
        source = _read()
        body = _fun(source, "fun showReply(text: String): Boolean")
        assert "if (!expanded" in body and "return false" in body
        assert "expand(" not in body, (
            "showReply riapre di nuovo la finestra da sola: è esattamente la "
            "cosa che una mascotte non deve fare sopra l'app di qualcun altro"
        )

    def test_la_risposta_entra_nella_conversazione(self):
        body = _fun(_read(), "fun showReply(text: String): Boolean")
        assert "appendLine(mine = false" in body


class TestSiAzzeraSoloChiudendo:
    def test_chiusura_e_volo_azzerano(self):
        source = _read()
        assert "clearHistory()" in _fun(source, "private fun collapse()")
        assert "clearHistory()" in _fun(source, "private fun startFlight(ctx: Context)")

    def test_nessun_altro_azzera(self):
        """Tre occorrenze e non una di più: la definizione e i due gesti. Se ne
        spunta una quarta, qualcosa azzera la conversazione senza che l'utente
        l'abbia chiesto."""
        source = _read()
        assert source.count("clearHistory()") == 3

    def test_il_timer_e_sospeso_finche_ci_sono_bolle(self):
        body = _fun(_read(), "private fun armHold()")
        assert "if (history.isNotEmpty()) return" in body
        # ...ma resta armato sul composer vuoto: un tocco per sbaglio non
        # deve lasciare un pannello sopra l'app.
        assert "main.postDelayed(holdRunnable, replyHoldMs)" in body


class TestIDueLati:
    def test_il_tuo_messaggio_sta_dove_lei_non_sta(self):
        source = _read()
        # `mine` a sinistra quando lei è a destra, e viceversa.
        assert source.count("if (line.mine) parkedRight else !parkedRight") == 2, (
            "la regola dei lati è cambiata o si è duplicata altrove"
        )
        body = _fun(source, "private fun renderHistory()")
        assert "if (atStart) Gravity.START else Gravity.END" in body

    def test_l_angolo_stretto_sta_dal_lato_di_chi_parla(self):
        body = _fun(_read(), "private fun bubbleView(ctx: Context, line: Line): TextView")
        assert "cornerRadii = corners" in body
        assert "BUBBLE_CORNER_DP" in _read()


class TestIlPassaggioAllApp:
    def test_il_chip_apre_la_chat(self):
        body = _fun(_read(), "private fun buildChip(ctx: Context): TextView")
        assert "openChat(ctx)" in body

    def test_l_etichetta_cambia_con_lo_stato(self):
        body = _fun(_read(), "private fun styleChip(ctx: Context, chip: TextView)")
        assert "R.string.floating_open_app" in body
        assert "R.string.floating_continue_app" in body

    def test_le_stringhe_esistono_in_tutte_e_due_le_lingue(self):
        for path in (STRINGS_EN, STRINGS_IT):
            text = path.read_text(encoding="utf-8")
            for name in ("floating_open_app", "floating_continue_app"):
                assert f'name="{name}"' in text, f"{name} manca in {path.name}"


class TestLaGeometria:
    def test_lo_spazio_di_lei_viene_dallo_sprite(self):
        """Non una costante: la sua parte visibile è fra i capelli e i piedi, e
        se cambia la taglia della mascotte questo si ricalcola da sé."""
        body = _fun(_read(), "private fun standHeight(ctx: Context): Int")
        assert "FEET_RATIO - HEAD_RATIO" in body
        assert "CHIP_GAP_DP" in body, "a conversazione vuota il chip non scende più"

    def test_il_tetto_della_lista_e_lo_spazio_che_resta(self):
        """Non un numero: la lista arriva fin dove c'è posto, e il posto cambia
        quando la pillola cresce o la tastiera si alza. Un tetto fisso
        spingerebbe le bolle fuori dal bordo alto, dove la colonna cresce e non
        c'è nessuno a fermarle."""
        source = _read()
        body = _fun(source, "private fun syncListCap(ctx: Context)")
        assert "chatBottomInsetPx" in body
        assert "LIST_TOP_MARGIN_DP" in body
        assert "LIST_MAX_DP" not in source, "è tornato un tetto fisso in dp"

    def test_le_bolle_stanno_piu_larghe_della_pillola(self):
        """La conversazione vuole i bordi; la pillola resta al suo 62%."""
        source = _read()
        assert re.search(
            r"private fun listWidth\(ctx: Context\): Int = screenWidth\(ctx\) - 2 \* dp\(ctx, LIST_EDGE_DP\)",
            source,
        ), "la lista non è più larga dello schermo meno il suo margine"
        body = _fun(source, "private fun bubbleView(ctx: Context, line: Line): TextView")
        assert "listWidth(ctx) * BUBBLE_MAX_RATIO" in body, (
            "la bolla torna a misurarsi sulla pillola, che è molto più stretta"
        )

    def test_la_colonna_e_verticale(self):
        """Lista, lo spazio di lei, pillola — e ancorata in basso, così la
        pillola non si muove di un pixel quando arriva un messaggio."""
        body = _fun(_read(), "private fun buildInputRow(ctx: Context): View")
        assert "orientation = LinearLayout.VERTICAL" in body
        assert "Gravity.CENTER_HORIZONTAL or Gravity.BOTTOM" in body

    def test_il_fumetto_singolo_non_c_e_piu(self):
        source = _read()
        assert "private var bubble:" not in source
        assert "bubbleBackground" not in source
