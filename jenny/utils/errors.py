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
