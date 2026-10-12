"""La bacheca di un quaderno: la faccia del quaderno, con la wiki dietro.

Un quaderno si apre sulla bacheca e non sulle pagine. La wiki resta la fonte di
verità del sapere; la bacheca è un oggetto a sé che punta alla wiki: poche
schede curate (un soggetto ciascuna, col suo cassetto), l'agenda delle attese,
i fili fra le schede e la busta con le proposte di Jenny.

Tre file, tutti dentro la cartella del quaderno (``<quaderno>/board/``):

- ``board.json`` — la forma: schede, posizioni, sezioni dell'utente, fili,
  busta. Lo scrivono in due, l'interfaccia e il giardiniere, e per questo passa
  **sempre** da :func:`jenny.board.store.mutate_board`.
- ``reminders.json`` — le attese, con data o condizione.
- ``cards/<id>.json`` — il riassunto di ogni cassetto. **Derivato**: si può
  cancellare e rigenerare dalla wiki in ogni momento.

**La forma è dell'utente, il contenuto è della wiki.** Jenny non sposta, non
toglie e non aggiunge schede da sola: propone. E il rispetto di quel che ha
deciso l'utente non lo affida a un prompt: lo controlla il codice a ogni
scrittura (``model.ownership_violations``).

Nessun re-export qui, per la stessa ragione di ``jenny/apps/__init__.py``.
"""
