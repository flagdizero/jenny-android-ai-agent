"""I messaggi dello schema della casa sono in inglese (Q6 della revisione).

Finiscono nel log (``casa page dropped (...)``) e nella risposta di un
salvataggio rifiutato: AGENTS.md vuole i log in inglese, e un rifiuto che il
client inoltra non deve mescolare due lingue.
"""

from __future__ import annotations

import pytest

from jenny.config.schema import SchermataConfig


@pytest.mark.parametrize(
    ("riga", "atteso"),
    [
        ({"id": "p1", "kind": "stanza", "ref": "x"}, "unknown page kind: 'stanza'"),
        ({"id": "p1", "kind": "conversazione", "ref": "piante"}, "needs a notebook"),
        ({"id": "p1", "kind": "conversazione", "ref": "project:a/b"}, "invalid notebook name"),
    ],
)
def test_a_refused_page_says_why_in_english(riga, atteso) -> None:
    with pytest.raises(ValueError, match=atteso):
        SchermataConfig(**riga)
