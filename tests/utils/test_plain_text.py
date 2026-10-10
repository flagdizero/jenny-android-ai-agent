"""``plain_text``: il markdown dell'agente come lo legge una notifica."""

from __future__ import annotations

import pytest

from jenny.utils.plain_text import plain_text


@pytest.mark.parametrize(
    ("md", "plain"),
    [
        ("**Fatto!** Ecco *tutto*.", "Fatto! Ecco tutto."),
        ("__grassetto__ e _corsivo_ e ~~via~~", "grassetto e corsivo e via"),
        ("## Titolo\ntesto", "Titolo\ntesto"),
        ("Vedi [il file](https://example.com/a?b=c) qui", "Vedi il file qui"),
        ("![una foto](media/x.png)", "una foto"),
        ("- uno\n- due\n* tre\n1. quattro", "uno\ndue\ntre\nquattro"),
        ("> citato", "citato"),
        ("sopra\n\n---\n\nsotto", "sopra\n\n\n\nsotto"),
        ("usa `a*b*c` qui", "usa a*b*c qui"),
        ("```python\nx = **2**\n```\ndopo", "x = **2**\ndopo"),
        ("snake_case_name resta", "snake_case_name resta"),
        ("", ""),
    ],
)
def test_markup_goes_and_text_stays(md: str, plain: str) -> None:
    assert plain_text(md) == plain
