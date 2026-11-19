"""Le altre Jenny dell'utente: un canale diretto sulla tailnet.

Due Jenny della stessa persona (il telefono di lavoro e quello personale) si
abbinano una volta con un codice, poi si scambiano messaggi da sole sul
secondo ascoltatore del gateway (``channels.ws_remote``), al percorso
:data:`jenny.peers.model.PEER_PATH`. È il primo canale in cui chi scrive non è
l'utente: quello che arriva è un dato, e cosa l'altra può fare lo decide chi
riceve.

- :mod:`.model` — i frame e le costanti del protocollo;
- :mod:`.store` — l'identità di questa Jenny, i segreti e le tabelle della coda;
- :mod:`.auth` — il codice d'abbinamento e la prova HMAC di ogni connessione;
- :mod:`.link` — :class:`~jenny.peers.link.PeerLink`, chi apre, consegna e riceve.
"""
