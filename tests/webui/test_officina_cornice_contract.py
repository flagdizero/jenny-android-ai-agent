"""La cornice dell'officina: intestazione dei cassetti e barra con le etichette.

Il 20/09/2026 l'officina sul telefono apriva ogni cassetto su **quattro righe
chiuse e nient'altro**: nessun titolo, nessuna riga che dicesse a cosa serve, e
in fondo quattro icone nude — un fiore, un cervello, una mano, un cilindro —
senza un nome sotto.

La macchina dell'intestazione c'era gia' e funzionava. Il difetto era una
tabella mancante: ``ViewTitleController._mount`` cercava ``title-<modo>``, e i
tre cassetti (``cervello``, ``mani``, ``memoria``) condividono **una vista
sola**, il cui mount si chiama ``title-settings``. Nessun mount, ``setMode``
usciva subito, e l'intestazione non si disegnava — silenziosamente, che e' il
modo peggiore.

I banchi qui sotto difendono la cornice da tre lati: che ogni cassetto abbia le
sue tre stringhe **in tutte e due le lingue**, che la barra porti i nomi, e che
il tasto per tornare in casa stia in **un posto solo**.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"

HEADER = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")
SETTINGS = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
OFFICINA = (UI / "officina.html").read_text(encoding="utf-8")
CSS = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")

CASSETTI = ("cervello", "mani", "memoria")
LINGUE = ("it", "en")


def _i18n(lingua: str) -> dict:
    return json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))


# ── Le stringhe ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lingua", LINGUE)
@pytest.mark.parametrize("cassetto", CASSETTI)
def test_ogni_cassetto_ha_nome_e_sottotitolo(lingua: str, cassetto: str) -> None:
    """Le tre stringhe che compongono l'intestazione, in tutte e due le lingue.

    Una chiave che manca non rompe niente: `i18n.t` restituisce la chiave
    grezza, e a schermo compare «officina.sub.mani». E' cosi' che una
    traduzione mancante si presenta, ed e' indistinguibile da un difetto.
    """
    d = _i18n(lingua)
    assert d["nav"][cassetto].strip(), f"{lingua}: nav.{cassetto} vuoto"
    sub = d["officina"]["sub"][cassetto]
    assert sub.strip(), f"{lingua}: officina.sub.{cassetto} vuoto"
    # Una soprascritta che e' anche il sottotitolo vuol dire che qualcuno ha
    # riempito la riga per far passare il banco.
    assert sub != d["officina"]["eyebrow"]


@pytest.mark.parametrize("lingua", LINGUE)
def test_la_soprascritta_e_il_pill_esistono(lingua: str) -> None:
    d = _i18n(lingua)["officina"]
    assert d["eyebrow"].strip()
    assert d["casaPill"].strip()


def test_i_sottotitoli_dicono_cose_diverse() -> None:
    """Tre cassetti, tre righe diverse — in ogni lingua.

    Copiare la stessa riga sotto i tre nomi soddisfa il banco di sopra e non
    aiuta nessuno: la riga serve a distinguere i cassetti, non a riempire uno
    spazio.
    """
    for lingua in LINGUE:
        subs = _i18n(lingua)["officina"]["sub"]
        assert len(set(subs.values())) == len(CASSETTI), f"{lingua}: sottotitoli ripetuti"


# ── L'aggancio che mancava ───────────────────────────────────────────────────


def test_la_tabella_delle_viste_e_una_sola() -> None:
    """`VISTA_DI` sta accanto a `CASSETTI` ed e' importata, non ricopiata.

    Era dichiarata in `mobile-app.js` e serviva anche a `mobile-header.js`, che
    non ce l'aveva: e' esattamente la copia mancante che ha lasciato i cassetti
    senza intestazione.
    """
    assert "export const VISTA_DI" in SETTINGS, "VISTA_DI non e' piu' in mobile-settings.js"
    assert re.search(r"import \{[^}]*VISTA_DI[^}]*\} from '\./mobile-settings\.js'", HEADER), (
        "mobile-header.js non importa VISTA_DI: `_mount` tornerebbe a cercare "
        "`title-cervello`, che non esiste"
    )
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    assert "const VISTA_DI = {" not in app, "mobile-app.js ha di nuovo una copia sua"


def test_il_mount_passa_dalla_tabella() -> None:
    """La riga che traduce il modo nel suo mount.

    Si legge il corpo di `_mount`: senza `VISTA_DI` li' dentro, i tre cassetti
    non trovano `title-settings` e `setMode` esce prima di disegnare.
    """
    m = re.search(r"_mount\(mode\)\s*\{(.*?)\}", HEADER, re.S)
    assert m, "_mount non trovato"
    assert "VISTA_DI" in m.group(1), f"_mount non consulta la tabella: {m.group(1).strip()}"


@pytest.mark.parametrize("cassetto", CASSETTI)
def test_ogni_cassetto_ha_una_intestazione(cassetto: str) -> None:
    """Il cassetto compare fra le viste che sanno disegnarsi un'intestazione."""
    m = re.search(r"this\.modeConfigs\s*=\s*\{(.*?)\n    \};", HEADER, re.S)
    assert m, "modeConfigs non trovato"
    assert re.search(rf"\b{cassetto}\s*:", m.group(1)), (
        f"{cassetto} non ha una voce in modeConfigs: resterebbe senza titolo"
    )


