"""Importare una foglia non deve tirarsi dietro il package intero.

``jenny/apps/__init__.py`` ri-esportava ``execute_action`` per comodità, e
quella riga veniva eseguita da **ogni** import nel package: ``import
jenny.apps.storage`` — un modulo che di suo tocca config e filesystem — caricava
113 moduli invece di 35, fra cui tutto ``jenny.agent`` e ``jenny.providers``.
Su Chaquopy quel conto si paga all'avvio del gateway, ogni volta.

La comodità non la usava nessuno: gli import via facciata erano zero contro 415
diretti al sottomodulo, sui tre package.

Qui si fissa il **fatto**, non un numero: una foglia leggera non nomina il grafo
pesante. Un conteggio esatto andrebbe aggiornato a ogni import legittimo aggiunto
a valle, e verrebbe alzato senza guardare al primo rosso.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Foglie che non hanno ragione di conoscere l'agente, e il grafo che non devono
# tirarsi dietro.
LEAVES = ["jenny.apps.storage", "jenny.bus.events", "jenny.cron.types"]
FORBIDDEN_PREFIXES = ("jenny.agent.", "jenny.providers.")


def _modules_loaded_by(leaf: str) -> set[str]:
    """Import in un interprete pulito: dentro la suite ``sys.modules`` è già
    pieno di tutto, e la misura direbbe sempre di sì."""
    code = (
        f"import {leaf}, sys, json;"
        "print(json.dumps([m for m in sys.modules if m.startswith('jenny')]))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, f"import di {leaf} fallito:\n{done.stderr}"
    import json

    return set(json.loads(done.stdout))


@pytest.mark.parametrize("leaf", LEAVES)
def test_a_leaf_import_does_not_drag_in_the_agent(leaf: str) -> None:
    loaded = _modules_loaded_by(leaf)
    dragged = sorted(m for m in loaded if m.startswith(FORBIDDEN_PREFIXES))
    assert not dragged, (
        f"`import {leaf}` carica {len(dragged)} modules di agent/providers "
        f"(es. {dragged[:3]}). Di solito è un re-export rimesso in un "
        "``__init__.py`` per comodità: quella riga la esegue ogni import del "
        "package, foglie comprese. Importa dal sottomodulo."
    )


@pytest.mark.parametrize("package", ["jenny.apps", "jenny.bus", "jenny.cron"])
def test_the_package_init_has_no_imports(package: str) -> None:
    """Anche l'``__init__`` vuoto va difeso al livello del sorgente.

    Il test sopra guarda il grafo, quindi un re-export *leggero* gli
    sfuggirebbe — e sarebbe comunque il primo passo verso quello pesante.
    """
    import ast

    path = REPO / Path(*package.split(".")) / "__init__.py"
    tree = ast.parse(path.read_text("utf-8"))
    statements = [n for n in tree.body if not isinstance(n, ast.Expr)]
    assert not statements, (
        f"{package}/__init__.py non è più solo la docstring: "
        f"{[ast.unparse(n) for n in statements]}. La nota nel file dice perché."
    )
