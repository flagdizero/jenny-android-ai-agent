"""Configuration loading utilities.

Questo modulo possiede la *fedeltà del file*: come `config.json` viene letto,
riscritto senza perdere pezzi, e recuperato quando è illeggibile. La
serializzazione delle modifiche concorrenti sta invece in
:mod:`jenny.config.store`, che orchestra queste primitive sotto un lock.
"""

import json
import os
import re
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from loguru import logger

from jenny.config.bootstrap import write_private_file
from jenny.config.schema import Config
from jenny.pydantic_compat import (
    BaseModel,
    ValidationError,
    canonical_input_key,
    field_for_input_key,
    lenient_literals,
)


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
    shadowed = _shadowed_key_paths(raw)
    if shadowed:
        logger.warning(
            "Config keys written twice under two spellings (the camelCase one is used; "
            "these are ignored and dropped on the next write): {}",
            ", ".join(shadowed),
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
        return raw, _validate(raw, path)
    except (json.JSONDecodeError, ValueError, ValidationError) as primary_error:
        logger.error("Config at {} is unusable: {}", path, primary_error)

    backup = _backup_path(path)
    if backup.exists():
        try:
            raw = _read_raw(backup)
            config = _validate(raw, backup)
        except (json.JSONDecodeError, ValueError, ValidationError) as backup_error:
            logger.error("Config backup at {} is unusable too: {}", backup, backup_error)
        else:
            quarantined = _quarantine(path)
            # Promuoviamo il backup a file vivo: senza questo passo ogni avvio
            # rifarebbe il recupero, e la prima scrittura riuscita partirebbe
            # da un grezzo rotto.
            write_private_file(path, json.dumps(raw, indent=2, ensure_ascii=False))
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


def _validate(raw: dict[str, Any], path: Path) -> Config:
    """Valida *raw*; un valore fuori da un ``Literal`` costa solo il suo campo.

    Il campo ricade sul default e lo si dice a WARNING: il resto del file vale.
    Prima quel valore faceva rifiutare il file intero, e con lui il ``.bak`` che
    lo porta uguale — si ripartiva sui default di tutto.
    """
    with lenient_literals() as fallbacks:
        config = Config.model_validate(raw)
    for model, field, value in fallbacks:
        logger.warning(
            "Config at {}: {}.{} = {!r} is not a value this version knows; using the "
            "default instead (the next write replaces it)",
            path, model, field, value,
        )
    return config


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

    resolved = get_runtime_context().device_timezone or "UTC"
    config.agents.defaults.timezone = resolved
    # Il valore dato alla sentinella, per :func:`_unresolve_default_timezone`:
    # senza fuso rilevato "" diventa "UTC", e il confronto col solo fuso del
    # device non lo riconosceva — la prima scrittura congelava "UTC" nel file.
    config._auto_timezone = resolved


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
    _unresolve_default_timezone(data, config)
    if preserve_unknown_from:
        data = _merge_unknown(preserve_unknown_from, data)

    _rotate_backup(path)
    write_private_file(path, json.dumps(data, indent=2, ensure_ascii=False))


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
        write_private_file(backup, content, fsync_dir=False)
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
    # L'heartbeat, ritirato il 31/10/2026: un controllo periodico ora è un job
    # ``monitor`` del cron con la sua cadenza (``jenny/cron/``).
    "gateway.heartbeat",
})


# La forma di un nodo del JSON, per sapere quali chiavi vi sono campi del modello:
# ``("model", M)`` un oggetto validato da ``M``; ``("dict", M)`` una mappa con
# chiavi libere (i nomi dei preset) e valori ``M``; ``None`` un nodo senza schema
# (``websocket``, un ``dict[str, Any]``), dove ogni chiave e' dato e non campo.
_Shape = tuple[str, type[BaseModel]] | None


