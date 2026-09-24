"""Cosa emettono ``IntegerSchema`` e ``NumberSchema``: la forma che arriva al modello."""

from __future__ import annotations

import pytest

from jenny.agent.tools.schema import IntegerSchema, NumberSchema

CASES = [
    (IntegerSchema(), {"type": "integer"}),
    (IntegerSchema(description="Quanti", minimum=1, maximum=10),
     {"type": "integer", "description": "Quanti", "minimum": 1, "maximum": 10}),
    (IntegerSchema(enum=(1, 2, 3)), {"type": "integer", "enum": [1, 2, 3]}),
    (IntegerSchema(nullable=True, minimum=0), {"type": ["integer", "null"], "minimum": 0}),
    (NumberSchema(), {"type": "number"}),
    (NumberSchema(description="Temperatura", minimum=0.0, maximum=2.0),
     {"type": "number", "description": "Temperatura", "minimum": 0.0, "maximum": 2.0}),
    (NumberSchema(enum=[0.5, 1.0]), {"type": "number", "enum": [0.5, 1.0]}),
    (NumberSchema(nullable=True), {"type": ["number", "null"]}),
]


@pytest.mark.parametrize(("schema", "expected"), CASES)
def test_json_schema(schema, expected) -> None:
    assert schema.to_json_schema() == expected


def test_a_positional_value_is_refused() -> None:
    """Dal 24/09/2026 solo keyword: un posizionale era una descrizione persa."""
    with pytest.raises(TypeError):
        IntegerSchema(5)  # type: ignore[misc]
    with pytest.raises(TypeError):
        NumberSchema(0.5)  # type: ignore[misc]
