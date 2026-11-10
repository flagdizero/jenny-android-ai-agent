"""La ``config.json`` di una prova: salvata coi default e indicata al runtime.

Era la stessa fixture ricopiata in ``tests/board/conftest.py`` e in nove file di
``tests/webui``; adesso la registrano i due conftest importandola da qui. Chi
vuole altro — il percorso **senza** un file già scritto (``unsaved_config_path``)
o anche un workspace privato (``private_config_path``) — ha la sua fixture con
un altro nome, così nessun modulo ne nasconde un'altra.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path
