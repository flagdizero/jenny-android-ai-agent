"""I nomi delle Jenny App non accettano un "a capo" finale.

``SLUG_RE``, ``ACTION_NAME_RE`` e ``COLLECTION_RE`` erano ancorate con
``^…$`` e usate con ``.match``: in Python ``$`` accetta anche un ``\\n`` in
fondo, quindi ``"todo\\n"`` passava. Per slug e azioni l'effetto era solo un
«non trovato» più in là; per una collezione ``storage._collection_path``
costruiva ``data/notes\\n.jsonl`` — un file con l'a capo nel nome. È lo stesso
difetto corretto sugli id del filo in ``security/wire_ids.py``. Trovato dalla
revisione finale della pulizia (25/09).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from jenny.apps import manifest
from jenny.apps.storage import StorageError, _collection_path

VALIDATOR = (
    Path(__file__).resolve().parents[2] / "jenny/skills/app-creator/scripts/validate_app.py"
)


@pytest.mark.parametrize("regex", ["SLUG_RE", "ACTION_NAME_RE", "COLLECTION_RE"])
def test_a_trailing_newline_is_refused(regex: str) -> None:
    pattern = getattr(manifest, regex)
    good = "todo" if regex != "ACTION_NAME_RE" else "add_todo"
    assert pattern.match(good)
    assert pattern.match(good + "\n") is None


def test_a_collection_with_a_newline_never_becomes_a_path(tmp_path: Path) -> None:
    assert _collection_path(tmp_path, "notes").name == "notes.jsonl"
    with pytest.raises(StorageError):
        _collection_path(tmp_path, "notes\n")


def test_the_skill_validator_uses_the_same_patterns() -> None:
    """Il commento in ``manifest.py`` le vuole identiche a quelle dello script
    della skill che crea le app: chi ne cambia una deve cambiare l'altra."""
    spec = importlib.util.spec_from_file_location("validate_app", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("SLUG_RE", "ACTION_NAME_RE", "COLLECTION_RE"):
        assert getattr(module, name).pattern == getattr(manifest, name).pattern, name
