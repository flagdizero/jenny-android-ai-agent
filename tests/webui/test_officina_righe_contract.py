"""Le righe dell'officina: nome a sinistra, comando a destra.

La tavola mette etichetta e controllo **sulla stessa riga**, alta 44 px, con il
comando di larghezza fissa. L'app li impilava: etichetta sopra, campo a tutta
larghezza sotto. E' la differenza che si ripeteva piu' volte nei tre cassetti —
«Parametri», «Ricerca web», i campi di Dream e del giardiniere.

**E c'e' una trappola, che questo banco esiste soprattutto per tenere chiusa.**
Le classi `.settings-field` / `.settings-label` / `.settings-input` non sono solo
dell'officina: le usano anche ``shared/backup-flow.js`` e
``shared/telegram-pairing.js``, e backup-flow disegna i **due campi della
passphrase dentro una finestra, anche in casa**. Allineare l'officina cambiando
`.settings-field` avrebbe schiacciato una password in 92 px, in una schermata
che con l'officina non c'entra niente.

Per questo la riga a due colonne e' una **classe nuova**, `.settings-riga`, e i
banchi qui sotto chiedono che resti tale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

SETTINGS = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
CSS = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
BACKUP = (ASSETS / "shared" / "backup-flow.js").read_text(encoding="utf-8")


def _corpo(nome: str) -> str:
    """Il corpo di un metodo di `SettingsController`, per nome.

    `async` è opzionale: senza, i tre metodi che aspettano la rete —
    proprio quelli che questi banchi devono leggere — risultavano "non
    trovati", che è un falso verde travestito da rosso.
    """
    m = re.search(rf"\n  (?:async )?{nome}\((.*?)\n  \}}", SETTINGS, re.S)
    assert m, f"{nome} non trovato"
    return m.group(1)


# ── La riga a due colonne ────────────────────────────────────────────────────


@pytest.mark.parametrize("helper", ("_field", "_select", "_numberField"))
def test_i_tre_aiutanti_fanno_una_riga_non_una_pila(helper: str) -> None:
    """I tre che disegnano «etichetta + controllo» usano la riga, non la pila."""
    corpo = _corpo(helper)
    assert 'class="settings-riga"' in corpo, (
        f"{helper} disegna ancora una pila: l'etichetta finirebbe sopra il campo"
    )
    assert 'class="settings-field"' not in corpo


def test_la_riga_ha_le_sue_regole() -> None:
    """Senza queste, `.settings-riga` eredita il nulla e la riga non esiste."""
    m = re.search(r"^\.settings-riga \{(.*?)\}", CSS, re.S | re.M)
    assert m, ".settings-riga non ha una regola sua"
    regola = m.group(1)
    assert "flex-direction: row" in regola, "la riga non e' orizzontale"
    assert re.search(r"min-height:\s*44px", regola), (
        "la riga non arriva a 44px: sotto quella misura il dito manca il bersaglio"
    )


def test_i_comandi_hanno_una_larghezza_fissa() -> None:
    """Un numero e un menu' a tutta larghezza sono la pila di prima con un altro
    nome: e' la larghezza fissa a fare le due colonne."""
    for selettore, largh in (
        (r"\.settings-riga > \.settings-input", "92px"),
        (r"\.settings-riga > \.settings-select", "150px"),
    ):
        m = re.search(rf"{selettore} \{{([^}}]*)\}}", CSS)
        assert m, f"{selettore} senza regola"
        assert f"width: {largh}" in m.group(1), f"{selettore}: larghezza non fissa"


def test_i_numeri_sono_allineati_a_destra() -> None:
    """Incolonnati, tre numeri si confrontano con l'occhio."""
    m = re.search(r"\.settings-riga > \.settings-input \{([^}]*)\}", CSS)
    assert m and "text-align: right" in m.group(1)


# ── La trappola: la casa non si storce ───────────────────────────────────────


def test_la_pila_resta_per_chi_la_usa_davvero() -> None:
    """`.settings-field` resta una **colonna**.

    E' la classe dei due campi della passphrase, che vivono in una finestra e
    anche in casa. Se qualcuno la rende orizzontale per allineare l'officina,
    quella password diventa larga 92 px in una schermata che non c'entra.
    """
    m = re.search(r"^\.settings-field \{(.*?)\}", CSS, re.S | re.M)
    assert m, ".settings-field non ha piu' una regola"
    assert "flex-direction: column" in m.group(1), (
        ".settings-field non e' piu' una pila: guarda i campi della passphrase "
        "in shared/backup-flow.js prima di dire che va bene"
    )