def test_la_soprascritta_e_il_sottotitolo_finiscono_nel_dom() -> None:
    """Non basta dichiararli: `setMode` deve anche scriverli."""
    for classe in ("view-title-eyebrow", "view-title-sub"):
        assert classe in HEADER, f"{classe} non e' disegnata da mobile-header.js"
        assert classe in CSS, f"{classe} non ha stile: sarebbe testo nudo"


def test_il_cambio_lingua_rifa_anche_i_cassetti() -> None:
    """Tre stringhe a testa: se il refresh ne dimentica una resta in italiano."""
    m = re.search(r"_refreshTitles\(\)\s*\{(.*?)\n  \}", HEADER, re.S)
    assert m, "_refreshTitles non trovato"
    assert "VISTA_DI" in m.group(1), (
        "_refreshTitles non ricostruisce i cassetti: al cambio lingua "
        "l'intestazione resta nella lingua di prima"
    )


# ── La barra in fondo ────────────────────────────────────────────────────────


def _voci_dock() -> list[re.Match]:
    return list(re.finditer(r'<div class="dock-item[^"]*"([^>]*)>(.*?)</div>', OFFICINA))


@pytest.mark.parametrize("modo", ("chat", "cervello", "mani", "memoria"))
def test_ogni_voce_visibile_del_dock_ha_il_suo_nome(modo: str) -> None:
    """Icona **e** parola.

    `title=` non conta: su un telefono non esiste il passaggio del mouse, quindi
    un `title` e' visibile a nessuno. Era gia' cosi' per tutte e quattro.
    """
    voci = [m for m in _voci_dock() if f'data-mode="{modo}"' in m.group(1)]
    assert len(voci) == 1, f"{modo}: {len(voci)} voci nel dock"
    corpo = voci[0].group(2)
    assert 'class="dock-label"' in corpo, f"{modo} non ha etichetta visibile"
    assert f'data-i18n="nav.{modo if modo != "chat" else "console"}"' in corpo, (
        f"{modo}: l'etichetta non e' tradotta"
    )


def test_la_voce_attiva_si_distingue_anche_senza_colore() -> None:
    """Il puntino.

    Su Chanel e Fumetto `--accent` **e'** il colore del testo: attivo e inattivo
    differirebbero per una sfumatura di grigio. Il puntino e' una differenza di
    forma, che sopravvive a qualunque tema.
    """
    assert re.search(r"\.dock-item\.active::(after|before)\s*\{", CSS), (
        "nessun indicatore di forma sulla voce attiva"
    )


def test_le_etichette_hanno_uno_stile() -> None:
    assert ".dock-label {" in CSS, "dock-label senza stile: erediterebbe il corpo del testo"


# ── Un posto solo per tornare a casa ─────────────────────────────────────────


