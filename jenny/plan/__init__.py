"""Piano a lista: un lavoro grosso diviso in passi che il codice guida uno alla volta.

Il package fornisce:

- ``model``: stati, transizioni ammesse, validazione di una proposta e i tag
  della versione nuova — puro, senza I/O;
- ``store``: ``plan.json``, l'unica fonte dello stato di un piano, fuori dal
  workspace (sotto ``<runtime_root>/plans/``);
- ``photos``: le foto del quaderno (uno ``SnapshotEngine`` per piano), il loro
  confronto e il ritorno indietro a tre vie con journal.
"""