def test_il_flusso_condiviso_usa_ancora_la_pila() -> None:
    """La prova che la trappola e' reale e non teorica: il file c'e', e la usa."""
    assert 'class="settings-field"' in BACKUP
    assert 'type="password"' in BACKUP, (
        "se la passphrase non e' piu' un campo password, questo banco va riletto"
    )


# ── I valori di macchina in monospazio ───────────────────────────────────────


@pytest.mark.parametrize(
    "selettore",
    (".model-inuse-name", ".provider-name", ".provider-card-body"),
)
def test_i_valori_di_macchina_sono_in_monospazio(selettore: str) -> None:
    """Un nome di modello, un endpoint e una chiave sono identificatori.

    In proporzionale si leggono come parole e si confrontano male con quelli
    scritti altrove (in un file di config, in un messaggio d'errore). Il token
    esiste in tutti e sette i temi.
    """
    m = re.search(re.escape(selettore) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"{selettore} senza regola"
    assert "var(--font-mono)" in m.group(1), f"{selettore} non e' in monospazio"


def test_il_monospazio_viene_dal_token_e_non_da_un_nome_di_carattere() -> None:
    """Sette temi, sette caratteri possibili: inchiodarne uno ne rompe sei."""
    for riga in CSS.splitlines():
        if "font-family" in riga and ("Fira Code" in riga or "monospace" in riga):
            assert "--font-mono" in riga or "@font-face" in riga or "--font-" in riga, (
                f"carattere monospazio scritto a mano invece che dal token: {riga.strip()}"
            )


# ── Riassumere invece di elencare ────────────────────────────────────────────
#
# La differenza più grossa fra i cassetti e le tavole non era di stile: era di
# **quanto c'è a schermo**. Memoria misurava 6 467 px sul telefono (foto intera,
# 21/09/2026) e quasi due terzi erano l'elenco delle istantanee, una riga per
# ognuna. Nessuna di quelle righe risponde alla domanda per cui si apre il
# gruppo — «ce l'ho una storia, e quanto va indietro?» — a cui invece bastano
# due numeri.
#
# Il criterio della tavola, che questi banchi tengono fermo: **in cassetto quel
# che si legge, l'amministrazione dietro un tocco.**

OFFICINA_HTML = (ROOT / "jenny" / "templates" / "ui" / "officina.html").read_text(encoding="utf-8")


def test_la_storia_in_cassetto_e_una_riga() -> None:
    """`_renderBackup` non disegna più l'elenco, il menù, né «crea adesso»."""
    corpo = _corpo("_renderBackup")
    assert "_riepilogo(" in corpo, "la storia non è più riassunta in una riga"
    for roba in ("snapshot-list", "snapshot-retention", "btn-snapshot-create"):
        assert roba not in corpo, f"«{roba}» è tornato disteso nel cassetto"


def test_il_dettaglio_vive_nel_pannello() -> None:
    """Niente è stato **tolto**: è solo andato dietro il tocco."""
    corpo = _corpo("_apriStoria")
    for roba in ("snapshot-list", "snapshot-retention", "btn-snapshot-create"):
        assert roba in corpo, f"«{roba}» non è nel pannello: allora è sparito davvero"
    assert 'id="drawer-storia"' in OFFICINA_HTML, "il pannello non esiste nel documento"
    assert 'id="drawer-storia-body"' in OFFICINA_HTML


def test_il_corpo_del_pannello_si_disegna_all_apertura() -> None:
    """Un pannello chiuso **non ha i suoi nodi**.

    Se il corpo si disegnasse al caricamento della schermata, `_loadSnapshotList`
    scriverebbe nel vuoto e la riga resterebbe su «Caricamento…» — in silenzio,
    che è il modo in cui questo difetto è già arrivato sul telefono una volta.
    """
    assert "this._apriStoria" in SETTINGS, "niente collega la riga al suo pannello"
    apri = _corpo("_apriStoria")
    assert "_wireStoria" in apri and "_loadSnapshotList" in apri, (
        "il pannello si disegna ma non si aggancia né si riempie"
    )
    # E il caricatore cerca il nodo nel documento, non dentro la vista: il
    # pannello vive fuori da `contentEl`.
    carico = _corpo("_loadSnapshotList")
    assert "document.getElementById('snapshot-list')" in carico
    assert "contentEl.querySelector('#snapshot-list')" not in carico


def test_il_riepilogo_racconta_la_piu_vecchia_non_la_piu_recente() -> None:
    """Quanto **indietro** si può tornare: è la cosa per cui una storia esiste.

    La più recente è quasi sempre «poco fa» e non distingue una storia di venti
    istantanee da una di due.
    """
    corpo = _corpo("_caricaRiepilogoStoria")
    assert "Math.min(" in corpo, "il riepilogo guarda la più recente"
    assert "Math.max(" not in corpo


def test_il_riepilogo_non_resta_a_caricamento_per_sempre() -> None:
    """Un errore è un'informazione; un «Caricamento…» eterno è un guasto
    travestito da attesa."""
    corpo = _corpo("_caricaRiepilogoStoria")
    assert "catch" in corpo
    assert "snapshotHistoryUnavailable" in corpo


@pytest.mark.parametrize("lingua", ("it", "en"))
def test_le_due_frasi_del_riepilogo_esistono(lingua: str) -> None:
    import json

    d = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))["backup"]
    assert "{count}" in d["snapshotSummary"] and "{when}" in d["snapshotSummary"], (
        f"{lingua}: il riepilogo non porta i due numeri che lo rendono utile"
    )
    assert d["snapshotSummaryEmpty"].strip()