def _shape_of(annotation: Any) -> _Shape:
    """La forma di un valore annotato *annotation*, se porta dentro un modello."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return ("model", annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        for arg in args:
            shape = _shape_of(arg)
            if shape is not None and shape[0] == "model":
                return shape
        return None
    if origin is dict and len(args) == 2:
        inner = _shape_of(args[1])
        if inner is not None and inner[0] == "model":
            return ("dict", inner[1])
    return None


def _classify_key(shape: _Shape, key: str) -> tuple[str, _Shape]:
    """Che cosa e' *key* in un nodo di forma *shape*, e la forma del suo valore.

    ``"field"`` la chiave con cui il modello scrive un suo campo; ``"synonym"`` un
    campo noto scritto con un'altra grafia (``max_tokens`` per ``maxTokens``), che
    il modello legge e riscrive con la sua; ``"other"`` tutto il resto: una chiave
    ignota a questo schema, o un dato di un nodo senza schema.
    """
    if shape is None:
        return "other", None
    kind, model = shape
    if kind == "dict":
        return "other", ("model", model)
    if not model.__pydantic_rebuilt__:
        model.model_rebuild(raise_errors=False)
    field = field_for_input_key(model, key)
    if field is None:
        return "other", None
    finfo = model.model_fields[field]
    child = _shape_of(finfo.resolved_type if finfo.resolved_type is not None else finfo.annotation)
    if key != canonical_input_key(model, field):
        return "synonym", child
    return "field", child


def _merge_unknown(
    raw: Any, dumped: Any, prefix: str = "", shape: _Shape = ("model", Config)
) -> Any:
    """Restituisce *dumped* con le chiavi presenti solo in *raw* riportate dentro.

    Ricorsivo sui dizionari. Le liste vengono sostituite in blocco: allineare
    gli elementi richiederebbe una nozione di identità (quale provider è
    quale) che qui non abbiamo, e indovinarla è peggio che perdere una chiave
    ignota dentro un elemento di array — caso segnalato comunque dal warning
    in ``load_config_with_raw``.

    Le chiavi in :data:`RETIRED_KEY_PATHS` **non** vengono riportate: e' l'unico
    punto in cui una chiave ritirata smette di esistere nel file.

    Nemmeno un campo noto scritto con un'altra grafia (``max_tokens`` accanto al
    ``maxTokens`` del dump): e' lo stesso campo, e il dump lo porta gia' con la
    grafia del modello. Riportarlo lo lasciava nel file in coda al dump, dove
    alla lettura dopo vinceva lui: ogni modifica dalla UI si salvava e non aveva
    effetto, per sempre.
    """
    if not isinstance(raw, dict) or not isinstance(dumped, dict):
        return dumped
    merged = dict(dumped)
    for key, raw_value in raw.items():
        where = f"{prefix}{key}"
        role, child = _classify_key(shape, key)
        if role == "synonym":
            continue
        if key not in merged:
            if where not in RETIRED_KEY_PATHS:
                merged[key] = raw_value
        else:
            merged[key] = _merge_unknown(raw_value, merged[key], f"{where}.", child)
    return merged


def _unknown_key_paths(
    raw: Any, dumped: Any, prefix: str = "", shape: _Shape = ("model", Config)
) -> list[str]:
    """Elenca i percorsi delle chiavi presenti in *raw* ma non nel dump del modello.

    Una chiave ritirata non e' sconosciuta: non compare, o il warning che questa
    lista alimenta suonerebbe a ogni caricamento fino alla prima riscrittura.
    Nemmeno un campo noto scritto con un'altra grafia: quello il modello lo
    legge (v. :func:`_shadowed_key_paths` per il caso in cui non lo legge).
    """
    if not isinstance(raw, dict) or not isinstance(dumped, dict):
        return []
    unknown: list[str] = []
    for key, raw_value in raw.items():
        where = f"{prefix}{key}"
        role, child = _classify_key(shape, key)
        if role == "synonym":
            continue
        if key not in dumped:
            if where not in RETIRED_KEY_PATHS:
                unknown.append(where)
            continue
        if isinstance(raw_value, dict):
            unknown.extend(_unknown_key_paths(raw_value, dumped[key], f"{where}.", child))
        elif isinstance(raw_value, list) and isinstance(dumped.get(key), list):
            item_shape = _list_item_shape(shape, key)
            for i, (raw_item, dumped_item) in enumerate(zip(raw_value, dumped[key])):
                unknown.extend(
                    _unknown_key_paths(raw_item, dumped_item, f"{where}[{i}].", item_shape)
                )
    return unknown


def _list_item_shape(shape: _Shape, key: str) -> _Shape:
    """La forma degli elementi della lista che il campo *key* di *shape* contiene."""
    if shape is None or shape[0] != "model":
        return None
    model = shape[1]
    field = field_for_input_key(model, key)
    if field is None:
        return None
    finfo = model.model_fields[field]
    annotation = finfo.resolved_type if finfo.resolved_type is not None else finfo.annotation
    if get_origin(annotation) is list and get_args(annotation):
        inner = _shape_of(get_args(annotation)[0])
        if inner is not None and inner[0] == "model":
            return inner
    return None


def _shadowed_key_paths(
    raw: Any, prefix: str = "", shape: _Shape = ("model", Config)
) -> list[str]:
    """Le grafie di un campo che il modello **non** legge, perche' c'e' anche la sua.

    ``max_tokens`` da solo si legge (e alla prossima scrittura diventa
    ``maxTokens``); ``max_tokens`` accanto a ``maxTokens`` no: vince la grafia del
    modello, e questa sparisce alla prossima scrittura. E' l'unico caso in cui un
    valore scritto nel file non ha effetto, e va detto.
    """
    if not isinstance(raw, dict) or shape is None:
        return []
    shadowed: list[str] = []
    for key, raw_value in raw.items():
        where = f"{prefix}{key}"
        role, child = _classify_key(shape, key)
        if role == "synonym" and shape[0] == "model":
            field = field_for_input_key(shape[1], key)
            if field is not None and canonical_input_key(shape[1], field) in raw:
                shadowed.append(where)
        if isinstance(raw_value, dict):
            shadowed.extend(_shadowed_key_paths(raw_value, f"{where}.", child))
        elif isinstance(raw_value, list):
            item_shape = _list_item_shape(shape, key)
            for i, item in enumerate(raw_value):
                shadowed.extend(_shadowed_key_paths(item, f"{where}[{i}].", item_shape))
    return shadowed


def _unresolve_default_timezone(data: dict[str, Any], config: Config) -> None:
    """Riporta a "auto" la timezone risolta prima della persistenza.

    ``load_config`` risolve la sentinella vuota nella timezone del device;
    senza questo passo ogni salvataggio la congelerebbe come valore esplicito
    (e smetterebbe di seguire i cambi di timezone del dispositivo). Se il
    valore coincide con la timezone del device, o con quella che il
    caricamento ha dato a ``""`` (``UTC`` quando il fuso non si rileva), si
    riscrive ``""`` (= auto); una scelta esplicita diversa viene persistita
    normalmente.
    """
    from jenny.runtime.context import get_runtime_context

    auto_values = {
        get_runtime_context().device_timezone,
        getattr(config, "_auto_timezone", None),
    } - {None, ""}
    defaults = data.get("agents", {}).get("defaults")
    if isinstance(defaults, dict) and defaults.get("timezone") in auto_values:
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
