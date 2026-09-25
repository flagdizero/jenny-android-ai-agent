"""La cornice dell'officina: intestazione dei cassetti e barra con le etichette.

Il 20/09/2026 l'officina sul telefono apriva ogni cassetto su **quattro righe
chiuse e nient'altro**: nessun titolo, nessuna riga che dicesse a cosa serve, e
in fondo quattro icone nude — un fiore, un cervello, una mano, un cilindro —
senza un nome sotto.

La macchina dell'intestazione c'era gia' e funzionava. Il difetto era una
tabella mancante: ``ViewTitleController._mount`` cercava ``title-<modo>``, e i
tre cassetti (``brain``, ``hands``, ``memory``) condividono **una vista
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
WORKSHOP = (UI / "workshop.html").read_text(encoding="utf-8")
CSS = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")

DRAWERS = ("brain", "hands", "memory")
LINGUE = ("it", "en")


def _i18n(lingua: str) -> dict:
    return json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))


# ── Le stringhe ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lingua", LINGUE)
@pytest.mark.parametrize("drawer", DRAWERS)
def test_ogni_cassetto_ha_nome_e_sottotitolo(lingua: str, drawer: str) -> None:
    """Le tre stringhe che compongono l'intestazione, in tutte e due le lingue.

    Una chiave che manca non rompe niente: `i18n.t` restituisce la chiave
    grezza, e a schermo compare «officina.sub.mani». E' cosi' che una
    traduzione mancante si presenta, ed e' indistinguibile da un difetto.
    """
    d = _i18n(lingua)
    assert d["nav"][drawer].strip(), f"{lingua}: nav.{drawer} vuoto"
    sub = d["workshop"]["sub"][drawer]
    assert sub.strip(), f"{lingua}: officina.sub.{drawer} vuoto"
    # Una soprascritta che e' anche il sottotitolo vuol dire che qualcuno ha
    # riempito la riga per far passare il banco.
    assert sub != d["workshop"]["eyebrow"]


@pytest.mark.parametrize("lingua", LINGUE)
def test_la_soprascritta_e_il_pill_esistono(lingua: str) -> None:
    d = _i18n(lingua)["workshop"]
    assert d["eyebrow"].strip()
    assert d["homePill"].strip()


def test_i_sottotitoli_dicono_cose_diverse() -> None:
    """Tre cassetti, tre righe diverse — in ogni lingua.

    Copiare la stessa riga sotto i tre nomi soddisfa il banco di sopra e non
    aiuta nessuno: la riga serve a distinguere i cassetti, non a riempire uno
    spazio.
    """
    for lingua in LINGUE:
        subs = _i18n(lingua)["workshop"]["sub"]
        assert len(set(subs.values())) == len(DRAWERS), f"{lingua}: sottotitoli ripetuti"


# ── L'aggancio che mancava ───────────────────────────────────────────────────


def test_la_tabella_delle_viste_e_una_sola() -> None:
    """`VIEW_OF` sta accanto a `DRAWERS` ed e' importata, non ricopiata.

    Era dichiarata in `mobile-app.js` e serviva anche a `mobile-header.js`, che
    non ce l'aveva: e' esattamente la copia mancante che ha lasciato i cassetti
    senza intestazione.
    """
    assert "export const VIEW_OF" in SETTINGS, "VIEW_OF non e' piu' in mobile-settings.js"
    assert re.search(r"import \{[^}]*VIEW_OF[^}]*\} from '\./mobile-settings\.js'", HEADER), (
        "mobile-header.js non importa VIEW_OF: `_mount` tornerebbe a cercare "
        "`title-cervello`, che non esiste"
    )
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    assert "const VIEW_OF = {" not in app, "mobile-app.js ha di nuovo una copia sua"


def test_il_mount_passa_dalla_tabella() -> None:
    """La riga che traduce il modo nel suo mount.

    Si legge il corpo di `_mount`: senza la tabella li' dentro, i tre cassetti
    non trovano `title-settings` e `setMode` esce prima di disegnare.

    **Due forme valgono**, e la seconda e' la piu' forte. Il 22/09/2026 la
    traduzione e' diventata una funzione — `titleElement(mode)`, accanto a
    `VIEW_OF` — perche' lo stesso errore era gia' uscito tre volte: qui, e due
    volte nel carosello di `mobile-app.js`, dove aveva ucciso lo scorrimento su
    tre linguette su quattro. Passare dalla funzione soddisfa questo banco
    **meglio** che consultare la tabella a mano, ed e' difeso a parte da
    `test_no_raw_view_lookup_contract.py`. Quel che resta vietato — costruire
    `title-${mode}` da se' — e' vietato in tutte e due le forme.
    """
    m = re.search(r"_mount\(mode\)\s*\{(.*?)\}", HEADER, re.S)
    assert m, "_mount non trovato"
    body = m.group(1)
    assert "VIEW_OF" in body or "titleElement" in body, (
        f"_mount non passa ne' dalla tabella ne' da titleElement(): {body.strip()}"
    )


@pytest.mark.parametrize("drawer", DRAWERS)
def test_ogni_cassetto_ha_una_intestazione(drawer: str) -> None:
    """Il cassetto compare fra le viste che sanno disegnarsi un'intestazione."""
    m = re.search(r"this\.modeConfigs\s*=\s*\{(.*?)\n    \};", HEADER, re.S)
    assert m, "modeConfigs non trovato"
    assert re.search(rf"\b{drawer}\s*:", m.group(1)), (
        f"{drawer} non ha una voce in modeConfigs: resterebbe senza titolo"
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
    assert "VIEW_OF" in m.group(1), (
        "_refreshTitles non ricostruisce i cassetti: al cambio lingua "
        "l'intestazione resta nella lingua di prima"
    )


# ── La barra in fondo ────────────────────────────────────────────────────────


def _voci_dock() -> list[re.Match]:
    return list(re.finditer(r'<div class="dock-item[^"]*"([^>]*)>(.*?)</div>', WORKSHOP))


@pytest.mark.parametrize("modo", ("chat", "brain", "hands", "memory"))
def test_ogni_voce_visibile_del_dock_ha_il_suo_nome(modo: str) -> None:
    """Icona **e** parola.

    `title=` non conta: su un telefono non esiste il passaggio del mouse, quindi
    un `title` e' visibile a nessuno. Era gia' cosi' per tutte e quattro.
    """
    entries = [m for m in _voci_dock() if f'data-mode="{modo}"' in m.group(1)]
    assert len(entries) == 1, f"{modo}: {len(entries)} voci nel dock"
    body = entries[0].group(2)
    assert 'class="dock-label"' in body, f"{modo} non ha etichetta visibile"
    assert f'data-i18n="nav.{modo if modo != "chat" else "console"}"' in body, (
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
    assert "'go-home'" in HEADER, "l'intestazione non porta piu' in casa"
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
    for word in ("settings-section", "settings-chevron", "_openSections"):
        assert word not in SETTINGS, f"la fisarmonica e' tornata in mobile-settings.js: {word}"
        assert word not in CSS, f"la fisarmonica e' tornata nel foglio di stile: {word}"


def test_il_gruppo_e_una_scheda_aperta() -> None:
    """Il mattone che l'ha sostituita: soprascritta fuori, scheda dentro."""
    # `porte` è arrivato dopo: le destinazioni stanno **dentro** la scheda, come
    # sua ultima riga. Concatenarle fuori le lasciava fluttuare fra due gruppi.
    m = re.search(r"_group\(id, label, body(?:, porte = '')?\)\s*\{(.*?)\n  \}", SETTINGS, re.S)
    assert m, "_gruppo non trovato"
    body = m.group(1)
    assert "settings-group-label" in body, "il gruppo non ha soprascritta"
    assert "settings-card" in body, "il gruppo non ha una scheda"
    assert "chevron" not in body and "collapsed" not in body, "il gruppo si richiude"
    # La **regola** della classe, non una sua comparsa qualunque: `.settings-card`
    # compare anche in due selettori discendenti (`.settings-card .tstrip`), e un
    # controllo che accettasse quelli restava verde con la scheda senza stile —
    # misurato mutando `.settings-card {` in `.settings-carta {`.
    for classe in ("settings-group", "settings-group-label", "settings-card"):
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
    """`data-group` non serve piu' a ricordare chi e' aperto, ma serve a chi
    cerca un gruppo nel DOM — il banco, e i caricatori asincroni che scrivono
    nel proprio segnaposto."""
    assert 'data-group="${id}"' in SETTINGS


# ── Il ritaglio ──────────────────────────────────────────────────────────────


def _gruppi_dichiarati() -> dict[str, list[str]]:
    """`DRAWERS` letto dal sorgente: cassetto -> gruppi, nell'ordine."""
    m = re.search(r"export const DRAWERS = \{(.*?)\n\};", SETTINGS, re.S)
    assert m, "CASSETTI non trovato"
    out = {}
    for c in DRAWERS:
        b = re.search(rf"{c}: \{{\s*sections: \[(.*?)\]", m.group(1), re.S)
        assert b, f"{c} non ha una voce"
        out[c] = re.findall(r"'([A-Za-z]+)'", b.group(1))
    return out


def _gruppi_disegnabili() -> set[str]:
    """Le chiavi della mappa dentro `render()`."""
    m = re.search(r"const sections = \{(.*?)\n    \};", SETTINGS, re.S)
    assert m, "la mappa dei gruppi non si trova"
    return set(re.findall(r"^      ([A-Za-z]+):", m.group(1), re.M))


def test_ogni_gruppo_di_un_cassetto_si_sa_disegnare() -> None:
    """Un id nella tabella senza il suo disegnatore e' un `TypeError`.

    `render()` fa `which.map((id) => sections[id]())`: un id che la mappa non
    conosce e' `undefined()`, cioe' la schermata intera che non si apre. Il
    file resta valido, la suite verde, e il difetto arriva sul telefono — la
    stessa famiglia dei metodi fantasma (v. test_no_ghost_methods_contract).
    """
    disegnabili = _gruppi_disegnabili()
    for drawer, groups in _gruppi_dichiarati().items():
        missing = [g for g in groups if g not in disegnabili]
        assert not missing, f"{drawer}: gruppi senza disegnatore {missing}"


def test_nessun_gruppo_disegnabile_resta_orfano() -> None:
    """E il contrario: un disegnatore che nessun cassetto usa e' codice morto."""
    usati = {g for groups in _gruppi_dichiarati().values() for g in groups}
    orfani = _gruppi_disegnabili() - usati
    assert not orfani, f"gruppi che nessun cassetto mostra: {sorted(orfani)}"


def test_un_gruppo_sta_in_un_cassetto_solo() -> None:
    """Due cassetti che mostrano lo stesso gruppo sono due copie che invecchiano
    separatamente — ed e' esattamente il difetto da cui il giro dei cassetti e'
    partito."""
    seen: dict[str, str] = {}
    for drawer, groups in _gruppi_dichiarati().items():
        for g in groups:
            assert g not in seen, f"{g} sta sia in {seen[g]} sia in {drawer}"
            seen[g] = drawer


@pytest.mark.parametrize("lingua", LINGUE)
def test_le_soprascritte_nuove_sono_tradotte(lingua: str) -> None:
    """I cinque gruppi che il ritaglio ha creato hanno un nome vero."""
    groups = _i18n(lingua)["workshop"]["groups"]
    for key in ("whoThinks", "parameters", "howMuchItRemembers", "dream", "gardener"):
        assert groups.get(key, "").strip(), f"{lingua}: officina.groups.{key} manca"


def test_il_taglio_fine_e_arrivato() -> None:
    """La misura del ritaglio: undici sezioni sono diventate quattordici
    gruppi, e le tre grandi si sono spezzate.

    `_renderModelSettings`, `_renderTools` e `_renderMemory` tenevano insieme
    cose che la tavola separa; se uno di quei nomi ricompare, qualcuno ha
    rimesso insieme quel che il ritaglio aveva diviso.

    **Erano quindici fino al 21/09/2026.** Il gruppo che manca e'
    `personalization`, e non e' un pezzo di ritaglio andato perso: non stava in
    nessuna tavola, era uno dei due parcheggi dichiarati, e delle sue quattro
    voci tre vivevano gia' in casa (temi, mascotte, finestra flottante). Il
    nome di Jenny e' andato nella stanza «Jenny» con loro, e la lingua se n'e'
    andata con l'ultimo interruttore che la cambiava. Quindi il conto sceso di
    uno **e'** il taglio, non un suo cedimento — e il controllo qui sotto dice
    proprio quello: personalizzazione in officina non deve tornare.
    """
    for old in ("_renderModelSettings", "_renderTools(", "_renderMemory("):
        assert old not in SETTINGS, f"{old} e' tornato: il ritaglio si e' richiuso"
    dichiarati = _gruppi_dichiarati()
    totale = sum(len(g) for g in dichiarati.values())
    assert totale >= 14, f"solo {totale} gruppi: il taglio fine non c'e'"
    all = {g for groups in dichiarati.values() for g in groups}
    assert "personalization" not in all, (
        "la personalizzazione e' tornata in officina: temi, mascotte e nome "
        "stanno in casa, e tenerli in due posti vuol dire tenerli allineati"
    )
    for morto in ("_renderPersonalization", "_renderTheme(", "_renderMascot(",
                  "_renderHomeView(", "_renderLanguage("):
        assert morto not in SETTINGS, f"{morto} e' tornato in officina"


# ── L'intestazione della Console ─────────────────────────────────────────────
#
# Fino al 21/09/2026 la chat dell'officina era l'unica vista a partire dal
# bordo dello schermo: nessun titolo, e — visto che il tasto per tornare in
# casa vive nell'intestazione — nessuna porta verso casa. Adesso ha la stessa
# intestazione della casa, con due differenze volute: nessuna soprascritta, e
# il nome e' «Console» invece di «Jenny».


def test_la_console_ha_il_suo_mount() -> None:
    """Senza mount `setMode` esce in silenzio — e' il difetto da cui e' nato
    questo file, ripetuto su un'altra vista."""
    assert 'id="title-chat"' in WORKSHOP, "la vista chat non ha un mount per il titolo"
    head = WORKSHOP.split('id="view-chat"', 1)[1]
    mount = head.index('id="title-chat"')
    area = head.index('id="chat-area"')
    assert mount < area, "il mount non sta in cima alla vista: il titolo finirebbe sotto la chat"


def test_la_console_ha_una_voce_in_modeconfigs() -> None:
    m = re.search(r"this\.modeConfigs\s*=\s*\{(.*?)\n    \};", HEADER, re.S)
    assert m, "modeConfigs non trovato"
    assert re.search(r"\bchat\s*:", m.group(1)), (
        "la chat non ha una voce in modeConfigs: il mount resterebbe vuoto"
    )


def _corpo_consolle() -> str:
    """Il corpo della fabbrica che disegna l'intestazione della Console."""
    m = re.search(r"function consoleConfig\(\)\s*\{(.*?)\n\}", HEADER, re.S)
    assert m, "consoleConfig() non trovata: la Console non ha piu' un'intestazione sua"
    return m.group(1)


def test_il_titolo_della_console_e_la_parola_del_dock() -> None:
    """Una parola sola per due posti.

    L'etichetta in fondo e il titolo in cima dicono la stessa cosa della stessa
    vista: due chiavi diverse sono due traduzioni che divergono al primo giro.
    """
    assert "i18n.t('nav.console')" in _corpo_consolle(), (
        "il titolo della console non viene da `nav.console`, che e' la stessa "
        "stringa dell'etichetta nel dock"
    )
    for lingua in LINGUE:
        assert _i18n(lingua)["nav"]["console"].strip(), f"{lingua}: nav.console vuoto"


def test_la_console_non_ha_soprascritta() -> None:
    """La differenza chiesta rispetto alla casa.

    In casa sopra il nome c'e' «conversazione personale». Qui no: la vista sta
    gia' dentro l'officina, e `setMode` nasconde la riga quando manca — quindi
    la si omette invece di riempirla.
    """
    body = _corpo_consolle()
    assert "eyebrow" not in body, "la console ha una soprascritta: non deve averla"
    assert "sub:" not in body, "la console ha un sottotitolo: non deve averlo"


def test_dalla_console_si_torna_in_casa() -> None:
    """Il pill «Jenny», come negli altri tre cassetti.

    E' la porta che a questa vista mancava del tutto: l'unica per la casa sta
    nell'intestazione, e la chat non ne aveva una.
    """
    assert "homePill()" in _corpo_consolle(), "dalla console non si torna in casa"


def test_il_pill_e_definito_una_volta_sola() -> None:
    """Quattro intestazioni, una definizione.

    Il pill era ricopiato a mano in `drawer()` e nella Console: due copie
    della stessa riga con due stringhe dentro, che e' il modo in cui una delle
    due resta indietro.
    """
    assert "function homePill()" in HEADER, "il pill non ha piu' una definizione sua"
    assert HEADER.count("i18n.t('workshop.homePill')") == 1, (
        "la stringa del pill compare piu' di una volta: e' tornata a essere copiata"
    )


def test_il_titolo_della_console_resta_su_mentre_la_chat_scorre() -> None:
    """In chat a scorrere e' il **documento**, non un riquadro interno.

    Le altre viste dell'officina tengono il titolo su da sole, perche' il loro
    mount sta in una colonna alta quanto lo schermo. Qui no: senza `sticky` il
    titolo se ne va al primo dito, e in casa — dov'e' nato — non se ne va mai.
    """
    m = re.search(r"#title-chat\s*\{([^}]*)\}", CSS)
    assert m, "#title-chat non ha stile: il titolo scorrerebbe via"
    regola = m.group(1)
    assert "position: sticky" in regola, "il titolo della console non e' appiccicato"
    assert "safe-area-inset-top" in regola, (
        "`top` non tiene conto della status bar: il titolo ci finirebbe sotto"
    )
    assert "background" in regola, "senza sfondo la chat scorre attraverso il titolo"


def test_una_intestazione_col_pill_si_rifa_intera() -> None:
    """**Il difetto vero, visto a schermo il 21/09/2026.**

    La prima versione di questa intestazione riassegnava il solo `title` al
    cambio lingua. Il resto della voce restava quello costruito **al
    caricamento del file**, quando le traduzioni non ci sono ancora — e nel
    bottone c'era scritto, per esteso, `workshop.homePill`.

    Perche' solo li'. Ogni intestazione dell'officina ha azioni con una
    stringa dentro, ma quella stringa e' quasi sempre un `title=`, cioe' un
    suggerimento che su un telefono non legge nessuno. Il pill e' l'unica
    azione che porta una **parola visibile**: e' l'unico posto dove una
    traduzione letta troppo presto finisce sotto gli occhi.

    Quindi la regola, e vale per chiunque ne aggiunga un'altra: una voce con
    un pill si ricostruisce intera.
    """
    m = re.search(r"this\.modeConfigs\s*=\s*\{(.*?)\n    \};", HEADER, re.S)
    assert m, "modeConfigs non trovato"
    dichiarazione = m.group(1)
    refresh = re.search(r"_refreshTitles\(\)\s*\{(.*?)\n  \}", HEADER, re.S)
    assert refresh, "_refreshTitles non trovato"
    body = refresh.group(1)

    for modo, entry in re.findall(r"\n      (\w+):\s*(.+?),\n", dichiarazione):
        fabbrica = re.match(r"(\w+)\(", entry)
        if not fabbrica:
            continue
        sorgente = re.search(rf"function {fabbrica.group(1)}\(.*?\)\s*\{{(.*?)\n\}}", HEADER, re.S)
        if not sorgente or "pill" not in sorgente.group(1):
            continue
        # I tre cassetti passano dal ciclo su `VIEW_OF`, che li rifa' tutti e
        # tre interi; chiunque altro deve nominarsi.
        rifatta = modo in DRAWERS or re.search(rf"modeConfigs\.{modo}\s*=[^=]", body)
        assert rifatta, (
            f"{modo} porta un pill ma al cambio lingua non si rifa' intera: "
            "la parola nel bottone resta quella letta al caricamento del file, "
            "cioe' la chiave grezza"
        )


def test_la_casa_non_e_stata_toccata() -> None:
    """La modifica e' solo dell'officina.

    La casa ha gia' la sua intestazione, con la sua soprascritta e il suo nome:
    un `title-chat` o un `nav.console` comparsi li' vorrebbero dire che il
    cambio e' tracimato nel documento sbagliato.
    """
    home = (UI / "index.html").read_text(encoding="utf-8")
    assert "title-chat" not in home
    assert "view-title-mount" not in home


def test_an_icon_button_fades_under_the_finger_on_the_phone() -> None:
    """Sul telefono (`hover: none`) il bottone-icona sbiadisce al tocco.

    La regola stava nel blocco «Responsive / Touch», tolto da 0116b1f insieme
    alla wiki (revisione del 25/09/2026): da allora il tocco restava col solo
    rimpicciolimento, che a movimento ridotto non c'e'.
    """
    blocks = re.findall(r"@media \(hover: none\) \{(.*?)^\}", CSS, re.S | re.M)
    assert any(re.search(r"\.ibtn:active \{ opacity: 0\.8; \}", b) for b in blocks), (
        "al tocco sul telefono il bottone-icona non da' piu' riscontro"
    )