def test_le_righe_di_riepilogo_si_agganciano_con_una_regola_sola() -> None:
    """Ne arriveranno altre due (Telegram, SSH): un `if` per ognuna le farebbe
    divergere una per volta."""
    assert "[data-riepilogo]" in SETTINGS, "il cablaggio non è generico"
    assert "_APRI_PANNELLO" in SETTINGS, "manca la tabella pannello -> chi lo riempie"


def test_telegram_in_cassetto_e_una_riga() -> None:
    """Il widget di accoppiamento è amministrazione: token, codice, disaccoppia.

    In cassetto ne resta la risposta alla sola domanda che si fa da lì: «posso
    scriverle da fuori, adesso?».
    """
    corpo = _corpo("_renderTelegram")
    assert "_riepilogo(" in corpo
    assert "settings-telegram-widget" not in SETTINGS, (
        "il widget è ancora montato nel cassetto invece che nel pannello"
    )
    assert 'id="drawer-telegram-body"' in OFFICINA_HTML


def test_il_widget_si_monta_all_apertura_del_pannello() -> None:
    corpo = _corpo("_apriTelegram")
    assert "TelegramPairingWidget" in corpo and "drawer-telegram-body" in corpo
    assert "destroy()" in corpo, (
        "riaprire il pannello lascerebbe due widget vivi sullo stesso stato"
    )


def test_la_riga_telegram_si_legge_senza_il_widget() -> None:
    """La riga deve dire qualcosa **prima** che il pannello esista."""
    corpo = _corpo("_caricaRiepilogoTelegram")
    assert "getTelegramStatus" in corpo, "la riga aspetta il widget per sapere cosa dire"
    assert "telegramSummary" in corpo


def test_il_riassunto_telegram_copre_tutti_gli_stati() -> None:
    """Quattro stati più «non leggibile»: se ne manca uno, la riga resta vuota
    proprio nel caso che l'utente vuole capire."""
    import json

    pairing = (ASSETS / "shared" / "telegram-pairing.js").read_text(encoding="utf-8")
    assert "export function telegramSummary" in pairing
    for lingua in ("it", "en"):
        tg = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        tg = tg["settings"]["telegram"]
        for k in ("summaryPaired", "summaryNotPaired", "summaryNoToken", "summaryOff",
                  "summaryUnknown"):
            assert tg.get(k, "").strip(), f"{lingua}: manca {k}"
        assert "{who}" in tg["summaryPaired"]


# ── SSH: la forma della tavola, senza perdere i due stati ────────────────────
#
# Qui la tavola e il codice erano in conflitto, ed è stato deciso di seguire
# **tutte e due**: la riga compatta della tavola, ma con dentro i due stati che
# il commento di `_renderSshHost` difendeva da prima —
#
#   «credenziale pronta e impronta accettata sono i due passi che l'utente deve
#   fare, e nasconderli dietro un tap lascerebbe host mezzi configurati che
#   falliscono solo al primo comando»
#
# — perché quella non era un'opinione grafica: era una misura.


