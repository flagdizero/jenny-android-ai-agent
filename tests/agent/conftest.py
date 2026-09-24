"""Shared fixtures for agent tests (the helpers live in ``support.agent``)."""

from __future__ import annotations

import pytest
from support.agent import make_loop


@pytest.fixture
def loop_factory(tmp_path):
    """Fixture providing a factory for creating AgentLoop instances."""
    def _factory(**kwargs):
        return make_loop(tmp_path, **kwargs)
    return _factory
