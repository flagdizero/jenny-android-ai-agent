"""La regola che divide le skill in Mani, eseguita davvero sotto node.

``shared/skills-view.js`` è puro per questo: la riga in cassetto e il pannello
leggono la stessa funzione, e qui la si prova senza telefono. Stesso idioma di
``test_launcher_rank_client.py``.

La domanda dietro ogni caso è una: **un interruttore su questa skill
sopravvive al riavvio?** Le integrate vengono ri-estratte dall'APK a ogni
avvio, quindi no — e un interruttore che mente è peggio di un lucchetto.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import locale, requires_node, run_js

VIEW_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "skills-view.js"
)


pytestmark = requires_node


def _run_js(script: str) -> None:
    source = (
        VIEW_JS.read_text(encoding="utf-8")
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    run_js(source)


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
        const { yours, integrate, service } = dividiSkill(skills);
        assert.deepEqual(yours.map(s => s.name), ['mia-bloccata', 'mia']);
        assert.deepEqual(integrate.map(s => s.name), ['cron']);
        assert.equal(service, 2);
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
        assert.deepEqual(dividiSkill([]), { yours: [], integrate: [], service: 0 });
        assert.deepEqual(dividiSkill(undefined), { yours: [], integrate: [], service: 0 });
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
        assert.equal(riepilogoSkill({ yours: [{}, {}], integrate: [{}] }, t),
          'skills.summary:{"integrate":1,"yours":2}');
        assert.equal(riepilogoSkill({ yours: [], integrate: [{}, {}] }, t),
          'skills.summaryNoneYours:{"integrate":2}');
        """
    )


def test_the_lock_says_why_in_two_different_ways() -> None:
    """Una skill tua con `locked` non «viene con l'app»: il lucchetto deve dire
    il motivo vero, e la chiave deve esistere nelle due lingue."""
    _run_js(
        """
        assert.equal(motivoBlocco({ bundled: true, locked: false }), 'skills.integrataBloccata');
        assert.equal(motivoBlocco({ bundled: true, locked: true }), 'skills.integrataBloccata');
        assert.equal(motivoBlocco({ bundled: false, locked: true }), 'skills.tuaBloccata');
        """
    )
    for lingua in ("it", "en"):
        voci = locale(lingua)["skills"]
        assert voci["tuaBloccata"] and voci["tuaBloccata"] != voci["integrataBloccata"]
    settings = (VIEW_JS.parents[1] / "mobile-settings.js").read_text(encoding="utf-8")
    assert "i18n.t('skills.integrataBloccata')" not in settings, (
        "il lucchetto della riga dice di nuovo «Viene con l'app» a tutte"
    )
