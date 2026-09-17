"""I limiti che le rotte impongono devono venire dallo schema, non da una copia.

``AgentDefaults.tool_hint_max_length`` dichiara ``ge=20, le=500``, e
``settings_api`` li aveva **riscritti a mano** (``if parsed < 20 or parsed >
500``). Due posti per lo stesso numero significa che a un cambio di schema il
messaggio d'errore — che nomina il range — è il primo a mentire: direbbe
«between 20 and 500» mentre il validatore ne accetta un altro.

``_parse_int`` li deriva da ``model_fields``. Questi test verificano che continui
a farlo, e che nessuno rimetta un letterale nel modulo.

Nota sul metodo: verificato per mutazione (schema portato a ``ge=30, le=250``, il
messaggio ha seguito). Attenzione al ``.pyc`` — ripristinare lo schema nello
stesso secondo riusa il bytecode in cache e la prova mente in entrambe le
direzioni; serve svuotare ``__pycache__``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from jenny.config.schema import AgentDefaults, DreamConfig, FloatingConfig, GardenerConfig
from jenny.webui.settings_api import WebUISettingsError, _bounds, _parse_int

SETTINGS_API = Path(__file__).resolve().parents[2] / "jenny" / "webui" / "settings_api.py"

# I campi interi che le rotte scrivono e che lo schema limita.
_BOUNDED = [
    (AgentDefaults, "tool_hint_max_length"),
    (DreamConfig, "interval_h"),
    (GardenerConfig, "interval_min"),
    (FloatingConfig, "reply_hold_s"),
]


@pytest.mark.parametrize(("model", "attr"), _BOUNDED)
def test_the_refusal_names_the_schema_range(model: type, attr: str) -> None:
    low, high = _bounds(model, attr)
    assert low is not None or high is not None, (
        f"{model.__name__}.{attr} non ha più bound nello schema: questo test "
        "non ha più soggetto, e la voce va rimossa da _BOUNDED."
    )

    if low is not None:
        with pytest.raises(WebUISettingsError) as refused:
            _parse_int(str(low - 1), attr, model, attr)
        # Il numero nel messaggio viene dallo schema: se qualcuno ne scrive uno
        # a mano da qualche parte, qui si vede.
        assert str(low) in refused.value.message

    if high is not None:
        with pytest.raises(WebUISettingsError) as refused:
            _parse_int(str(high + 1), attr, model, attr)
        assert str(high) in refused.value.message

    # E il valore in mezzo passa.
    middle = (low if low is not None else 0) + 1
    assert _parse_int(str(middle), attr, model, attr) == middle


def test_no_handwritten_bounds_survive_in_settings_api() -> None:
    """Nessun confronto numerico su un campo che lo schema già limita.

    Cerca la forma che c'era: un letterale confrontato con un valore appena
    parsato. Non è una regex sulla prosa — è un passaggio AST, così un
    ``if parsed < 20`` scritto in qualunque modo viene visto.
    """
    tree = ast.parse(SETTINGS_API.read_text("utf-8"))
    # Solo i bound *distintivi*: un ``ge=1`` renderebbe sospetto ogni ``> 1``
    # del file (indici, lunghezze, contatori), e un test che grida su quelli
    # verrebbe spento entro una settimana. La soglia non è un compromesso sulla
    # correttezza: i numeri che vale la pena non ricopiare sono quelli che
    # nessuno indovinerebbe, e sono tutti sopra questa.
    too_common = 4
    schema_numbers = {
        n
        for model, attr in _BOUNDED
        for n in _bounds(model, attr)
        if n is not None and n > too_common
    }
    assert schema_numbers, "nessun bound distintivo da controllare"

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if (
                isinstance(operand, ast.Constant)
                and isinstance(operand.value, int)
                and operand.value in schema_numbers
            ):
                offenders.append(f"riga {node.lineno}: confronto con {operand.value}")

    assert not offenders, (
        f"limiti dello schema riscritti a mano in settings_api.py: {offenders}. "
        "Derivarli con _bounds/_parse_int: il messaggio d'errore nomina il "
        "range, quindi una copia diventa una bugia al primo cambio di schema."
    )


def test_the_appliers_are_shared_not_recopied() -> None:
    """I nove rami scritti a mano sono passati per i generalizzatori.

    Il segnale che erano tornati: un ramo che legge la query, striscia, confronta
    e assegna dentro ``_apply_agent_settings`` invece di chiamare un
    ``_apply_*``. Si conta quante volte la funzione assegna direttamente su
    ``defaults``, che è ciò che gli applicatori fanno per lei.
    """
    src = SETTINGS_API.read_text("utf-8")
    body = re.search(
        r"def _apply_agent_settings\(.*?\n(?=\nasync def |\ndef )", src, re.S
    )
    assert body, "_apply_agent_settings non trovata"

    direct = len(re.findall(r"^\s+defaults\.\w+ = ", body.group(0), re.M))
    assert direct <= 4, (
        f"{direct} assegnazioni dirette su `defaults` in _apply_agent_settings. "
        "I campi con un parser dedicato (max_tokens, temperature, "
        "reasoning_effort, context_window_tokens) le hanno per buone ragioni; "
        "per gli altri esistono _apply_str/_apply_int."
    )
