"""Il token di una Jenny App: apre le route di quell'app e nient'altro.

Fino a Sett 2026 la cornice di un'app riceveva in ``?token=`` il segreto del
gateway (``websocket.token_issue_secret``) — lo stesso che apre ogni route
``/api/`` e la WebSocket. Un'app, o un'iniezione dentro un'app, aveva quindi
l'intera API: impostazioni dei provider, RPC di scrittura, prompt all'agente
(che ha ``python_exec``). Il sandbox dell'iframe teneva l'app fuori dal DOM
della SPA, non fuori dal gateway.

Ora la cornice riceve ``app_token(secret, slug)``: un HMAC del segreto sullo
slug, che ``check_app_secret`` accetta **solo** sulle route di quello slug
(``/apps/<slug>/**`` e ``/api/apps/<slug>/actions/*``). Ovunque altro il
confronto resta col segreto intero, quindi il token di un'app lì non vale
niente — nemmeno sulla WebSocket, che confronta col segreto e basta.

Deterministico di proposito: nessuno stato da tenere, nessuna scadenza da
rinnovare mentre l'app è aperta, e cambia da sé se il segreto cambia.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# Separatore di dominio: un HMAC dello stesso segreto usato per altro in futuro
# non deve poter coincidere con questo.
_DOMAIN = b"jenny-app-token/v1\x00"


def app_token(secret: str, slug: str) -> str:
    """Il token dell'app *slug*, derivato dal segreto del gateway."""
    mac = hmac.new(secret.encode("utf-8"), _DOMAIN + slug.encode("utf-8"), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode("ascii")