def test_un_host_e_una_riga() -> None:
    corpo = _corpo("_renderSshHost")
    assert 'class="ssh-riga"' in corpo, "l'host è tornato una scheda"
    assert "provider-card" not in corpo
    m = re.search(r"^\.ssh-riga \{(.*?)\}", CSS, re.S | re.M)
    assert m and re.search(r"min-height:\s*52px", m.group(1)), (
        "la riga non ha l'altezza della tavola"
    )


def test_i_due_stati_restano_in_chiaro_nella_riga() -> None:
    """Il punto della decisione: **non** dietro il tocco.

    Se qualcuno li sposta nel pannello per accorciare la riga, un host a cui
    manca la chiave sembra a posto finché non fallisce il primo comando.
    """
    corpo = _corpo("_renderSshHost")
    assert corpo.count("this._segnoSsh(") == 2, "i due stati non sono più due"
    assert "has_key" in corpo and "pinned" in corpo, (
        "la riga non guarda più credenziale e impronta"
    )
    pannello = _corpo("_apriHostSsh")
    assert "_segnoSsh" not in pannello, "i due stati sono migrati dietro il tocco"


def test_lo_stato_si_distingue_anche_senza_colore() -> None:
    """Pieno contro vuoto, non solo verde contro giallo: su un tema in cui
    l'accento **è** il colore del testo, due pallini colorati si somigliano."""
    corpo = _corpo("_segnoSsh")
    assert "ti-circle-check-filled" in corpo and "ti-circle" in corpo, (
        "la differenza è affidata al solo colore"
    )
    assert "title=" in corpo, "il testo lungo non è più raggiungibile da nessuna parte"


def test_i_comandi_dell_host_stanno_nel_pannello() -> None:
    """Genera, verifica, modifica, elimina, copia: sono cose che si fanno **a**
    un host, non informazioni su di lui."""
    pannello = _corpo("_apriHostSsh")
    for cmd in ("ssh-generate", "ssh-verify", "ssh-edit", "ssh-delete"):
        assert cmd in pannello, f"«{cmd}» non è nel pannello"
    riga = _corpo("_renderSshHost")
    for cmd in ("ssh-generate", "ssh-verify", "ssh-edit", "ssh-delete"):
        assert cmd not in riga, f"«{cmd}» è rimasto nella riga"
    assert 'id="drawer-ssh-host"' in OFFICINA_HTML


def test_il_cablaggio_dei_comandi_guarda_dentro_il_pannello() -> None:
    """Cercarli in `contentEl` non troverebbe niente: il pannello è fuori."""
    corpo = _corpo("_wireHostSsh")
    assert "#drawer-ssh-host-body" in corpo
    assert "contentEl" not in corpo


@pytest.mark.parametrize("lingua", ("it", "en"))
def test_le_parole_corte_dei_due_stati_esistono(lingua: str) -> None:
    import json

    ssh = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
    ssh = ssh["settings"]["ssh"]
    for k in ("markKey", "markPassword", "markFingerprint"):
        assert ssh.get(k, "").strip(), f"{lingua}: manca {k}"
        assert len(ssh[k]) <= 12, f"{lingua}: «{ssh[k]}» è troppo lungo per una riga da 52px"


# ── Secondo giro: struttura ──────────────────────────────────────────────────


def test_le_porte_stanno_dentro_il_loro_gruppo() -> None:
    """Stavano in cima al cassetto, tutte insieme e staccate dal loro argomento:
    si apriva Memoria e la prima cosa erano «Workspace» e «Wiki», prima ancora
    di sapere di cosa parlasse la pagina. Nelle tavole di Mani e Memoria non c'è
    niente prima del primo gruppo.
    """
    assert "_renderPorte(" not in SETTINGS, "il blocco in cima è tornato"
    assert "_portePerGruppo(" in SETTINGS
    # E finiscono **dentro** la scheda: concatenarle fuori le lascerebbe
    # fluttuare fra due gruppi, che è dove sono atterrate al primo tentativo.
    m = re.search(r"_gruppo\(id, etichetta, corpo, porte = ''\)\s*\{(.*?)\n  \}", SETTINGS, re.S)
    assert m, "_gruppo non prende più le porte"
    assert "${corpo}${porte}" in m.group(1), (
        "le porte non sono dentro la scheda del gruppo"
    )


