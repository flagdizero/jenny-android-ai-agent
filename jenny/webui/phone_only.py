"""Le azioni che hanno senso solo col telefono in mano.

Da un altro dispositivo del tailnet (``channels.ws_remote``) la WebUI di Jenny
e' la stessa, ma alcune route agiscono **sul telefono**, in modo visibile: aprono
un'app Android, la finestra di disinstallazione, Info app o Accessibilita',
accendono la mascotte flottante, lanciano l'installazione di un aggiornamento.
Dal Mac, col telefono nel cassetto, nessuna di queste e' quel che si voleva: un
installer resterebbe aperto su uno schermo che nessuno guarda, un'app aperta
manderebbe Jenny in background.

Qui la regola sta in un posto solo: :data:`PHONE_ONLY_ROUTES` elenca le route,
:func:`refuse_if_remote` e' il controllo che ognuna fa per prima cosa, **prima**
di toccare un ponte nativo. Un test verifica che ogni route che arriva a quei
ponti sia nell'elenco: la prossima non passa da fuori per dimenticanza.
La pagina lo sa gia' dal bootstrap (``remote: true``) e non mostra quei comandi;
il rifiuto qui e' la garanzia, per chi non passa dalla pagina.
"""

from __future__ import annotations

from websockets.http11 import Response

from jenny.channels.http_utils import http_json_response
from jenny.channels.ws_remote import on_remote_listener

PHONE_ONLY_ERROR = "phone_only"

# Le route che agiscono sul telefono. I segmenti fra ``<>`` sono variabili.
PHONE_ONLY_ROUTES = (
    "/api/webui/android-apps/<package>/launch",
    "/api/webui/android-apps/<package>/uninstall",
    "/api/webui/android-apps/<package>/app-info",
    "/api/settings/floating/update",
    "/api/settings/floating/screen-settings",
    "/api/updates/install",
    "/api/webui/apps/<slug>/view",
)


def refuse_if_remote() -> Response | None:
    """Un 403 ``phone_only`` se la richiesta arriva da fuori, altrimenti ``None``."""
    if not on_remote_listener():
        return None
    return http_json_response({"error": PHONE_ONLY_ERROR}, status=403)
