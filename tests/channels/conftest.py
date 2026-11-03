"""Il bus finto dei test sul canale WebSocket.

Sei file ne avevano una copia identica: un ``MagicMock`` con ``publish_inbound``
attendibile, cioè ``support.ws_channel.make_bus``. Chi vuole un ``MessageBus``
vero ridefinisce la fixture nel suo file.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from support.ws_channel import make_bus


@pytest.fixture()
def bus() -> MagicMock:
    return make_bus()
