"""In CI le prove che eseguono il JS reale devono girare, non saltare.

Una ventina di suite della WebUI (`*_client.py`, più il contratto del grafo)
lanciano `node` sugli asset veri e sono sotto `skipif`. Un salto è la risposta
giusta sulla macchina di chi sviluppa e non ha node; in CI invece è una
sparizione — e una sparizione **in verde**, che è il modo peggiore di perdere
copertura: nessuno la nota finché non serve.

Prima di questo file la CI non installava node nel job di test: quelle prove
passavano solo perché l'immagine `ubuntu-latest` lo include per conto suo. Il
guard qui non ripara quel caso, lo rende **rumoroso**: se un domani l'immagine
cambia, o il passo `setup-node` viene tolto, questo test diventa rosso invece di
lasciare le altre duecento sparire.
"""

from __future__ import annotations

import os
import shutil

import pytest

# Le variabili che i runner impostano da sé. `CI` la mettono tutti; le altre due
# distinguono GitHub Actions, così il guard non si arma su una shell in cui
# qualcuno ha esportato `CI` per altri motivi.
_IN_CI = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))


@pytest.mark.skipif(not _IN_CI, reason="guard di CI: in locale node è opzionale")
def test_node_is_installed_in_ci() -> None:
    node = shutil.which("node")
    assert node, (
        "node non è nel PATH: le suite `tests/webui/*_client.py` si salterebbero "
        "in silenzio. Manca il passo `actions/setup-node` nel job `test` di "
        ".github/workflows/ci.yml."
    )
