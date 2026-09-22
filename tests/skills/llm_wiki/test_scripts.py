"""Test degli script della skill llm-wiki: scaffold, registry, lint, audit.

Veniva da ``jenny/skills/llm-wiki/scripts/tests/test_scripts.py``, e da la'
**non lo eseguiva nessuno**: ``pyproject.toml`` fissa ``testpaths = ["tests"]``,
quindi ``pytest -q`` restava verde con tre test rossi dentro. Spostarlo qui,
accanto a ``test_lint_wiki.py`` e ``test_scaffold_topup.py`` che provano gli
stessi script, e' la strada che non chiede niente a ``pyproject.toml`` (file
condiviso) e che ha un secondo effetto concreto: solo ``jenny/`` finisce
nell'APK, quindi il codice di test non viaggia piu' sul telefono.

Gli script della skill non fanno parte del package ``jenny`` importabile, quindi
la dir ``scripts/`` viene aggiunta a ``sys.path`` — e va aggiunta comunque,
perche' i tre script si importano ``reindex_wikis`` a vicenda.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3] / "jenny" / "skills" / "llm-wiki" / "scripts"
)
sys.path.insert(0, str(_SCRIPTS_DIR))

import audit_review  # noqa: E402
import lint_wiki  # noqa: E402
import reindex_wikis  # noqa: E402
import scaffold as scaffold_mod  # noqa: E402


def quiet(fn, *args, **kwargs):
    """Call fn with stdout/stderr suppressed; return its result."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*args, **kwargs)


def registry_block(wikis_dir: Path) -> str:
    text = (wikis_dir / "_index.md").read_text(encoding="utf-8")
    m = reindex_wikis.BLOCK_RE.search(text)
    return m.group(1) if m else ""


def make_wiki(wikis_dir: Path, name: str, agents_md: str) -> Path:
    """Wiki minima (``AGENTS.md`` + ``wiki/index.md``) senza passare da scaffold.

    Il nome del file di istruzioni e' quello di **oggi**. Fino al 7.5 questa
    fixture scriveva ``CLAUDE.md`` e diceva che leggerlo era «esattamente quel
    che deve continuare a funzionare»: non lo e' piu'. Il nome vecchio resta
    noto a due posti, e a nessun lettore — la migrazione dell'avvio, che lo
    rinomina, e ``wiki_paths.wiki_id``, che deve poter leggere l'identita' di una
    wiki non ancora migrata. Per lo scope il ripiego e' stato tolto: due nomi per
    lo stesso file sono due nomi da tenere allineati in ogni lettore, e i lettori
    sono quattro. Cosa legge una cartella col solo nome vecchio lo fissa
    :meth:`ScopeExtraction.test_legacy_name_is_not_read`.
    """
    root = wikis_dir / name
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(agents_md, encoding="utf-8")
    (root / "wiki" / "index.md").write_text(f"# Index — {name}\n", encoding="utf-8")
    return root


def make_page(wiki_root: Path, relpath: str, title: str, sources=None, body="Body.") -> Path:
    p = wiki_root / "wiki" / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {title}", "type: concept"]
    if sources is not None:
        fm.append("sources: [" + ", ".join(sources) + "]")
    p.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n", encoding="utf-8")
    return p


def run_lint(root: Path):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = lint_wiki.lint(str(root))
    return rc, buf.getvalue()


def write_audit(wiki_root: Path, filename: str, fields: dict, resolved=False) -> Path:
    d = wiki_root / "audit" / ("resolved" if resolved else "")
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in fields.items())
    body = f"---\n{fm}\n---\n\n# Comment\n\nTest.\n\n# Resolution\n"
    path = d / filename
    path.write_text(body, encoding="utf-8")
    return path


VALID_AUDIT = {
    "id": "20260101-090000-abcd",
    "target": "index.md",
    "target_lines": "[1, 1]",
    "anchor_before": '""',
    "anchor_text": '"# Index"',
    "anchor_after": '""',
    "author": "tester",
    "source": "manual",
    "created": "2026-01-01T09:00:00+01:00",
    "status": "open",
}


