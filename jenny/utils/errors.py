"""Un rifiuto atteso che porta il codice con cui va detto all'interfaccia."""

from __future__ import annotations


class CodedError(Exception):
    """Un rifiuto atteso: *code* è un codice di ``webui.commands.CommandError``.

    La base dei rifiuti dei moduli che non stanno nella WebUI (la bacheca, il
    rinomino di un quaderno), così ``dispatch_command`` li traduce in un posto
    solo invece che in ogni comando che li chiama. *message* è la ragione, in
    inglese: il client dice nella sua lingua i codici che si aspetta.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Lo status HTTP di una rotta → il codice di ``CommandError`` con cui lo dice un
# comando. Un 502 (Telegram o il provider irraggiungibili) e un 503 dicono
# entrambi «adesso non si puo'».
_STATUS_CODES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    502: "unavailable",
    503: "unavailable",
}


class StatusError(CodedError):
    """Un rifiuto atteso con lo status HTTP con cui lo dice una rotta.

    Il codice per i comandi ne discende (``bad_request`` per uno status che non
    ha un codice suo), così ``dispatch_command`` lo traduce come ogni altro
    :class:`CodedError` e nessun comando lo riscrive a mano.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(_STATUS_CODES.get(status, "bad_request"), message)
        self.status = status
