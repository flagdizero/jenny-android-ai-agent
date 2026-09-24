"""Il troncatore e lo stimatore del consolidator usano lo stesso rapporto.

Il consolidator sottrae dal budget quello che il troncatore applica: se i due
contassero i token in modo diverso, la differenza uscirebbe come una richiesta
fuori finestra invece che come un taglio (v. ``consolidator._estimate_tokens``).
"""

from __future__ import annotations

import pytest

from jenny.agent.consolidator import _estimate_tokens
from jenny.utils.helpers import CHARS_PER_TOKEN, truncate_text_to_tokens


@pytest.mark.parametrize("budget", [1, 7, 100, 1234])
def test_what_the_truncator_keeps_is_what_the_estimator_counts(budget: int) -> None:
    kept = truncate_text_to_tokens("x" * 50_000, budget).removesuffix("\n... (truncated)")
    assert len(kept) == budget * CHARS_PER_TOKEN
    assert _estimate_tokens(kept) == budget
