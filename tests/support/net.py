"""Il loopback come se fosse un server di fuori, solo nei test.

Dal 25/11/2026 nessuna ``security.ssrfWhitelist`` apre il telefono stesso
(``jenny.security.network._THE_PHONE_ITSELF``). Ma un test che vuole provare
che una destinazione *permessa* passa davvero ha a portata di mano un solo
server: uno su ``127.0.0.1``. Questo lo fa contare come una fascia aperta dalla
whitelist, togliendo il pavimento per la durata del blocco.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from jenny.security.network import configure_ssrf_whitelist


@contextmanager
def loopback_as_a_server() -> Iterator[None]:
    configure_ssrf_whitelist(["127.0.0.0/8"])
    try:
        with patch("jenny.security.network.is_the_phone", lambda addr: False):
            yield
    finally:
        configure_ssrf_whitelist([])
