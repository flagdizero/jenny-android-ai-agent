"""Le pagine della casa nel `config.json`: che specie esistono, e cosa succede
a quelle che non esistono piu'.

Le stanze sono state pagine dal 22 al 23/09/2026 e poi sono uscite. Il rischio
di quell'uscita non e' nel client: e' qui. Lo schema rifiuta una specie che non
conosce, e il loader davanti a un file che non valida prova il `.bak` e poi
**parte dai default** — cioe' chi aveva una stanza appesa avrebbe perso
provider e chiavi per una pagina. Per questo il banco passa dal loader vero e
non dal solo schema: e' li' che il danno si vedrebbe.
"""

from __future__ import annotations

import json

import pytest

from jenny.config.loader import load_config, load_config_with_raw, save_config
from jenny.config.schema import SPECIE_SCHERMATA, CasaConfig
from jenny.runtime.context import get_runtime_context


def _reset_recovery_flags() -> None:
    ctx = get_runtime_context()
    ctx.config_recovered_from = None
    ctx.config_quarantine_path = None


def _file_con_una_stanza(tmp_path):
    percorso = tmp_path / "config.json"
    percorso.write_text(
        json.dumps(
            {
                "providers": {
                    "providers": [
                        {"name": "deepseek", "format": "openai_compat", "apiKey": "sk-keep-me"}
                    ],
                    "default": "deepseek",
                },
                "casa": {
                    "schermate": [
                        {"id": "p1", "kind": "app", "ref": "orto"},
                        {"id": "p2", "kind": "stanza", "ref": "backup"},
                        {"id": "p3", "kind": "conversazione", "ref": "project:viaggi"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return percorso


def test_only_places_you_stay_are_page_kinds() -> None:
    """App e quaderni. Le stanze sono posti dove si va, non dove si sta."""
    assert SPECIE_SCHERMATA == ("app", "conversazione")


def test_a_file_with_a_room_page_loads_whole_minus_that_page(tmp_path) -> None:
    """Il caso che costerebbe caro: una pagina stanza non deve portarsi via il file."""
    _reset_recovery_flags()
    config = load_config(_file_con_una_stanza(tmp_path))

    assert get_runtime_context().config_recovered_from is None, (
        "il file con una pagina stanza non ha validato: il loader e' ripiegato "
        "sul backup o sui default, cioe' l'utente ha perso la configurazione"
    )
    assert [p.api_key for p in config.providers.providers] == ["sk-keep-me"]
    assert [(s.id, s.kind) for s in config.casa.schermate] == [
        ("p1", "app"),
        ("p3", "conversazione"),
    ]


def test_an_unknown_kind_is_still_refused() -> None:
    """La migrazione toglie le stanze e **solo** loro.

    Una specie che nessuna versione ha mai avuto e' un errore vero, e deve
    restare un errore: e' quel che impedisce a un `config.json` scritto a mano
    di mettere in casa una pagina che il prodotto non sa disegnare.
    """
    with pytest.raises(ValueError):
        CasaConfig(schermate=[{"id": "p1", "kind": "cassetto", "ref": "x"}])


def test_a_room_can_no_longer_be_saved() -> None:
    """Chi prova a scriverne una nuova se la vede rifiutare, non ingoiare.

    La migrazione sta su `CasaConfig`, che e' quel che si legge dal file; la
    rotta valida ogni riga come `SchermataConfig`, quindi li' una stanza e'
    una specie sconosciuta come un'altra.
    """
    from jenny.config.schema import SchermataConfig

    with pytest.raises(ValueError):
        SchermataConfig(id="p1", kind="stanza", ref="backup")


def test_the_dropped_room_does_not_come_back_on_the_next_write(tmp_path) -> None:
    """Uscita vuol dire uscita: il prossimo salvataggio non la riscrive.

    Passa da `save_config` col grezzo, com'e' la strada di `store.mutate`: e'
    quella che riporta dentro le chiavi che lo schema non conosce, e una
    stanza tornata da li' ricomparirebbe a ogni scrittura.
    """
    _reset_recovery_flags()
    percorso = _file_con_una_stanza(tmp_path)
    config, grezzo = load_config_with_raw(percorso)
    save_config(config, percorso, preserve_unknown_from=grezzo)

    scritte = json.loads(percorso.read_text(encoding="utf-8"))["casa"]["schermate"]
    assert [s["kind"] for s in scritte] == ["app", "conversazione"]