def test_tornare_in_casa_sta_in_un_posto_solo() -> None:
    """La porta per la casa e' nell'intestazione, e **non** anche in Sistema.

    E' la stessa regola dei cinque passi precedenti: se ce l'ha la cornice,
    il cassetto non lo rifa'. Due tasti per la stessa destinazione, uno in cima
    e uno in fondo a una pagina lunga, sono due modi di non trovarne nessuno.
    """
    assert "btn-open-casa" not in SETTINGS, (
        "il tasto «Torna alla casa» e' tornato dentro le impostazioni: adesso "
        "e' il pill dell'intestazione"
    )
    assert "_renderOpenCasa" not in SETTINGS
    assert "'go-casa'" in HEADER, "l'intestazione non porta piu' in casa"
    assert "/html-mobile/index.html" in HEADER


def test_il_pill_porta_una_parola_e_non_solo_una_icona() -> None:
    """Una casetta puo' voler dire home, casa o indietro. «Jenny» no."""
    assert "ibtn-pill" in HEADER, "l'azione verso la casa non e' un pill"
    assert ".ibtn-pill {" in CSS, "ibtn-pill senza stile: sarebbe un quadrato da 36px"


# ── Le schede aperte ─────────────────────────────────────────────────────────


def test_niente_piu_fisarmoniche() -> None:
    """Nessuna sezione che si apre e si chiude, in nessuna forma.

    Era il difetto piu' grosso: un cassetto si apriva su quattro teste chiuse e
    per sapere cosa c'era dentro bisognava toccarle una per una. La tavola non
    ha nessuna fisarmonica, e la pagina si legge scorrendo.

    Si controlla il vocabolario intero — la classe, la testa, il chevron, lo
    stato — perche' reintrodurne *uno* basta a far tornare il difetto.
    """
    for parola in ("settings-section", "settings-chevron", "_openSections"):
        assert parola not in SETTINGS, f"la fisarmonica e' tornata in mobile-settings.js: {parola}"
        assert parola not in CSS, f"la fisarmonica e' tornata nel foglio di stile: {parola}"


def test_il_gruppo_e_una_scheda_aperta() -> None:
    """Il mattone che l'ha sostituita: soprascritta fuori, scheda dentro."""
    m = re.search(r"_gruppo\(id, etichetta, corpo\)\s*\{(.*?)\n  \}", SETTINGS, re.S)
    assert m, "_gruppo non trovato"
    corpo = m.group(1)
    assert "settings-gruppo-label" in corpo, "il gruppo non ha soprascritta"
    assert "settings-card" in corpo, "il gruppo non ha una scheda"
    assert "chevron" not in corpo and "collapsed" not in corpo, "il gruppo si richiude"
    # La **regola** della classe, non una sua comparsa qualunque: `.settings-card`
    # compare anche in due selettori discendenti (`.settings-card .tstrip`), e un
    # controllo che accettasse quelli restava verde con la scheda senza stile —
    # misurato mutando `.settings-card {` in `.settings-carta {`.
    for classe in ("settings-gruppo", "settings-gruppo-label", "settings-card"):
        assert re.search(rf"^\.{classe} \{{", CSS, re.M), f"{classe} non ha una regola sua"


def test_le_pezze_della_fisarmonica_se_ne_vanno_con_lei() -> None:
    """Due sezioni si aprivano d'ufficio, ognuna con la stessa nota in commento:
    «un accordion chiuso e' esattamente il posto in cui il problema e' rimasto
    invisibile». Erano pezze su un difetto, non funzioni: senza fisarmoniche
    non c'e' piu' niente da forzare, e lasciarle sarebbe codice che non fa
    niente ma sembra fare qualcosa.
    """
    assert "_cronAutoOpened" not in SETTINGS, "la pezza del cron e' rimasta"
    assert "batteryExemptionNeeded()" not in SETTINGS or "_openSections" not in SETTINGS


def test_ogni_gruppo_ha_ancora_un_id_nel_dom() -> None:
    """`data-gruppo` non serve piu' a ricordare chi e' aperto, ma serve a chi
    cerca un gruppo nel DOM — il banco, e i caricatori asincroni che scrivono
    nel proprio segnaposto."""
    assert 'data-gruppo="${id}"' in SETTINGS


