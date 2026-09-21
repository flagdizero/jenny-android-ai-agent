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
    """Il corpo di un metodo di `SettingsController`, per nome."""
    m = re.search(rf"\n  {nome}\((.*?)\n  \}}", SETTINGS, re.S)
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
