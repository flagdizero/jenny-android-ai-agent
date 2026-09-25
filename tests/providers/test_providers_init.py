"""Tests for lazy provider exports from jenny.providers.

Ogni prova gira in un interprete nuovo. Prima toglieva ``jenny.providers`` da
``sys.modules`` con ``monkeypatch.delitem`` e lo reimportava: al ripristino il
pacchetto tornava, ma i sottomoduli caricati *durante* la prova restavano in
``sys.modules`` legati al pacchetto usa-e-getta, e quello ricreato dopo non
aveva più l'attributo ``base``. Chi girava dopo e faceva
``monkeypatch.setattr("jenny.providers.base....")`` falliva — 21 prove di
``test_provider_retry.py`` con la suite in ordine inverso. Un sottoprocesso
misura la pigrizia dell'import su un interprete davvero pulito, e non lascia
niente dietro di sé.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _run(script: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=_ROOT,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_importing_providers_package_is_lazy() -> None:
    _run(
        """
        import importlib
        import sys

        providers = importlib.import_module("jenny.providers")

        assert "jenny.providers.anthropic_provider" not in sys.modules
        assert "jenny.providers.openai_compat_provider" not in sys.modules
        assert providers.__all__ == [
            "LLMProvider",
            "LLMResponse",
            "AnthropicProvider",
            "OpenAICompatProvider",
        ], providers.__all__
        """
    )


def test_explicit_provider_import_still_works() -> None:
    _run(
        """
        import sys

        assert "jenny.providers.anthropic_provider" not in sys.modules
        from jenny.providers import AnthropicProvider

        assert AnthropicProvider.__name__ == "AnthropicProvider"
        assert "jenny.providers.anthropic_provider" in sys.modules
        """
    )