def test_la_tabella_delle_porte_dice_a_quale_gruppo_appartengono() -> None:
    """Da elenco per cassetto a mappa gruppo -> porte: senza, «dentro il gruppo»
    non è esprimibile."""
    m = re.search(r"export const CASSETTI = \{(.*?)\n\};", SETTINGS, re.S)
    assert m
    # **Nessuna** voce può essere un elenco piatto. Controllarne una sola
    # lasciava passare la mutazione che ne riportava indietro un'altra:
    # misurato il 21/09/2026, il banco era verde con `mani` già rotta.
    piatte = re.findall(r"(\w+): \{\s*sezioni: \[[^\]]*\],\s*porte: \[", m.group(1))
    assert not piatte, f"`porte` è tornata un elenco piatto in: {piatte}"
    assert m.group(1).count("porte: {") == 3, "un cassetto non dichiara più le sue porte"


def test_i_tetti_si_leggono_e_si_cambiano_altrove() -> None:
    """«Quanto ricorda» è una domanda con una risposta da leggere."""
    corpo = _corpo("_renderQuantoRicorda")
    assert "_misuraTetto(" in corpo
    assert "_numberField(" not in corpo, "i campi modificabili sono tornati in cassetto"
    assert 'data-riepilogo="tetti"' in corpo, "manca il modo di cambiarli"
    pannello = _corpo("_apriTetti")
    assert pannello.count("_numberField(") == 3, "i tre campi non sono nel pannello"


def test_la_misura_dice_quanto_resta_non_solo_quanto_misura() -> None:
    """«2.090 su 3.000» va letto e sottratto; «restano 910» è la risposta.

    E sopra il tetto la frase cambia del tutto, perché cambia la conseguenza:
    Dream smette di scrivere.
    """
    corpo = _corpo("_misuraTetto")
    assert "settings.memory.headroom" in corpo
    assert "headroomOver" in corpo, "sopra il tetto non si dice cosa succede"
    import json

    for lingua in ("it", "en"):
        mem = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        mem = mem["settings"]["memory"]
        assert "{left}" in mem["headroom"] and "{over}" in mem["headroomOver"]


def test_una_marca_e_una_riga_con_chi_risponde() -> None:
    """La pastiglia «risponde» la scheda non ce l'aveva: da qui si amministrano
    le marche, e sapere quale sta rispondendo è il contesto di ogni decisione."""
    corpo = _corpo("_renderProviderListHtml")
    assert 'class="marca-riga"' in corpo and "provider-card" not in corpo
    assert "marca-risponde" in corpo and "settings.answersNow" in corpo
    assert "provider-edit" not in corpo and "provider-delete" not in corpo, (
        "modifica ed elimina sono rimaste nella riga"
    )
    assert "provider-edit" in _corpo("_apriMarca")
    m = re.search(r"^\.marca-riga \{(.*?)\}", CSS, re.S | re.M)
    assert m and re.search(r"min-height:\s*52px", m.group(1))


def test_il_colore_della_marca_non_viene_da_una_tabella() -> None:
    """Una tabella nome->colore lascerebbe grigie proprio le marche che
    l'utente si è aggiunto da sé, che sono il motivo per cui questa schermata
    esiste."""
    corpo = _corpo("_coloreMarca")
    assert "charCodeAt" in corpo and "hsl(" in corpo


def test_tenere_sveglia_la_cpu_e_un_comando_a_segmenti() -> None:
    """Tre voci stanno in riga; il criterio era già scritto nel codice."""
    corpo = _corpo("_renderKeepAwake")
    assert "settings-seg" in corpo and "<select" not in corpo
    assert "keepAwakeShort" in corpo, "le parole lunghe non stanno in un terzo di riga"
    assert 'role="radiogroup"' in corpo and 'role="radio"' in corpo
    # Il testo lungo — «(consigliato)» compreso — non si perde: va nel title.
    assert "title=" in corpo and "settings.battery.keepAwake.$" in corpo.replace("{id}", "$")


def test_il_bottone_principale_e_pieno_e_uno_solo() -> None:
    """Sei bottoni pieni sulla stessa pagina non ne fanno risaltare nessuno."""
    assert SETTINGS.count("settings-btn-pieno") == 1, (
        "il modificatore è finito su più di un'azione"
    )
    assert "settings.addProviderHint" in SETTINGS, "manca la riga che spiega cosa comporta"
    m = re.search(r"^\.settings-btn-pieno \{(.*?)\}", CSS, re.S | re.M)
    assert m and "var(--on-accent)" in m.group(1), (
        "testo non su --on-accent: con un accento chiaro non si legge"
    )


# ── Terzo giro: dettagli ─────────────────────────────────────────────────────


