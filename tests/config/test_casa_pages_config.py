"""Le pagine della casa nel `config.json`: che specie esistono, e cosa succede
a quelle che non esistono piu'.

Le stanze sono state pagine dal 22 al 23/09/2026 e poi sono uscite. Il rischio
di quell'uscita non e' nel client: e' qui. Il loader davanti a un file che non
valida prova il `.bak` e poi **parte dai default** — cioe' chi aveva una pagina
che lo schema rifiuta (una stanza, una specie di una versione piu' nuova)
avrebbe perso provider e chiavi per una pagina. Per questo il banco passa dal
loader vero e non dal solo schema: e' li' che il danno si vedrebbe.
"""

from __future__ import annotations

import json

import pytest

from jenny.config.loader import load_config, load_config_with_raw, save_config
from jenny.config.schema import PAGE_KINDS, HomeConfig
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
                "home": {
                    "pages": [
                        {"id": "p1", "kind": "app", "ref": "orto"},
                        {"id": "p2", "kind": "room", "ref": "backup"},
                        {"id": "p3", "kind": "conversation", "ref": "project:viaggi"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return percorso


def test_only_places_you_stay_are_page_kinds() -> None:
    """App e quaderni. Le stanze sono posti dove si va, non dove si sta."""
    assert PAGE_KINDS == ("app", "conversation")


def test_a_file_with_a_room_page_loads_whole_minus_that_page(tmp_path) -> None:
    """Il caso che costerebbe caro: una pagina stanza non deve portarsi via il file."""
    _reset_recovery_flags()
    config = load_config(_file_con_una_stanza(tmp_path))

    assert get_runtime_context().config_recovered_from is None, (
        "il file con una pagina stanza non ha validato: il loader e' ripiegato "
        "sul backup o sui default, cioe' l'utente ha perso la configurazione"
    )
    assert [p.api_key for p in config.providers.providers] == ["sk-keep-me"]
    assert [(s.id, s.kind) for s in config.home.pages] == [
        ("p1", "app"),
        ("p3", "conversation"),
    ]


def test_an_unknown_kind_leaves_the_file_not_the_file_leaves() -> None:
    """Una specie che questa versione non conosce non entra in casa — ma costa la
    pagina, non il file.

    Fino al 25/09/2026 era un errore, e un errore qui vuol dire provider e chiavi
    persi: bastava ripristinare il backup di una versione piu' nuova, con una
    specie in piu'. La regola che conta — in casa non entra una pagina che il
    prodotto non sa disegnare — resta vera.
    """
    casa = HomeConfig(pages=[
        {"id": "p1", "kind": "cassetto", "ref": "x"},
        {"id": "p2", "kind": "app", "ref": "orto"},
    ])
    assert [s.id for s in casa.pages] == ["p2"]


@pytest.mark.parametrize("storta", [
    {"id": "p9", "kind": "widget", "ref": "meteo"},              # una specie di domani
    {"id": "p9", "kind": "conversation", "ref": "project:a b"},  # un quaderno che non si apre
    {"id": "p9", "kind": "app"},                                   # manca un campo
    "p9",                                                          # non e' nemmeno una riga
    {"id": "p9", "kind": "app", "ref": "project:orto"},            # uno slug che non e' uno slug
    {"id": "p 9", "kind": "app", "ref": "spesa"},                  # un id storto
])
def test_a_page_that_cannot_be_drawn_does_not_cost_the_file(tmp_path, storta) -> None:
    """Dal loader vero, perche' e' li' che il danno si vedrebbe."""
    _reset_recovery_flags()
    percorso = tmp_path / "config.json"
    percorso.write_text(json.dumps({
        "providers": {
            "providers": [{"name": "deepseek", "format": "openai_compat", "apiKey": "sk-keep-me"}],
            "default": "deepseek",
        },
        "home": {"pages": [{"id": "p1", "kind": "app", "ref": "orto"}, storta]},
    }), encoding="utf-8")

    config = load_config(percorso)

    assert get_runtime_context().config_recovered_from is None
    assert [p.api_key for p in config.providers.providers] == ["sk-keep-me"]
    assert [s.id for s in config.home.pages] == ["p1"]


def test_each_row_is_validated_once(monkeypatch) -> None:
    """``_drawable_pages`` valida ogni riga per vagliarla: la pagina
    che ne esce e' quella che il campo tiene, senza una seconda validazione."""
    from jenny.config.schema import HomePageConfig

    chiamate: list[object] = []
    vera = HomePageConfig.model_validate.__func__

    def _conta(cls, data, *args, **kwargs):
        chiamate.append(data)
        return vera(cls, data, *args, **kwargs)

    monkeypatch.setattr(HomePageConfig, "model_validate", classmethod(_conta))
    HomeConfig(pages=[
        {"id": "p1", "kind": "app", "ref": "orto"},
        {"id": "p2", "kind": "conversation", "ref": "project:viaggi"},
    ])
    assert len(chiamate) == 2


def test_too_many_and_duplicate_pages_are_trimmed_not_refused() -> None:
    """Il tetto e gli id restano regole, ma sul file si applicano togliendo."""
    righe = [{"id": f"p{i}", "kind": "app", "ref": f"a{i}"} for i in range(12)]
    righe.insert(1, {"id": "p0", "kind": "app", "ref": "doppione"})
    casa = HomeConfig(pages=righe)
    assert [s.id for s in casa.pages] == [f"p{i}" for i in range(8)]
    assert casa.pages[0].ref == "a0", "vince la prima delle due con lo stesso id"


def test_a_dropped_page_is_reported_once_not_at_every_read(monkeypatch) -> None:
    """Il file tiene la riga finche' qualcuno non riscrive la config, e
    ``load_config()`` non ha cache: senza memoria lo stesso avviso tornava a ogni
    lettura, piu' volte per turno."""
    from loguru import logger

    from jenny.config import schema

    monkeypatch.setattr(schema, "_DROPPED_PAGES_WARNED", set())
    visti: list[str] = []
    sink = logger.add(lambda m: visti.append(str(m)), level="WARNING", format="{message}")
    try:
        storta = {"id": "p9", "kind": "widget", "ref": "meteo"}
        for _ in range(3):
            HomeConfig(pages=[storta])
        HomeConfig(pages=[{"id": "p8", "kind": "widget", "ref": "orologio"}])
    finally:
        logger.remove(sink)

    assert len(visti) == 2, visti
    assert all(v.startswith("home page dropped (") for v in visti), visti


def test_a_room_can_no_longer_be_saved() -> None:
    """Chi prova a scriverne una nuova se la vede rifiutare, non ingoiare.

    La migrazione sta su `HomeConfig`, che e' quel che si legge dal file; la
    rotta valida ogni riga come `HomePageConfig`, quindi li' una stanza e'
    una specie sconosciuta come un'altra.
    """
    from jenny.config.schema import HomePageConfig

    with pytest.raises(ValueError):
        HomePageConfig(id="p1", kind="room", ref="backup")


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

    scritte = json.loads(percorso.read_text(encoding="utf-8"))["home"]["pages"]
    assert [s["kind"] for s in scritte] == ["app", "conversation"]


# ── L'ordine delle pagine (23/09/2026) ──────────────────────────────────────
#
# Dal 23/09 la chat non e' piu' la pagina 0: l'utente sposta tutte le pagine,
# le quattro fisse comprese (`.agent/pagine-in-alto-plan.md`). L'ordine sta in
# un campo a parte, e la sua regola e' la stessa della migrazione qui sopra:
# **mai un errore**, perche' un errore costa il file intero.

from jenny.config.schema import FIXED_PAGES, normalize_order  # noqa: E402

TODO = {"id": "p1", "kind": "app", "ref": "todo"}
ORTO = {"id": "p2", "kind": "app", "ref": "orto"}


def test_the_fixed_pages_are_the_four_the_user_named() -> None:
    assert FIXED_PAGES == ("app", "chat", "notebooks", "settings")


def test_someone_who_never_moved_anything_starts_from_the_default() -> None:
    """App · Jenny · <le aggiunte> · Quaderni · Impostazioni."""
    assert HomeConfig().order == ["app", "chat", "notebooks", "settings"]
    assert HomeConfig(pages=[TODO, ORTO]).order == [
        "app", "chat", "p1", "p2", "notebooks", "settings",
    ]


def test_an_order_the_user_chose_is_kept_as_is() -> None:
    scelto = ["p1", "settings", "chat", "app", "notebooks"]
    assert HomeConfig(pages=[TODO], order=scelto).order == scelto


def test_unknown_ids_and_duplicates_leave_quietly() -> None:
    """Un id orfano e' lo stato normale di una pagina appena staccata."""
    assert HomeConfig(
        pages=[TODO], order=["chat", "p9", "p1", "chat", 7, "app", "notebooks", "settings"]
    ).order == ["chat", "p1", "app", "notebooks", "settings"]


def test_a_missing_fixed_page_comes_back_at_the_end() -> None:
    """Si spostano, non si tolgono: nemmeno un file scritto a mano le toglie."""
    assert HomeConfig(order=["settings", "chat"]).order == [
        "settings", "chat", "app", "notebooks",
    ]


def test_a_page_missing_from_the_order_goes_right_after_the_chat() -> None:
    """E' dove stavano le pagine prima che l'ordine esistesse."""
    assert HomeConfig(
        pages=[TODO, ORTO], order=["notebooks", "p2", "chat", "app", "settings"]
    ).order == ["notebooks", "p2", "chat", "p1", "app", "settings"]


def test_junk_in_place_of_the_order_is_the_default() -> None:
    assert normalize_order("chat,app", []) == ["app", "chat", "notebooks", "settings"]
    assert normalize_order(None, ["p1"]) == ["app", "chat", "p1", "notebooks", "settings"]


def test_a_page_cannot_take_a_fixed_page_id() -> None:
    """L'ordine non saprebbe piu' quale delle due e' — come un id doppio: esce."""
    casa = HomeConfig(pages=[{"id": "chat", "kind": "app", "ref": "todo"}, TODO])
    assert [s.id for s in casa.pages] == ["p1"]
    assert casa.order.count("chat") == 1


def test_a_file_from_before_the_order_loads_with_its_pages_after_the_chat(tmp_path) -> None:
    """La migrazione vera: il file di chi aveva `[todo]` appesa, dal loader."""
    _reset_recovery_flags()
    percorso = tmp_path / "config.json"
    percorso.write_text(json.dumps({"home": {"pages": [TODO]}}), encoding="utf-8")
    config = load_config(percorso)
    assert get_runtime_context().config_recovered_from is None
    assert config.home.order == ["app", "chat", "p1", "notebooks", "settings"]


def test_a_hand_written_broken_order_does_not_cost_the_file(tmp_path) -> None:
    _reset_recovery_flags()
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
                "home": {"pages": [TODO], "order": ["boh", "p1", "p1"]},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(percorso)
    assert get_runtime_context().config_recovered_from is None
    assert [p.api_key for p in config.providers.providers] == ["sk-keep-me"]
    assert config.home.order == ["p1", "app", "chat", "notebooks", "settings"]


def test_an_order_of_the_wrong_type_is_cleaned_not_refused() -> None:
    """Il tipo del campo lo rifiuterebbe prima della normalizzazione."""
    assert HomeConfig(order="chat").order == ["app", "chat", "notebooks", "settings"]
    assert HomeConfig(order=[None, "chat", 3]).order == ["chat", "app", "notebooks", "settings"]


# ── Il blocco di prima del rinomino in inglese (25/09/2026) ─────────────────
#
# Fino a quel giorno il file diceva ``casa: {schermate, ordine}``, con le specie e
# gli id fissi in italiano. Un telefono ha gia' quel blocco: si deve caricare con
# le pagine sotto ``home``, e perderlo alla prima scrittura invece di trascinarlo
# per sempre come chiave sconosciuta. Dal loader vero, come il resto del banco.

_OLD_HOME_BLOCK = {
    "schermate": [
        {"id": "p1", "kind": "app", "ref": "orto"},
        {"id": "p2", "kind": "stanza", "ref": "backup"},
        {"id": "p3", "kind": "conversazione", "ref": "project:viaggi"},
    ],
    "ordine": ["impostazioni", "p3", "chat", "p1", "app", "quaderni"],
}


def _old_file(tmp_path, block=None):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "providers": [
                        {"name": "deepseek", "format": "openai_compat", "apiKey": "sk-keep-me"}
                    ],
                    "default": "deepseek",
                },
                "casa": _OLD_HOME_BLOCK if block is None else block,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_old_casa_block_loads_under_home_translated(tmp_path) -> None:
    """Chiavi e valori: ``conversazione`` diventa ``conversation``, gli id fissi
    ``quaderni``/``impostazioni`` diventano ``notebooks``/``settings``, e la
    stanza esce in silenzio come prima. L'ordine scelto dall'utente resta suo."""
    _reset_recovery_flags()
    config = load_config(_old_file(tmp_path))

    assert get_runtime_context().config_recovered_from is None
    assert [p.api_key for p in config.providers.providers] == ["sk-keep-me"]
    assert [(p.id, p.kind, p.ref) for p in config.home.pages] == [
        ("p1", "app", "orto"),
        ("p3", "conversation", "project:viaggi"),
    ]
    assert config.home.order == ["settings", "p3", "chat", "p1", "app", "notebooks"]


async def test_after_a_write_the_file_has_home_and_no_casa(tmp_path) -> None:
    """La chiave vecchia e' ritirata: ``store.mutate`` scrive ``home`` e basta."""
    from jenny.config import store

    path = _old_file(tmp_path)
    await store.mutate(lambda _config: None, config_path=path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert "casa" not in written
    assert written["home"]["pages"] == [
        {"id": "p1", "kind": "app", "ref": "orto"},
        {"id": "p3", "kind": "conversation", "ref": "project:viaggi"},
    ]
    assert written["home"]["order"] == ["settings", "p3", "chat", "p1", "app", "notebooks"]
    # E riletto dice la stessa cosa: la traduzione non si applica due volte.
    assert load_config(path).home.order == written["home"]["order"]


def test_a_broken_old_row_costs_the_row_not_the_file(tmp_path) -> None:
    """La traduzione non valida niente: una riga storta arriva ai validatori di
    ``HomeConfig`` com'era, e li' costa la riga (v. ``_drawable_pages``)."""
    _reset_recovery_flags()
    block = {
        "schermate": [{"id": "p1", "kind": "app", "ref": "orto"}, "non un oggetto",
                      {"id": "p2", "kind": "conversazione", "ref": "non-un-quaderno"}],
        "ordine": "chat",
    }
    config = load_config(_old_file(tmp_path, block))

    assert get_runtime_context().config_recovered_from is None
    assert [p.api_key for p in config.providers.providers] == ["sk-keep-me"]
    assert [p.id for p in config.home.pages] == ["p1"]
    assert config.home.order == ["app", "chat", "p1", "notebooks", "settings"]


def test_when_both_blocks_exist_home_wins(tmp_path) -> None:
    """Un file con tutti e due (scritto a mano, o a meta' di una scrittura): il
    nuovo e' la verita', il vecchio non si mescola."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({
            "casa": _OLD_HOME_BLOCK,
            "home": {"pages": [{"id": "n1", "kind": "app", "ref": "spesa"}]},
        }),
        encoding="utf-8",
    )
    config = load_config(path)
    assert [p.id for p in config.home.pages] == ["n1"]
    assert config.home.order == ["app", "chat", "n1", "notebooks", "settings"]
