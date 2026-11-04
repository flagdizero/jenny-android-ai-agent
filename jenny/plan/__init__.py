"""Piano a lista: un lavoro grosso diviso in passi che il codice guida uno alla volta.

Il package fornisce:

- ``model``: stati, transizioni ammesse, validazione di una proposta e i tag
  della versione nuova — puro, senza I/O;
- ``store``: il piano salvato, l'unica fonte del suo stato, e le
  pianificazioni in corso (una per posto): righe di ``undo.sqlite``, accanto
  al diario dell'annulla, fuori dal workspace;
- ``place``: il posto di un piano, un quaderno (``nb:<nome>``) o la chat
  personale (``personal``);
- ``work``: il lavoro di un piano letto dai suoi registri d'annulla (i file per
  passo, i primi testi), da cui si torna indietro.
"""
