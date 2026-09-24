"""Sessioni finte per i test del dispatch di cron e heartbeat.

Sette file ne avevano una coppia sua (sessione + store), con le stesse tre
cose in combinazioni diverse: la chiave, le potature chieste
(``retain_recent_legal_suffix``) e i messaggi, che il ramo heartbeat legge
per sapere se l'utente si è fatto vivo dopo un avviso. Qui tutte e tre, e due
modi di rispondere a ``get_or_create``:

- di norma **una sessione per chiave, la stessa a ogni richiesta**: chi scrive
  in ``unified:default`` la ritrova (restituirne una nuova ogni volta rendeva
  invisibile tutto ciò che sta nella conversazione dell'utente);
- con ``shared=True`` **la stessa sessione per ogni chiave**, in
  :attr:`FakeSessions.session`, per i test che guardano l'unica sessione che
  il dispatch tocca.

``saved`` elenca le chiavi salvate, in ordine; ``save_count`` le conta.
``test_heartbeat_user_rearm.py`` resta fuori: usa ``Session`` vere, perché il
lettore del timbro utente è ciò che prova.
"""

from __future__ import annotations


class FakeSession:
    def __init__(self, key: str = "") -> None:
        self.key = key
        self.retained: list[int] = []
        self.messages: list[dict] = []

    def retain_recent_legal_suffix(self, keep: int) -> None:
        self.retained.append(keep)


class FakeSessions:
    def __init__(self, *, shared: bool = False) -> None:
        self.by_key: dict[str, FakeSession] = {}
        self.saved: list[str] = []
        self.session: FakeSession | None = FakeSession() if shared else None

    def get_or_create(self, key: str) -> FakeSession:
        if self.session is not None:
            return self.session
        return self.by_key.setdefault(key, FakeSession(key))

    def save(self, session: FakeSession) -> None:
        self.saved.append(session.key)

    @property
    def save_count(self) -> int:
        return len(self.saved)
