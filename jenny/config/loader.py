"""Configuration loading utilities.

Questo modulo possiede la *fedeltà del file*: come `config.json` viene letto,
riscritto senza perdere pezzi, e recuperato quando è illeggibile. La
serializzazione delle modifiche concorrenti sta invece in
:mod:`jenny.config.store`, che orchestra queste primitive sotto un lock.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.config.bootstrap import restrict_config_permissions
from jenny.config.schema import Config
from jenny.pydantic_compat import BaseModel, ValidationError
from jenny.utils.path import atomic_write


def get_config_path() -> Path:
    """Get the configuration file path.

    Usa l'override ``RuntimeContext.config_path`` se impostato (usato dai test e
    da eventuali istanze multiple), altrimenti ``workspace/config.json``.
    """
    from jenny.runtime.context import get_runtime_context

    override = get_runtime_context().config_path
    if override:
        return override
    from jenny.config.paths import get_workspace_path
    return get_workspace_path() / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    return load_config_with_raw(config_path)[0]


def load_config_with_raw(
    config_path: Path | None = None,
) -> tuple[Config, dict[str, Any]]:
    """Come :func:`load_config`, ma restituisce anche il JSON grezzo letto.

    Il grezzo serve a :func:`save_config` per non cancellare le chiavi che lo
    schema non conosce (vedi ``preserve_unknown_from``). Chi deve solo leggere
    usa ``load_config``.
    """
    path = config_path or get_config_path()
    raw, config = _load_with_recovery(path)

    unknown = _unknown_key_paths(raw, config.model_dump(mode="json", by_alias=True))
    if unknown:
        # Non è un errore: possono essere impostazioni di una versione più
        # nuova. Ma se è un refuso, questo è l'unico posto dove l'utente può
        # accorgersene — prima sparivano senza dire niente.
        logger.warning(
            "Config keys not recognised by this version (kept in the file, ignored at runtime): {}",
            ", ".join(unknown),
        )

    _apply_ssrf_whitelist(config)
    _resolve_default_timezone(config)
    return config, raw


def _load_with_recovery(path: Path) -> tuple[dict[str, Any], Config]:
    """Legge e valida *path*, ripiegando su backup o default se è illeggibile.

    Su Android un `config.json` illeggibile bloccava l'avvio del gateway, e
    l'utente non ha modo di ripararlo: preferiamo partire sempre, dicendolo.
    """
    if not path.exists():
        return {}, Config()

    try:
        raw = _read_raw(path)
        return raw, Config.model_validate(raw)
    except (json.JSONDecodeError, ValueError, ValidationError) as primary_error:
        logger.error("Config at {} is unusable: {}", path, primary_error)

    backup = _backup_path(path)
    if backup.exists():
        try:
            raw = _read_raw(backup)
            config = Config.model_validate(raw)
        except (json.JSONDecodeError, ValueError, ValidationError) as backup_error:
            logger.error("Config backup at {} is unusable too: {}", backup, backup_error)
        else:
            quarantined = _quarantine(path)
            # Promuoviamo il backup a file vivo: senza questo passo ogni avvio
            # rifarebbe il recupero, e la prima scrittura riuscita partirebbe
            # da un grezzo rotto.
            atomic_write(path, json.dumps(raw, indent=2, ensure_ascii=False))
            restrict_config_permissions(path)
            _record_recovery("backup", quarantined)
            logger.warning("Config recovered from {}; broken file kept at {}", backup, quarantined)
            return raw, config

    quarantined = _quarantine(path)
    _record_recovery("defaults", quarantined)
    logger.warning(
        "Config could not be recovered; starting on defaults. Broken file kept at {}",
        quarantined,
    )
    return {}, Config()


def _read_raw(path: Path) -> dict[str, Any]:
    """Legge il JSON grezzo, pretendendo un oggetto in radice."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a JSON object, got {type(data).__name__}")
    return data


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _quarantine(path: Path) -> Path:
    """Sposta di lato il file rotto, senza distruggerlo: serve per capire cosa è successo."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
    try:
        os.replace(path, target)
    except OSError as e:
        logger.error("Could not set aside the broken config at {}: {}", path, e)
        return path
    return target


def _record_recovery(kind: str, quarantined: Path) -> None:
    """Segna sul RuntimeContext che la config è stata recuperata.

    La WebUI legge questo flag e lo mostra: ripartire con impostazioni diverse
    da quelle scelte dall'utente senza dirglielo sarebbe la sorpresa peggiore.
    """
    from jenny.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    ctx.config_recovered_from = kind
    ctx.config_quarantine_path = quarantined


def _resolve_default_timezone(config: Config) -> None:
    """Risolve la timezone "auto" (stringa vuota) in un valore concreto.

    Avviene una sola volta qui, nel funnel unico di caricamento: tutti i
    consumer a valle (container, cron, tool, settings) vedono sempre un nome
    concreto — la timezone del dispositivo se rilevata, altrimenti UTC.
    """
    if config.agents.defaults.timezone.strip():
        return
    from jenny.runtime.context import get_runtime_context

    config.agents.defaults.timezone = get_runtime_context().device_timezone or "UTC"


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply SSRF whitelist from config to the network security module."""
    from jenny.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.security.ssrf_whitelist)


