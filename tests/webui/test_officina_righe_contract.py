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
