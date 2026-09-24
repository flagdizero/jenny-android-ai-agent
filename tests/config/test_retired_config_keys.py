"""Le chiavi di config **ritirate**: ne' conservate, ne' segnalate.

``store.mutate`` riporta nel file le chiavi che lo schema non conosce — giusto
per una versione piu' nuova, sbagliato per una chiave che questa versione ha
tolto: resterebbe nel file per sempre, e con lei il warning «Config keys not
recognised» a ogni caricamento. ``RETIRED_KEY_PATHS`` e' la terza specie, e la
versione 2 dello schema fa riscrivere il file una volta all'avvio cosi' cadono
al primo boot (v. ``.agent/retire-atlas-and-main-plan.md``, D2).

Il meccanismo si prova con percorsi **sintetici** montati sulla lista: le due
voci vere (``agents.defaults.atlas``, ``wiki.defaultWiki``) sono ancora campi
dello schema finche' il passo B non li toglie, quindi oggi il dump le contiene
e nessun merge le vede. Il giorno che escono dallo schema, il test in fondo
smette di essere banale e ``test_the_first_boot_rewrites_the_file_without_them``
va allargato alle due chiavi vere (checklist B.3).

La lista e' chiusa e deve restarlo: una chiave **davvero** sconosciuta accanto a
una ritirata avvisa e sopravvive come prima.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from jenny.config import loader
from jenny.config.loader import RETIRED_KEY_PATHS, load_config_with_raw
from jenny.config.schema import CURRENT_CONFIG_VERSION
from jenny.config.store import persist_schema_migrations

# Dentro sezioni **conosciute**: una chiave ritirata sotto un genitore sconosciuto
# non e' un caso reale (il genitore stesso sarebbe l'ignoto da segnalare).
_SYNTHETIC = frozenset({"agents.defaults.gone", "wiki.old_name"})


@pytest.fixture
def retired(monkeypatch: pytest.MonkeyPatch) -> frozenset[str]:
    """La lista vera piu' due percorsi che nessuno schema conosce."""
    paths = RETIRED_KEY_PATHS | _SYNTHETIC
    monkeypatch.setattr(loader, "RETIRED_KEY_PATHS", paths)
    return paths


_LEGACY = {
    "configVersion": 1,
    "agents": {"defaults": {"gone": {"enabled": True}, "maxToolIterations": 25}},
    "wiki": {"enabled": True, "wikisDir": "wikis", "old_name": "main"},
}


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _warnings_while(fn) -> list[str]:
    records: list[str] = []
    handler = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        fn()
    finally:
        loguru_logger.remove(handler)
    return [r for r in records if "not recognised" in r]


def test_the_two_paths_this_plan_retires_are_listed() -> None:
    assert {"agents.defaults.atlas", "wiki.defaultWiki"} <= RETIRED_KEY_PATHS


def test_a_retired_key_does_not_raise_the_unknown_keys_warning(tmp_path, retired) -> None:
    path = tmp_path / "config.json"
    _write(path, _LEGACY)

    assert _warnings_while(lambda: load_config_with_raw(path)) == []


def test_a_truly_unknown_key_beside_them_still_warns(tmp_path, retired) -> None:
    """La lista chiusa non e' un lasciapassare."""
    path = tmp_path / "config.json"
    _write(path, {**_LEGACY, "somethingFromTheFuture": 1})

    hits = _warnings_while(lambda: load_config_with_raw(path))

    assert len(hits) == 1
    assert "somethingFromTheFuture" in hits[0]
    assert "gone" not in hits[0] and "old_name" not in hits[0]


async def test_the_first_boot_rewrites_the_file_without_them(tmp_path, retired) -> None:
    """Versione 1 → 2: una scrittura sola, e le chiavi ritirate non ci sono piu'."""
    path = tmp_path / "config.json"
    _write(path, {**_LEGACY, "somethingFromTheFuture": {"keep": "me"}})

    assert await persist_schema_migrations(config_path=path) is True

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["configVersion"] == CURRENT_CONFIG_VERSION == 2
    assert "gone" not in written["agents"]["defaults"]
    assert "old_name" not in written["wiki"]
    # Quel che era vicino resta: i valori dell'utente e la chiave del futuro.
    assert written["agents"]["defaults"]["maxToolIterations"] == 25
    assert written["wiki"]["wikisDir"] == "wikis"
    assert written["somethingFromTheFuture"] == {"keep": "me"}


async def test_a_current_file_is_not_touched(tmp_path) -> None:
    path = tmp_path / "config.json"
    _write(path, {"configVersion": CURRENT_CONFIG_VERSION, "wiki": {"enabled": True}})
    before = path.read_bytes()

    assert await persist_schema_migrations(config_path=path) is False
    assert path.read_bytes() == before


def test_the_real_retired_keys_load_without_a_warning(tmp_path) -> None:
    """Banale finche' ``AtlasConfig`` e ``default_wiki`` sono nello schema (v. modulo)."""
    path = tmp_path / "config.json"
    _write(path, {
        "configVersion": 1,
        "agents": {"defaults": {"atlas": {"enabled": True, "intervalH": 6}}},
        "wiki": {"defaultWiki": "main", "default_wiki": "main"},
    })

    assert _warnings_while(lambda: load_config_with_raw(path)) == []


async def test_the_mood_model_preset_is_retired_for_real(tmp_path) -> None:
    """La prima chiave ritirata che e' **davvero** uscita dallo schema.

    ``mascotMoodModelPreset`` sceglieva il modello della richiesta dell'umore,
    che dal 24/09/2026 non esiste piu' (l'umore si legge dagli emoji). Un file
    che la porta ancora si carica senza avvisi, e alla prima scrittura
    ordinaria la chiave cade mentre le vicine restano.
    """
    from jenny.config.store import mutate

    path = tmp_path / "config.json"
    _write(path, {
        "configVersion": CURRENT_CONFIG_VERSION,
        "agents": {"defaults": {
            "mascotMood": True,
            "mascotMoodModelPreset": "cheap",
            "mascot_mood_model_preset": "cheap",
        }},
    })

    assert _warnings_while(lambda: load_config_with_raw(path)) == []

    def _spegni(config) -> None:
        config.agents.defaults.mascot_mood = False

    await mutate(_spegni, config_path=path)
    defaults = json.loads(path.read_text(encoding="utf-8"))["agents"]["defaults"]
    assert defaults["mascotMood"] is False
    assert "mascotMoodModelPreset" not in defaults
    assert "mascot_mood_model_preset" not in defaults
