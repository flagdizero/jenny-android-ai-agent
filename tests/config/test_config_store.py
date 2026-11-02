"""Il funnel di scrittura della config: nessuna modifica persa, niente file troncati.

Prima della 0.3.2 ogni scrittura era un leggi-modifica-riscrivi indipendente, e
`save_config` riscriveva il file intero: chi aveva letto prima e salvava dopo
cancellava in silenzio la modifica di un altro. Il caso reale era il pairing
Telegram, che teneva un `Config` in mano attraverso tre chiamate di rete.
"""

import asyncio
import json

import pytest
from loguru import logger as loguru_logger

from jenny.config import store
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config, ProviderConfig
from jenny.utils.path import backup_path as _backup_path


def _names(path) -> list[str]:
    return [p["name"] for p in json.loads(path.read_text(encoding="utf-8"))["providers"]["providers"]]


def _seed(path) -> None:
    config = Config()
    config.providers.providers = [
        ProviderConfig(name="deepseek", format="openai_compat", api_key="sk-deadbeefdeadbeef")
    ]
    config.providers.default = "deepseek"
    save_config(config, path)


def _add(name: str):
    def _apply(config: Config) -> None:
        config.providers.providers.append(
            ProviderConfig(name=name, format="openai_compat", api_key="EMPTY")
        )

    return _apply


async def test_concurrent_mutations_both_survive(tmp_path) -> None:
    """La regressione centrale: due scritture in volo, nessuna delle due sparisce."""
    path = tmp_path / "config.json"
    _seed(path)

    async def slow_add(name: str, delay: float) -> None:
        # Un chiamante che attende *prima* di entrare nel funnel: è il caso del
        # pairing Telegram, che fa I/O di rete e solo dopo modifica la config.
        await asyncio.sleep(delay)
        await store.mutate(_add(name), config_path=path)

    await asyncio.gather(slow_add("groq", 0.0), slow_add("local-llama", 0.01))

    assert _names(path) == ["deepseek", "groq", "local-llama"]


async def test_mutations_are_serialised(tmp_path) -> None:
    """Nessuna mutazione vede lo stato di mezzo di un'altra."""
    path = tmp_path / "config.json"
    _seed(path)
    seen: list[int] = []

    def _apply(config: Config) -> None:
        seen.append(len(config.providers.providers))
        config.providers.providers.append(
            ProviderConfig(name=f"p{len(seen)}", format="openai_compat", api_key="EMPTY")
        )

    await asyncio.gather(*(store.mutate(_apply, config_path=path) for _ in range(5)))

    # Ogni mutazione ha letto il risultato della precedente: 1, 2, 3, 4, 5.
    assert seen == [1, 2, 3, 4, 5]
    assert len(_names(path)) == 6


async def test_failed_mutation_leaves_the_file_untouched(tmp_path) -> None:
    path = tmp_path / "config.json"
    _seed(path)
    before = path.read_text(encoding="utf-8")

    def _boom(config: Config) -> None:
        config.providers.providers.clear()
        raise RuntimeError("validazione fallita")

    with pytest.raises(RuntimeError, match="validazione fallita"):
        await store.mutate(_boom, config_path=path)

    assert path.read_text(encoding="utf-8") == before
    assert _names(path) == ["deepseek"]


async def test_no_change_means_no_write(tmp_path) -> None:
    """Un handler che non cambia nulla non deve riscrivere il file né ruotare il backup."""
    path = tmp_path / "config.json"
    _seed(path)
    stamp = path.stat().st_mtime_ns

    await store.mutate(lambda config: False, config_path=path)

    assert path.stat().st_mtime_ns == stamp
    assert not _backup_path(path).exists()


async def test_unknown_keys_survive_a_mutation(tmp_path) -> None:
    """Le chiavi che lo schema non conosce non vengono cancellate da un salvataggio.

    Prima sparivano in silenzio: un refuso in un config.json scritto a mano
    veniva ignorato, e un downgrade cancellava le impostazioni della versione
    più nuova.
    """
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "unknownRootKey": "tienimi",
                "agents": {"defaults": {"model": "x", "futureSetting": 42}},
                "providers": {
                    "providers": [{"name": "p", "format": "openai_compat", "apiKey": "k"}],
                    "default": "p",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    await store.mutate(_add("groq"), config_path=path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["unknownRootKey"] == "tienimi"
    assert data["agents"]["defaults"]["futureSetting"] == 42
    assert [p["name"] for p in data["providers"]["providers"]] == ["p", "groq"]


async def test_unknown_keys_are_logged(tmp_path) -> None:
    """Un refuso in un config scritto a mano ora si vede almeno nei log."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"unknownRootKey": 1, "agents": {"defaults": {"futureSetting": 2}}}),
        encoding="utf-8",
    )
    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        load_config(path)
    finally:
        loguru_logger.remove(handler_id)

    warning = "\n".join(records)
    assert "unknownRootKey" in warning
    assert "agents.defaults.futureSetting" in warning


async def test_write_is_atomic_on_failure(tmp_path, monkeypatch) -> None:
    """Se la scrittura muore a metà, il file valido di prima resta valido.

    È lo scenario che su Android bloccava l'avvio del gateway: processo ucciso
    durante il salvataggio, `config.json` troncato.
    """
    path = tmp_path / "config.json"
    _seed(path)
    before = path.read_text(encoding="utf-8")

    real_open = open

    def exploding_open(file, mode="r", *args, **kwargs):
        if "w" in mode and ".tmp" in str(file):
            raise OSError("disco pieno a metà scrittura")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", exploding_open)
    with pytest.raises(OSError):
        await store.mutate(_add("groq"), config_path=path)
    monkeypatch.undo()

    assert path.read_text(encoding="utf-8") == before
    assert load_config(path).providers.providers[0].name == "deepseek"


async def test_backup_keeps_the_previous_good_content(tmp_path) -> None:
    path = tmp_path / "config.json"
    _seed(path)

    await store.mutate(_add("groq"), config_path=path)

    backup = _backup_path(path)
    assert backup.exists()
    # Il .bak è lo stato *precedente*, non quello appena scritto.
    assert [p["name"] for p in json.loads(backup.read_text(encoding="utf-8"))["providers"]["providers"]] == [
        "deepseek"
    ]


async def test_backup_is_not_overwritten_by_broken_content(tmp_path) -> None:
    """Un salvataggio partito da una config rotta non deve distruggere il backup buono."""
    path = tmp_path / "config.json"
    _seed(path)
    await store.mutate(_add("groq"), config_path=path)
    good_backup = _backup_path(path).read_text(encoding="utf-8")

    path.write_text("{troncato", encoding="utf-8")
    save_config(Config(), path)

    assert _backup_path(path).read_text(encoding="utf-8") == good_backup


async def test_writes_keep_the_config_unreadable_by_others(tmp_path) -> None:
    """`atomic_write` sostituisce l'inode: senza un chmod esplicito il 600 si perde.

    Il file (e il suo backup) portano le chiavi API e il secret che emette i
    token della WebUI: la restrizione va riapplicata a ogni scrittura.
    """
    import stat

    path = tmp_path / "config.json"
    _seed(path)
    await store.mutate(_add("groq"), config_path=path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(_backup_path(path).stat().st_mode) == 0o600
