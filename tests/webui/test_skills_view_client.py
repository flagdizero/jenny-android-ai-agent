"""La regola che divide le skill in Mani, eseguita davvero sotto node.

``shared/skills-view.js`` è puro per questo: la riga in cassetto e il pannello
leggono la stessa funzione, e qui la si prova senza telefono. Stesso idioma di
``test_launcher_rank_client.py``.

La domanda dietro ogni caso è una: **un interruttore su questa skill
sopravvive al riavvio?** Le integrate vengono ri-estratte dall'APK a ogni
avvio, quindi no — e un interruttore che mente è peggio di un lucchetto.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

VIEW_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "skills-view.js"
)

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _run_js(script: str) -> None:
    source = (
        VIEW_JS.read_text(encoding="utf-8")
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_each_kind_of_skill_lands_where_the_plan_says() -> None:
    """Una voce per riga della tabella «Le regole di divisione»."""
    _run_js(
        """
        const skills = [
          { name: 'memory', bundled: true, internal: true, locked: false },
          { name: 'cron', bundled: true, internal: false, locked: true },
          { name: 'mia-bloccata', bundled: false, internal: false, locked: true },
          { name: 'mia', bundled: false, internal: false, locked: false },
          { name: 'mia-di-servizio', bundled: false, internal: true, locked: false },
        ];
        const { tue, integrate, servizio } = dividiSkill(skills);
        assert.deepEqual(tue.map(s => s.name), ['mia-bloccata', 'mia']);
        assert.deepEqual(integrate.map(s => s.name), ['cron']);
        assert.equal(servizio, 2);
        """
    )


def test_only_your_unlocked_skills_get_a_switch() -> None:
    _run_js(
        """
        assert.equal(controllabile({ bundled: false, internal: false, locked: false }), true);
        assert.equal(controllabile({ bundled: true, internal: false, locked: false }), false,
          'una integrata senza lucchetto resta comunque ri-estratta al riavvio');
        assert.equal(controllabile({ bundled: false, internal: false, locked: true }), false);
        assert.equal(controllabile({ bundled: false, internal: true, locked: false }), false);
        """
    )


def test_a_payload_without_skills_divides_into_nothing() -> None:
    _run_js(
        """
        assert.deepEqual(dividiSkill([]), { tue: [], integrate: [], servizio: 0 });
        assert.deepEqual(dividiSkill(undefined), { tue: [], integrate: [], servizio: 0 });
        """
    )


def test_the_summary_speaks_the_interface_language_then_falls_back() -> None:
    _run_js(
        """
        const both = { name: 'cron', description: 'Schedule reminders.',
                       user_summary: { it: 'Promemoria', en: 'Reminders' } };
        assert.equal(riassuntoSkill(both, 'en'), 'Reminders');
        assert.equal(riassuntoSkill(both, 'it'), 'Promemoria');
        assert.equal(riassuntoSkill({ ...both, user_summary: { en: 'Reminders' } }, 'it'),
          'Reminders');
        assert.equal(riassuntoSkill({ ...both, user_summary: null }, 'it'), 'Schedule reminders.');
        """
    )


def test_the_summary_never_repeats_the_name() -> None:
    """Senza descrizione il server ripiega sul nome: sotto il nome non va."""
    _run_js(
        """
        assert.equal(riassuntoSkill({ name: 'mia', description: 'mia' }, 'it'), '');
        assert.equal(riassuntoSkill({ name: 'mia', description: '  ' }, 'it'), '');
        assert.equal(riassuntoSkill({ name: 'mia' }, 'it'), '');
        """
    )


def test_the_drawer_row_has_two_forms() -> None:
    _run_js(
        """
        const t = (k, v) => `${k}:${JSON.stringify(v)}`;
        assert.equal(riepilogoSkill({ tue: [{}, {}], integrate: [{}] }, t),
          'skills.riepilogo:{"integrate":1,"tue":2}');
        assert.equal(riepilogoSkill({ tue: [], integrate: [{}, {}] }, t),
          'skills.riepilogoNessunaTua:{"integrate":2}');
        """
    )
