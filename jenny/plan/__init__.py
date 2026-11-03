"""Piano a lista: un lavoro grosso diviso in passi che il codice guida uno alla volta.

Il package fornisce:

- ``model``: stati, transizioni ammesse, validazione di una proposta e i tag
  della versione nuova — puro, senza I/O;
- ``store``: ``plan.json``, l'unica fonte dello stato di un piano, fuori dal
  workspace (sotto ``<runtime_root>/plans/``), e accanto ``drafting.json``, le
  pianificazioni in corso (una per quaderno);
- ``place``: il posto di un piano, un quaderno (``nb:<nome>``) o la chat
  personale (``personal``);
- ``work``: il lavoro di un piano letto dai suoi registri d'annulla (i file per
  passo, i primi testi), da cui si torna indietro.
"""
