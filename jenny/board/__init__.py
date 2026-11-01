"""La bacheca di un quaderno: la faccia del quaderno, con la wiki dietro.

Un quaderno si apre sulla bacheca e non sulle pagine. La wiki resta la fonte di
verità del sapere; la bacheca è un oggetto a sé che punta alla wiki: poche
schede curate (un soggetto ciascuna, col suo cassetto), i post-it dei
promemoria, i fili fra le schede e la busta con le proposte di Jenny.

Due file, tutti e due dentro la cartella del quaderno (``<quaderno>/board/``):

- ``board.json`` — la forma: schede, posizioni, sezioni dell'utente, fili,
  busta. Lo scrivono in due, l'interfaccia e il giardiniere, e per questo passa
  **sempre** da :func:`jenny.board.store.mutate_board`.
- ``cards/<id>.json`` — il riassunto di ogni cassetto. **Derivato**: si può
  cancellare e rigenerare dalla wiki in ogni momento.

I promemoria non stanno qui: dal 31/10/2026 sono job dello scheduler
(``cron/jobs.json``), legati al quaderno da ``payload.board`` (v.
:mod:`jenny.board.cron_link`).

**La forma è dell'utente, il contenuto è della wiki.** Jenny non sposta, non
toglie e non aggiunge schede da sola: propone. E il rispetto di quel che ha
deciso l'utente non lo affida a un prompt: lo controlla il codice a ogni
scrittura (``model.ownership_violations``).

Nessun re-export qui, per la stessa ragione di ``jenny/apps/__init__.py``.
"""
