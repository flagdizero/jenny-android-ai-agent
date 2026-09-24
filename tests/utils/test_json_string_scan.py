"""I due lettori di una stringa JSON in un argomento che arriva a pezzi.

``_extract_json_string_prefix`` serve durante lo streaming (quanto testo c'è
finora), ``_extract_complete_json_string`` alla fine (il valore, se la stringa è
chiusa). Erano due copie dello stesso scanner che differivano solo in cosa
rendere quando la stringa non è finita. Questa tabella fissa entrambi.
"""

from __future__ import annotations

import pytest

from jenny.utils.file_edit_streaming import (
    _extract_complete_json_string,
    _extract_json_string_prefix,
)

# (sorgente, prefisso atteso, completo atteso)
CASES = {
    "simple": ('{"content": "ciao"}', "ciao", "ciao"),
    "spaces_around_colon": ('{"content" :  "ciao"}', "ciao", "ciao"),
    "escapes": (r'{"content": "a\nb\tc\rd"}', "a\nb\tc\rd", "a\nb\tc\rd"),
    "quote_and_backslash": (r'{"content": "dice \"sì\" e \\ basta"}', 'dice "sì" e \\ basta',
                            'dice "sì" e \\ basta'),
    # \b, \f e \/ restano la lettera dopo il backslash: comportamento storico.
    "letter_escapes": (r'{"content": "x\by\fz\/w"}', "xbyfz/w", "xbyfz/w"),
    "unicode": (r'{"content": "perché"}', "perché", "perché"),
    "unterminated": ('{"content": "a metà', "a metà", None),
    "truncated_unicode": (r'{"content": "ok\u00', "ok", None),
    "invalid_unicode": (r'{"content": "ok\uZZZZ dopo"}', "ok", None),
    "backslash_at_end": ('{"content": "abc\\', "abc", None),
    "absent_key": ('{"other": "x"}', None, None),
    "empty_string": ('{"content": ""}', "", ""),
    "first_occurrence_wins": ('{"content": "uno", "content": "due"}', "uno", "uno"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_prefix_and_complete(name: str) -> None:
    source, prefix, complete = CASES[name]
    assert _extract_json_string_prefix(source, "content") == prefix
    assert _extract_complete_json_string(source, "content") == complete