class ScaffoldAndRegistry(unittest.TestCase):
    def test_scaffold_creates_tree_and_registers(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            quiet(scaffold_mod.scaffold, str(wikis / "loops"), "Loops")

            self.assertTrue((wikis / "main" / "wiki" / "index.md").is_file())
            self.assertTrue((wikis / "main" / "AGENTS.md").is_file())
            self.assertTrue((wikis / "_index.md").is_file())

            block = registry_block(wikis)
            self.assertIn("[[main/wiki/index|main]]", block)
            self.assertIn("[[loops/wiki/index|loops]]", block)
            self.assertEqual(reindex_wikis.check_index(wikis), [])

    def test_registry_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            for n in ("zebra", "alpha", "mid"):
                quiet(scaffold_mod.scaffold, str(wikis / n), n)
            names = reindex_wikis.discover_wikis(wikis)
            self.assertEqual(names, ["alpha", "mid", "zebra"])


class ScopeExtraction(unittest.TestCase):
    def test_summary_frontmatter_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            make_wiki(wikis, "w", "---\nsummary: The explicit summary line\n---\n\n# W\n")
            self.assertEqual(
                reindex_wikis.read_wiki_scope(wikis / "w"),
                "The explicit summary line",
            )

    def test_scope_bullet_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            md = "# W\n\n## Scope\n\nWhat this wiki covers:\n- Bullet scope text\n"
            make_wiki(wikis, "w", md)
            self.assertEqual(
                reindex_wikis.read_wiki_scope(wikis / "w"),
                "Bullet scope text",
            )

    def test_placeholder_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            md = "---\nsummary: <one-line scope>\n---\n\n# W\n\n## Scope\n\nWhat this wiki covers:\n- <describe>\n"
            make_wiki(wikis, "w", md)
            self.assertEqual(
                reindex_wikis.read_wiki_scope(wikis / "w"),
                "(no scope set)",
            )

    def test_legacy_name_is_not_read(self):
        """Una cartella col solo ``CLAUDE.md`` non ha un file di istruzioni.

        E' il prezzo dichiarato del 7.5, e questo script deve pagarlo come lo
        paga il package: ``utils/wiki_paths.read_wiki_scope`` risponde
        «(no AGENTS.md)» sulla stessa cartella (v.
        ``tests/utils/test_wiki_paths.py``), e due copie della stessa logica che
        rispondono diverso sono la cosa peggiore delle due. La finestra si chiude
        da se': la migrazione rinomina al prossimo avvio.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wikis" / "w"
            (root / "wiki").mkdir(parents=True)
            (root / "CLAUDE.md").write_text(
                "---\nsummary: Lo scope vecchio\n---\n", encoding="utf-8"
            )
            self.assertEqual(reindex_wikis.read_wiki_scope(root), "(no AGENTS.md)")


class ReindexDriftAndHeal(unittest.TestCase):
    def test_drift_detected_and_healed(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            self.assertEqual(reindex_wikis.check_index(wikis), [])

            # New wiki on disk, not yet registered.
            make_wiki(wikis, "extra", "# Extra\n")
            problems = reindex_wikis.check_index(wikis)
            self.assertTrue(any("extra" in p for p in problems))

            quiet(reindex_wikis.regenerate_index, wikis)
            self.assertEqual(reindex_wikis.check_index(wikis), [])

    def test_ghost_dir_without_wiki_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            (wikis / "ghost").mkdir()  # no wiki/ subdir
            self.assertNotIn("ghost", reindex_wikis.discover_wikis(wikis))
            self.assertEqual(reindex_wikis.check_index(wikis), [])

    def test_missing_index_reported_by_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            make_wiki(wikis, "main", "# Main\n")  # no _index.md written
            problems = reindex_wikis.check_index(wikis)
            self.assertTrue(problems)


class Lint(unittest.TestCase):
    def test_fresh_scaffold_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            self.assertEqual(quiet(lint_wiki.lint, str(wikis / "main")), 0)

    def test_cross_wiki_link_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            page = wikis / "main" / "wiki" / "concepts" / "A.md"
            page.write_text("---\ntitle: A\n---\n[[loops/wiki/concepts/B]]\n", encoding="utf-8")
            idx = wikis / "main" / "wiki" / "index.md"
            idx.write_text(idx.read_text().replace("*(none yet)*", "- [[A]]", 1), encoding="utf-8")
            self.assertNotEqual(quiet(lint_wiki.lint, str(wikis / "main")), 0)

    def test_workspace_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            quiet(scaffold_mod.scaffold, str(wikis / "loops"), "Loops")
            self.assertEqual(quiet(lint_wiki.lint_workspace, str(wikis)), 0)

    def test_workspace_fix_repairs_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            make_wiki(wikis, "extra", "---\nsummary: Extra wiki\n---\n# Extra\n")
            # Drift present → non-fix run fails.
            self.assertNotEqual(quiet(lint_wiki.lint_workspace, str(wikis)), 0)
            # Fix run repairs the registry and passes.
            self.assertEqual(quiet(lint_wiki.lint_workspace, str(wikis), True), 0)
            self.assertEqual(reindex_wikis.check_index(wikis), [])


class AuditShape(unittest.TestCase):
    def _wiki(self, tmp):
        wikis = Path(tmp) / "wikis"
        quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
        return wikis / "main"

    def test_valid_audit_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            write_audit(root, "20260101-090000-note.md", VALID_AUDIT)
            self.assertEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_custom_source_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            fields = dict(VALID_AUDIT, source="my-tool")
            write_audit(root, "20260101-090000-note.md", fields)
            self.assertEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_missing_field_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            fields = dict(VALID_AUDIT)
            del fields["author"]
            write_audit(root, "20260101-090000-note.md", fields)
            self.assertNotEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_an_audit_without_severity_is_fine(self):
        """La gravita' e' uscita dal formato il 22/09/2026.

        ``VALID_AUDIT`` non ce l'ha piu', quindi questo e' il caso normale — e
        vale dirlo per nome: il linter non deve chiederla, o ogni segnalazione
        scritta dal telefono nascerebbe gia' segnalata.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            self.assertNotIn("severity", VALID_AUDIT)
            write_audit(root, "20260101-090000-note.md", VALID_AUDIT)
            self.assertEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_an_old_audit_that_still_has_severity_still_passes(self):
        """Le chiavi obbligatorie sono un insieme **aperto**.

        Il controllo e' ``REQUIRED - set(fm.keys())``, quindi una chiave in piu'
        non e' un errore. Sul telefono non esiste nessun file di audit — contati
        il 22/09 su quindici quaderni, aperti e risolti: zero — ma un corpus
        altrui potrebbe averne, e toglierla dal formato non deve rompere i loro.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            fields = dict(VALID_AUDIT, severity="warn")
            write_audit(root, "20260101-090000-note.md", fields)
            self.assertEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_filename_timestamp_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            # id ts 20260101-090000 vs filename ts 20260102-100000
            write_audit(root, "20260102-100000-note.md", VALID_AUDIT)
            self.assertNotEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_duplicate_id_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            write_audit(root, "20260101-090000-a.md", VALID_AUDIT)
            write_audit(root, "20260101-090000-b.md", VALID_AUDIT)
            self.assertNotEqual(quiet(lint_wiki.lint, str(root)), 0)


class DuplicatePages(unittest.TestCase):
    def test_dup_key_normalization(self):
        k = lint_wiki.dup_key
        self.assertEqual(k("Market Making"), k("Market-Making"))
        self.assertEqual(k("Market Making"), k("market_making"))
        self.assertEqual(k("RAG vs LLM Wiki"), k("LLM Wiki vs RAG"))
        # Different scope must NOT collide.
        self.assertNotEqual(k("Market Making"), k("Market Making Strategy"))

    def test_lint_flags_duplicate_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            make_page(root, "concepts/MarketMaking.md", "Market Making")
            make_page(root, "concepts/Market-Making.md", "Market-Making")
            rc, out = run_lint(root)
            self.assertIn("Possible duplicate pages", out)
            self.assertNotEqual(rc, 0)

    def test_index_pages_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            # Two folder-split index.md files must not count as duplicates.
            make_page(root, "concepts/a/index.md", "A")
            make_page(root, "concepts/b/index.md", "B")
            _, out = run_lint(root)
            self.assertIn("No duplicate-looking pages", out)


class SourceIntegrity(unittest.TestCase):
    def test_missing_source_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            make_page(root, "concepts/P.md", "P", sources=["ghost-source"])
            _, out = run_lint(root)
            self.assertIn("sources not found in raw/", out)
            self.assertIn("ghost-source", out)

    def test_valid_source_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            (root / "raw" / "articles" / "realsrc.md").write_text("x", encoding="utf-8")
            make_page(root, "concepts/P.md", "P", sources=["realsrc"])
            _, out = run_lint(root)
            self.assertIn("All cited sources resolve", out)


class AuditReview(unittest.TestCase):
    def test_review_reads_open_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            write_audit(wikis / "main", "20260101-090000-note.md", VALID_AUDIT)
            self.assertEqual(quiet(audit_review.main, str(wikis / "main"), "open"), 0)

    def test_workspace_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            quiet(scaffold_mod.scaffold, str(wikis / "loops"), "Loops")
            self.assertEqual(quiet(audit_review.run_workspace, str(wikis), "open"), 0)