def save_config(
    config: Config,
    config_path: Path | None = None,
    *,
    preserve_unknown_from: dict[str, Any] | None = None,
) -> None:
    """
    Save configuration to file.

    La scrittura è atomica (temporaneo + ``os.replace`` + fsync via
    :func:`jenny.utils.path.atomic_write`): un processo ucciso a metà lasciava
    un JSON troncato, e con esso un gateway che non parte più.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
        preserve_unknown_from: JSON grezzo da cui riportare le chiavi che lo
            schema non conosce, così un salvataggio non le cancella. Lo passa
            :mod:`jenny.config.store`; chi riscrive tutto di proposito (il
            ripristino da backup) lo lascia a None.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)
    _unresolve_default_timezone(data)
    if preserve_unknown_from:
        data = _merge_unknown(preserve_unknown_from, data)

    _rotate_backup(path)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
    restrict_config_permissions(path)


def _rotate_backup(path: Path) -> None:
    """Conserva l'ultimo contenuto *valido* come ``<nome>.bak``.

    Solo se il file attuale si legge come JSON: altrimenti un salvataggio
    partito da una config già rotta distruggerebbe l'ultimo backup buono.
    """
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8")
        json.loads(content)
    except (OSError, json.JSONDecodeError):
        return
    try:
        backup = _backup_path(path)
        atomic_write(backup, content, fsync_dir=False)
        restrict_config_permissions(backup)
    except OSError as e:
        # Il backup è una rete di sicurezza, non un requisito: se non si può
        # scrivere, il salvataggio vero deve comunque procedere.
        logger.warning("Could not refresh the config backup: {}", e)


# Chiavi che una versione precedente scriveva e questa **ha ritirato**. Sono la
# terza specie, dopo "conosciuta" e "sconosciuta", e serve perche' le altre due
# non la coprono: ``_merge_unknown`` conserva per progetto quel che lo schema non
# conosce (potrebbe venire da una versione piu' nuova), quindi una chiave tolta
# dallo schema resterebbe nel file **per sempre** — e con lei il warning di
# ``load_config_with_raw`` a ogni caricamento. Qui non si avvisa e non si
# conserva: alla prossima scrittura cade. Percorsi con l'alias JSON (camelCase)
# e, dove il file puo' portarla, la forma snake_case.
RETIRED_KEY_PATHS: frozenset[str] = frozenset({
    "agents.defaults.atlas",
    "wiki.defaultWiki",
    "wiki.default_wiki",
    # Il modello della richiesta dell'umore, ritirata il 24/09/2026 con la
    # richiesta stessa: l'umore si legge dagli emoji (``session/mascot_mood.py``).
    "agents.defaults.mascotMoodModelPreset",
    "agents.defaults.mascot_mood_model_preset",
    # Le estensioni Markdown della wiki, ritirate il 24/09/2026: il renderer
    # non le ha mai lette (``webui/wiki.py`` usa le sue), e la documentazione
    # le dava per configurabili. Ogni file le porta, perche' il dump scriveva
    # anche i default.
    "wiki.extensions",
    # Il blocco delle pagine della casa prima del rinomino in inglese del
    # 25/09/2026: ``casa: {schermate, ordine}``. Lo traduce in ``home`` lo schema
    # (``Config._migrate_casa_to_home``); qui smette di esistere nel file.
    "casa",
})


def _merge_unknown(raw: Any, dumped: Any, prefix: str = "") -> Any:
    """Restituisce *dumped* con le chiavi presenti solo in *raw* riportate dentro.

    Ricorsivo sui dizionari. Le liste vengono sostituite in blocco: allineare
    gli elementi richiederebbe una nozione di identità (quale provider è
    quale) che qui non abbiamo, e indovinarla è peggio che perdere una chiave
    ignota dentro un elemento di array — caso segnalato comunque dal warning
    in ``load_config_with_raw``.

    Le chiavi in :data:`RETIRED_KEY_PATHS` **non** vengono riportate: e' l'unico
    punto in cui una chiave ritirata smette di esistere nel file.
    """
    if not isinstance(raw, dict) or not isinstance(dumped, dict):
        return dumped
    merged = dict(dumped)
    for key, raw_value in raw.items():
        where = f"{prefix}{key}"
        if key not in merged:
            if where not in RETIRED_KEY_PATHS:
                merged[key] = raw_value
        else:
            merged[key] = _merge_unknown(raw_value, merged[key], f"{where}.")
    return merged


def _unknown_key_paths(raw: Any, dumped: Any, prefix: str = "") -> list[str]:
    """Elenca i percorsi delle chiavi presenti in *raw* ma non nel dump del modello.

    Una chiave ritirata non e' sconosciuta: non compare, o il warning che questa
    lista alimenta suonerebbe a ogni caricamento fino alla prima riscrittura.
    """
    if not isinstance(raw, dict) or not isinstance(dumped, dict):
        return []
    unknown: list[str] = []
    for key, raw_value in raw.items():
        where = f"{prefix}{key}"
        if key not in dumped:
            if where not in RETIRED_KEY_PATHS:
                unknown.append(where)
            continue
        if isinstance(raw_value, dict):
            unknown.extend(_unknown_key_paths(raw_value, dumped[key], f"{where}."))
        elif isinstance(raw_value, list) and isinstance(dumped.get(key), list):
            for i, (raw_item, dumped_item) in enumerate(zip(raw_value, dumped[key])):
                unknown.extend(_unknown_key_paths(raw_item, dumped_item, f"{where}[{i}]."))
    return unknown


def _unresolve_default_timezone(data: dict[str, Any]) -> None:
    """Riporta a "auto" la timezone risolta prima della persistenza.

    ``load_config`` risolve la sentinella vuota nella timezone del device;
    senza questo passo ogni salvataggio la congelerebbe come valore esplicito
    (e smetterebbe di seguire i cambi di timezone del dispositivo). Se il
    valore coincide con la timezone del device si riscrive ``""`` (= auto);
    una scelta esplicita diversa viene persistita normalmente.
    """
    from jenny.runtime.context import get_runtime_context

    device_tz = get_runtime_context().device_timezone
    if not device_tz:
        return
    defaults = data.get("agents", {}).get("defaults")
    if isinstance(defaults, dict) and defaults.get("timezone") == device_tz:
        defaults["timezone"] = ""


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_config_env_vars(config: Config) -> Config:
    """Return *config* with ``${VAR}`` env-var references resolved.

    Walks in place so fields declared with ``exclude=True`` survive;
    returns the same instance when no references are present.
    Raises ``ValueError`` if a referenced variable is not set.
    """
    return _resolve_in_place(config)


def _resolve_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        _check_nested_env_refs(obj)
        new = _ENV_REF_PATTERN.sub(_env_replace, obj)
        return new if new != obj else obj
    if isinstance(obj, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _resolve_in_place(old)
            if new is not old:
                updates[name] = new
        extras = obj.__pydantic_extra__
        new_extras: dict[str, Any] | None = None
        if extras:
            resolved = {k: _resolve_in_place(v) for k, v in extras.items()}
            if any(resolved[k] is not extras[k] for k in extras):
                new_extras = resolved
        if not updates and new_extras is None:
            return obj
        copy = obj.model_copy(update=updates) if updates else obj.model_copy()
        if new_extras is not None:
            copy.__pydantic_extra__ = new_extras
        return copy
    if isinstance(obj, dict):
        resolved = {k: _resolve_in_place(v) for k, v in obj.items()}
        return resolved if any(resolved[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        resolved = [_resolve_in_place(v) for v in obj]
        return resolved if any(nv is not ov for nv, ov in zip(resolved, obj)) else obj
    return obj


def _check_nested_env_refs(value: str) -> None:
    """Reject a malformed/nested ``${...}`` pattern such as ``${OUTER${INNER}}``.

    ``_ENV_REF_PATTERN`` matches the *innermost* ``${...}`` span, so a nested
    reference like ``${OUTER${INNER}}`` would otherwise resolve only
    ``${INNER}`` and silently leave a mangled literal (e.g. ``${OUTERabc}``)
    in the config. Detect a second ``${`` opening before the matching ``}``
    of an already-open ``${`` and raise instead of producing that value.

    Back-to-back, non-nested references like ``${VAR1}${VAR2}`` are valid
    (each ``${`` is closed before the next one opens) and are not affected.
    """
    depth = 0
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "$" and i + 1 < n and value[i + 1] == "{":
            if depth > 0:
                raise ValueError(
                    "Malformed nested '${...}' reference in config value: "
                    f"{value!r}"
                )
            depth = 1
            i += 2
            continue
        if value[i] == "}" and depth > 0:
            depth = 0
        i += 1


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value
