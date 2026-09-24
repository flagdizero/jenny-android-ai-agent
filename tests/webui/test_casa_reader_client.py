"""Dove porta un collegamento dentro una pagina del quaderno.

Il testo lo rende il server; qui c'e' l'unica logica che la casa ci mette
attorno, e il suo valore sta nella terza uscita: **`null` vuol dire «non da
qui»**, e chi chiama lo deve dire invece di ingoiarlo. Un link inerte che non
spiega perche' e' il difetto che l'officina ha gia' avuto e riparato.

Due cose si rifiutano e non si normalizzano — la risalita (`..`) e il percorso
assoluto. Il server la sua guardia ce l'ha (``safe_wiki_page_path``), e due
guardie che normalizzano in modo diverso sono il modo classico di aprirsi un
buco in mezzo: qui si dice solo di no.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import function, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
READER_JS = ASSETS / "casa-reader.js"


pytestmark = requires_node


def _run(script: str) -> None:
    src = READER_JS.read_text(encoding="utf-8")
    harness = (
        "import assert from 'node:assert/strict';\n"
        + function(src, "resolveRelativePage")
        + "\n"
        + function(src, "linkTarget")
        + "\n"
    )
    run_js(harness + script)


def test_a_relative_link_resolves_against_the_page_that_holds_it() -> None:
    _run("""
      assert.equal(resolveRelativePage('concepts/orto.md', 'note.md'), 'concepts/note.md');
      assert.equal(resolveRelativePage('concepts/orto.md', './note.md'), 'concepts/note.md');
      assert.equal(resolveRelativePage('orto.md', 'sub/note.md'), 'sub/note.md');
      assert.equal(resolveRelativePage('concepts/orto.md', 'note.md#taglio'),
                   'concepts/note.md');
    """)


def test_what_is_not_a_page_of_this_notebook_is_refused() -> None:
    _run("""
      for (const href of ['../fuori.md', '/assoluto.md', 'http://x/y.md',
                          'mailto:a@b.md', 'immagine.png', '']) {
        assert.equal(resolveRelativePage('concepts/orto.md', href), null, href);
      }
    """)


def test_a_wikilink_of_this_notebook_opens_its_page() -> None:
    _run("""
      const t = linkTarget({
        href: '?wiki=orto&page=entities/rosmarino.md',
        wikilink: true, notebook: 'orto', currentPath: 'index.md',
      });
      assert.deepEqual(t, { kind: 'page', path: 'entities/rosmarino.md' });
    """)


def test_a_wikilink_to_another_notebook_is_not_opened_from_here() -> None:
    """In casa una pagina appartiene alla conversazione in cui sei: saltare in
    un'altra stanza senza dirlo e' una scorciatoia che poi non si sa disfare."""
    _run("""
      const t = linkTarget({
        href: '?wiki=erbe&page=x.md',
        wikilink: true, notebook: 'orto', currentPath: 'index.md',
      });
      assert.equal(t, null);
    """)


def test_the_web_goes_out_of_the_webview() -> None:
    _run("""
      assert.deepEqual(
        linkTarget({ href: 'https://example.org/a', notebook: 'orto', currentPath: 'i.md' }),
        { kind: 'external', href: 'https://example.org/a' },
      );
      assert.equal(
        linkTarget({ href: 'mailto:a@b.c', notebook: 'orto', currentPath: 'i.md' }).kind,
        'external',
      );
    """)


def test_an_anchor_stays_on_the_page() -> None:
    _run("""
      assert.deepEqual(
        linkTarget({ href: '#sintomi', notebook: 'orto', currentPath: 'i.md' }),
        { kind: 'hash', id: 'sintomi' },
      );
    """)


def test_a_dead_link_is_null_and_not_a_guess() -> None:
    """Un wikilink che il renderer non ha risolto resta un href qualunque: si
    dice che non si apre, non si prova a indovinare una pagina."""
    _run("""
      assert.equal(
        linkTarget({ href: 'Pagina Che Non Esiste', wikilink: true,
                     notebook: 'orto', currentPath: 'i.md' }),
        null,
      );
      assert.equal(linkTarget({ href: '', notebook: 'orto', currentPath: 'i.md' }), null);
    """)