# ── Il ritaglio ──────────────────────────────────────────────────────────────


def _gruppi_dichiarati() -> dict[str, list[str]]:
    """`CASSETTI` letto dal sorgente: cassetto -> gruppi, nell'ordine."""
    m = re.search(r"export const CASSETTI = \{(.*?)\n\};", SETTINGS, re.S)
    assert m, "CASSETTI non trovato"
    out = {}
    for c in CASSETTI:
        b = re.search(rf"{c}: \{{\s*sezioni: \[(.*?)\]", m.group(1), re.S)
        assert b, f"{c} non ha una voce"
        out[c] = re.findall(r"'([A-Za-z]+)'", b.group(1))
    return out


def _gruppi_disegnabili() -> set[str]:
    """Le chiavi della mappa dentro `render()`."""
    m = re.search(r"const sezioni = \{(.*?)\n    \};", SETTINGS, re.S)
    assert m, "la mappa dei gruppi non si trova"
    return set(re.findall(r"^      ([A-Za-z]+):", m.group(1), re.M))


def test_ogni_gruppo_di_un_cassetto_si_sa_disegnare() -> None:
    """Un id nella tabella senza il suo disegnatore e' un `TypeError`.

    `render()` fa `quali.map((id) => sezioni[id]())`: un id che la mappa non
    conosce e' `undefined()`, cioe' la schermata intera che non si apre. Il
    file resta valido, la suite verde, e il difetto arriva sul telefono — la
    stessa famiglia dei metodi fantasma (v. test_no_ghost_methods_contract).
    """
    disegnabili = _gruppi_disegnabili()
    for cassetto, gruppi in _gruppi_dichiarati().items():
        mancanti = [g for g in gruppi if g not in disegnabili]
        assert not mancanti, f"{cassetto}: gruppi senza disegnatore {mancanti}"


def test_nessun_gruppo_disegnabile_resta_orfano() -> None:
    """E il contrario: un disegnatore che nessun cassetto usa e' codice morto."""
    usati = {g for gruppi in _gruppi_dichiarati().values() for g in gruppi}
    orfani = _gruppi_disegnabili() - usati
    assert not orfani, f"gruppi che nessun cassetto mostra: {sorted(orfani)}"


def test_un_gruppo_sta_in_un_cassetto_solo() -> None:
    """Due cassetti che mostrano lo stesso gruppo sono due copie che invecchiano
    separatamente — ed e' esattamente il difetto da cui il giro dei cassetti e'
    partito."""
    visti: dict[str, str] = {}
    for cassetto, gruppi in _gruppi_dichiarati().items():
        for g in gruppi:
            assert g not in visti, f"{g} sta sia in {visti[g]} sia in {cassetto}"
            visti[g] = cassetto


@pytest.mark.parametrize("lingua", LINGUE)
def test_le_soprascritte_nuove_sono_tradotte(lingua: str) -> None:
    """I cinque gruppi che il ritaglio ha creato hanno un nome vero."""
    gruppi = _i18n(lingua)["officina"]["gruppi"]
    for chiave in ("chiPensa", "parametri", "quantoRicorda", "dream", "giardiniere"):
        assert gruppi.get(chiave, "").strip(), f"{lingua}: officina.gruppi.{chiave} manca"


def test_il_taglio_fine_e_arrivato() -> None:
    """La misura del ritaglio: undici sezioni sono diventate almeno quindici
    gruppi, e le tre grandi si sono spezzate.

    `_renderModelSettings`, `_renderTools` e `_renderMemory` tenevano insieme
    cose che la tavola separa; se uno di quei nomi ricompare, qualcuno ha
    rimesso insieme quel che il ritaglio aveva diviso.
    """
    for vecchio in ("_renderModelSettings", "_renderTools(", "_renderMemory("):
        assert vecchio not in SETTINGS, f"{vecchio} e' tornato: il ritaglio si e' richiuso"
    totale = sum(len(g) for g in _gruppi_dichiarati().values())
    assert totale >= 15, f"solo {totale} gruppi: il taglio fine non c'e'"