def test_la_finestra_di_contesto_ha_finalmente_un_comando() -> None:
    """Esisteva nello schema, nel payload e nella rotta — con due soli valori
    accettati — e **nessuna schermata la mostrava**. Non era un dato mancante:
    era un comando mancante."""
    corpo = _corpo("_renderParametri")
    assert "context_window_tokens" in corpo
    # L'**espressione**, non la parola: `context_window_options` compare anche
    # nel commento sopra, e cercarla lì lasciava passare la mutazione che
    # ricopiava l'elenco a mano (misurato il 21/09/2026, banco verde su codice
    # rotto). E nessun numero scritto qui: la rotta ne rifiuta ogni altro, e
    # due copie divergono in silenzio alla prima aggiunta.
    assert "a.context_window_options" in corpo, (
        "le voci sono ricopiate qui invece di arrivare dal server"
    )
    codice = "\n".join(
        r for r in SETTINGS.splitlines()
        if not r.lstrip().startswith(("*", "//", "/*"))
    )
    assert not re.search(r"\b(65536|262144)\b", codice), (
        "un valore della finestra di contesto è scritto a mano nel client"
    )
    # E si salva: senza questa chiave il menù cambia e non succede niente.
    assert "'context_window_tokens'" in _corpo("_wireSections")


def test_le_voci_della_finestra_vengono_dal_server() -> None:
    """La rotta rifiuta qualunque altro valore: due copie dell'elenco
    divergerebbero in silenzio alla prima aggiunta. Stessa forma di
    `power.modes`, che era già così."""
    api = (ROOT / "jenny" / "webui" / "settings_api.py").read_text(encoding="utf-8")
    assert '"context_window_options": list(_CONTEXT_WINDOW_TOKEN_OPTIONS)' in api
    # Tupla e non `set`: la UI ne fa un menù, e l'ordine di un `set` non è
    # garantito fra due esecuzioni.
    assert "_CONTEXT_WINDOW_TOKEN_OPTIONS = (" in api, (
        "l'elenco è tornato un set: l'ordine del menù ballerebbe"
    )


def test_i_numeri_del_menu_hanno_i_separatori() -> None:
    """«65536» non si conta a occhio; «65 536» sì."""
    corpo = _corpo("_renderParametri")
    assert "toLocaleString" in corpo
    sel = _corpo("_select")
    assert "typeof o === 'object'" in sel, (
        "il menù non sa più separare quel che salva da quel che mostra"
    )


def test_la_pastiglia_dice_una_cosa_che_il_guscio_sa_davvero() -> None:
    """La tavola scrive «idle», che è uno stato di **turno**: lo conosce la
    chat, non il guscio, e duplicarlo qui vorrebbe dire tenerne due copie che
    divergono. La connessione invece il guscio ce l'ha, ed è la più utile delle
    due da un cassetto di impostazioni — se è caduta, quel che tocchi non
    arriva da nessuna parte.
    """
    header = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")
    assert "wsManager.chatConnected" in header
    assert "'chat:open'" in header and "'chat:close'" in header, (
        "la pastiglia non segue le transizioni: un cassetto aperto da dieci "
        "minuti con la WS caduta direbbe il falso"
    )
    assert "view-title-stato" in CSS
    m = re.search(r"\.view-title-stato\.is-giu \.view-title-punto \{([^}]*)\}", CSS)
    assert m and "inset" in m.group(1), (
        "pieno e vuoto sono l'unica differenza che sopravvive a ogni tema"
    )


def test_i_cassetti_non_hanno_piu_il_bottone_aggiorna() -> None:
    """`activate()` ricarica a ogni apertura e ogni salvataggio ridisegna: un
    bottone che rifà quel che è appena successo insegna a premerlo per
    scaramanzia."""
    header = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")
    m = re.search(r"function cassetto\(nome\) \{(.*?)\n\}", header, re.S)
    assert m, "cassetto() non trovata"
    assert "'refresh'" not in m.group(1), "l'icona «aggiorna» è tornata nei cassetti"


def test_il_cassetto_dice_dove_sta_quel_che_non_ci_sta() -> None:
    """Un cassetto che si chiama «Cervello» sembra il posto dove cercare Dream:
    è l'errore che il giro dei cassetti ha già fatto una volta."""
    assert "officina.rimando.dreamInMemoria" in SETTINGS
    assert ".settings-rimando {" in CSS
    import json

    for lingua in ("it", "en"):
        d = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        assert d["officina"]["rimando"]["dreamInMemoria"].strip()
