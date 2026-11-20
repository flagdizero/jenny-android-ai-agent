"""La fixture ``two``: due Jenny abbinabili, sul banco di ``harness.py``."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.peers.harness import Jenny


@pytest.fixture()
async def two(tmp_path: Path):
    a, b = Jenny(tmp_path, "a"), Jenny(tmp_path, "b")
    await a.start()
    await b.start()
    yield a, b
    for j in (a, b):
        if j.link is not None:
            await asyncio.wait_for(j.link.stop(), timeout=15)
    await a.close()
    await b.close()
