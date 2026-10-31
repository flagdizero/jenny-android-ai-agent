"""Il banco della casa esce appena la prova finisce: che non perda niente.

``run_home`` non aspetta piu' che node si spenga da solo (circa un secondo di
timer della pagina a prova): a fine script esce. Un'uscita anticipata puo'
nascondere tre cose, e qui si prova che non lo fa:

- un assert dentro un ``setTimeout`` della prova scritto senza ``await``, che
  non partirebbe mai: la prova deve fallire, non passare;
- un rifiuto di promessa non gestito, che prima finiva in un elenco che quasi
  nessuna prova guardava;
- la coda dello stdout, che su macOS una pipe asincrona perde se ``exit``
  arriva prima che sia scritta.
"""

from __future__ import annotations

import pytest
from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import { boot, tick } from './boot.mjs';
await boot();
"""


def test_a_timer_the_case_never_awaited_fails_the_case() -> None:
    with pytest.raises(AssertionError, match="timer della prova mai partiti"):
        run_home(_HEAD + "setTimeout(() => { throw new Error('mai eseguito'); }, 50);\n")


def test_an_awaited_timer_is_not_reported() -> None:
    run_home(_HEAD + "await new Promise((r) => setTimeout(r, 10));\nawait tick(5);\n")


def test_an_unhandled_rejection_fails_the_case() -> None:
    with pytest.raises(AssertionError, match="rifiuti non gestiti"):
        run_home(_HEAD + "Promise.reject(new Error('nessuno la prende'));\n")


def test_the_whole_stdout_reaches_the_test() -> None:
    out = run_home(_HEAD + "for (let i = 0; i < 20000; i++) console.log('riga ' + i);\n")
    lines = out.splitlines()
    assert len(lines) == 20000
    assert lines[-1] == "riga 19999"
