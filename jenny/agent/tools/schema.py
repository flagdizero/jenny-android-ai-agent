"""JSON Schema fragment types: all subclass :class:`~jenny.agent.tools.base.Schema` for descriptions and constraints on tool parameters.

- ``to_json_schema()``: returns a dict compatible with :meth:`~jenny.agent.tools.base.Schema.validate_json_schema_value` /
  :class:`~jenny.agent.tools.base.Tool`.
- ``validate_value(value, path)``: validates a single value against this schema; returns a list of error messages (empty means valid).

Shared validation and fragment normalization are on the class methods of :class:`~jenny.agent.tools.base.Schema`.

Note: Python does not allow subclassing ``bool``, so booleans use :class:`BooleanSchema`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from jenny.agent.tools.base import Schema

# Tetto per ogni vincolo di ripetizione (`maxLength`, `maxItems`, e i rispettivi
# minimi) che finisce nello schema JSON inviato al provider.
#
# I server che vincolano il tool-calling con una grammatica — llama.cpp in testa
# — espandono quei vincoli in regole di ripetizione *letterali*, e llama.cpp ha
# due guard in `src/llama-grammar.cpp`, entrambi contro `MAX_REPETITION_THRESHOLD
# = 2000`:
#
#   1. `min_times > 2000 || max_times > 2000` → "number of repetitions exceeds
#      sane defaults";
#   2. `n_prev_rules * total_rules >= 2000` → "number of rules that are going to
#      be repeated multiplied by the new repetition exceeds sane defaults".
#
# Il secondo è quello che conta: moltiplica la ripetizione per la complessità
# della regola ripetuta, quindi il valore utile NON è 2000 ma una frazione che
# dipende dal resto della grammatica. Misurato contro llama-server b10210 con
# Qwen2.5-3B: `long_task` da solo passa con `goal` a 1000 e fallisce a 2000.
#
# Attenzione: essendo `n_prev_rules` cumulativo e la grammatica compilata
# dall'UNIONE di tutti i tool, restare sotto questa soglia è necessario ma non
# sufficiente — oltre una quindicina di tool la richiesta fallisce comunque, per
# complessità totale. Vedi `docs/reference/local-models.md`.
#
# Il limite è un vincolo di interoperabilità, non un'opinione di design:
# ``tests/agent/tools/test_schema_wire_limits.py`` lo verifica su tutti i tool
# registrati. Serve un campo più capiente? Va tolto il vincolo dallo schema e
# controllato in ``execute()`` (v. ``long_task``), non alzato oltre la soglia.
WIRE_STRING_LIMIT = 1000


class StringSchema(Schema):
    """String parameter: ``description`` documents the field; optional length bounds and enum."""

    def __init__(
        self,
        description: str = "",
        *,
        min_length: int | None = None,
        max_length: int | None = None,
        enum: tuple[Any, ...] | list[Any] | None = None,
        nullable: bool = False,
    ) -> None:
        self._description = description
        self._min_length = min_length
        self._max_length = max_length
        self._enum = tuple(enum) if enum is not None else None
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        t: Any = "string"
        if self._nullable:
            t = ["string", "null"]
        d: dict[str, Any] = {"type": t}
        if self._description:
            d["description"] = self._description
        if self._min_length is not None:
            d["minLength"] = self._min_length
        if self._max_length is not None:
            d["maxLength"] = self._max_length
        if self._enum is not None:
            d["enum"] = list(self._enum)
        return d


class _NumericSchema(Schema):
    """Base dei due schemi numerici: stessi campi, cambia solo il tipo JSON.

    ``IntegerSchema`` e ``NumberSchema`` erano due copie identiche a parte la
    parola ``"integer"``/``"number"``. Argomenti solo keyword: un posizionale era
    una descrizione che si perdeva (v. il commit che li ha resi tali).
    """

    _JSON_TYPE = ""
    # I tipi Python ammessi per limiti ed enum. ``bool`` e' un ``int`` per Python
    # ma non per JSON Schema, quindi si esclude a parte.
    _VALUE_TYPES: tuple[type, ...] = (int, float)

    def __init__(
        self,
        *,
        description: str = "",
        minimum: float | None = None,
        maximum: float | None = None,
        enum: Sequence[float] | None = None,
        nullable: bool = False,
    ) -> None:
        values = [v for v in (minimum, maximum) if v is not None] + list(enum or ())
        for value in values:
            if isinstance(value, bool) or not isinstance(value, self._VALUE_TYPES):
                raise TypeError(f"{type(self).__name__} bound {value!r} is not a valid value")
        self._description = description
        self._minimum = minimum
        self._maximum = maximum
        self._enum = tuple(enum) if enum is not None else None
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        t: Any = self._JSON_TYPE
        if self._nullable:
            t = [self._JSON_TYPE, "null"]
        d: dict[str, Any] = {"type": t}
        if self._description:
            d["description"] = self._description
        if self._minimum is not None:
            d["minimum"] = self._minimum
        if self._maximum is not None:
            d["maximum"] = self._maximum
        if self._enum is not None:
            d["enum"] = list(self._enum)
        return d


class IntegerSchema(_NumericSchema):
    """Integer parameter: description and optional bounds (keyword-only).

    Limiti ed enum sono interi, e un float si rifiuta: ``minimum=0.5`` su un
    parametro intero annuncia al modello uno schema che nessun valore valido
    rispetta nel modo in cui sembra.
    """

    _JSON_TYPE = "integer"
    _VALUE_TYPES = (int,)

    def __init__(
        self,
        *,
        description: str = "",
        minimum: int | None = None,
        maximum: int | None = None,
        enum: Sequence[int] | None = None,
        nullable: bool = False,
    ) -> None:
        super().__init__(
            description=description,
            minimum=minimum,
            maximum=maximum,
            enum=enum,
            nullable=nullable,
        )


class NumberSchema(_NumericSchema):
    """Numeric parameter (JSON number): description and optional bounds (keyword-only)."""

    _JSON_TYPE = "number"


class BooleanSchema(Schema):
    """Boolean parameter (standalone class because Python forbids subclassing ``bool``)."""

    def __init__(
        self,
        *,
        description: str = "",
        default: bool | None = None,
        nullable: bool = False,
    ) -> None:
        self._description = description
        self._default = default
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        t: Any = "boolean"
        if self._nullable:
            t = ["boolean", "null"]
        d: dict[str, Any] = {"type": t}
        if self._description:
            d["description"] = self._description
        if self._default is not None:
            d["default"] = self._default
        return d


class ArraySchema(Schema):
    """Array parameter: element schema is given by ``items``."""

    def __init__(
        self,
        items: Any | None = None,
        *,
        description: str = "",
        min_items: int | None = None,
        max_items: int | None = None,
        nullable: bool = False,
    ) -> None:
        self._items_schema: Any = items if items is not None else StringSchema("")
        self._description = description
        self._min_items = min_items
        self._max_items = max_items
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        t: Any = "array"
        if self._nullable:
            t = ["array", "null"]
        d: dict[str, Any] = {
            "type": t,
            "items": Schema.fragment(self._items_schema),
        }
        if self._description:
            d["description"] = self._description
        if self._min_items is not None:
            d["minItems"] = self._min_items
        if self._max_items is not None:
            d["maxItems"] = self._max_items
        return d


class ObjectSchema(Schema):
    """Object parameter: ``properties`` or keyword args are field names; values are child Schema or JSON Schema dicts."""

    def __init__(
        self,
        properties: Mapping[str, Any] | None = None,
        *,
        required: list[str] | None = None,
        description: str = "",
        additional_properties: bool | dict[str, Any] | None = None,
        nullable: bool = False,
        **kwargs: Any,
    ) -> None:
        self._properties = dict(properties or {}, **kwargs)
        self._required = list(required or [])
        self._root_description = description
        self._additional_properties = additional_properties
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        t: Any = "object"
        if self._nullable:
            t = ["object", "null"]
        props = {k: Schema.fragment(v) for k, v in self._properties.items()}
        out: dict[str, Any] = {"type": t, "properties": props}
        if self._required:
            out["required"] = self._required
        if self._root_description:
            out["description"] = self._root_description
        if self._additional_properties is not None:
            out["additionalProperties"] = self._additional_properties
        return out


def tool_parameters_schema(
    *,
    required: list[str] | None = None,
    description: str = "",
    **properties: Any,
) -> dict[str, Any]:
    """Build root tool parameters ``{"type": "object", "properties": ...}`` for :meth:`Tool.parameters`."""
    return ObjectSchema(
        required=required,
        description=description,
        **properties,
    ).to_json_schema()
