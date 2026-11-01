"""Guardia: il vocabolario delle session key vive in un solo modulo.

Il confine "sessione interna vs conversazione utente" è stato ricopiato quattro
volte (``session/keys.py``, ``agent/memory.py``, ``agent/autocompact.py``,
``agent/token_usage.py``) senza che nessun test confrontasse mai due copie fra
loro. La quarta — ``token_usage`` — è sopravvissuta più a lungo delle altre
proprio perché non era una tupla di prefissi ma una catena di ``if/elif``, che
una ricerca per letterali di tupla non intercetta.

Questo test fa uno sweep ``ast`` su ``jenny/`` e ``tests/`` e conta i membri
**distinti** del vocabolario che compaiono in *posizione di classificazione*:

- elemento di un letterale tupla/set/lista, o chiave di un letterale dict;
- argomento di ``.startswith()`` / ``.endswith()``;
- operando di un ``Compare`` (``key == "unified:default"``).

Perché proprio questa forma, e non "qualunque occorrenza": il vocabolario
compare legittimamente in una ventina di punti che non classificano niente —
``jenny/runtime/notifier.py`` costruisce ``f"cron:{label}"`` come *tag* di una
notifica, ``jenny/cron/session_turns.py`` conia ``f"cron:{job_id}"`` come chiave
di un monitor. Sono usi produttori o di etichettatura, non decisioni sul confine, e una
versione ingenua li segnalerebbe tutti. Filtrando sulla posizione sintattica
restano solo i punti che davvero *decidono*.

La soglia è 2 perché un modulo che nomina un solo membro sta trattando un caso
particolare, non replicando la partizione.

**Esteso alla terza categoria** quando ``project:`` è nato (2026-08-21): la
partizione da difendere non è più "interna sì/no" ma
interna/progetto/personale, e la forma sbagliata da intercettare è la stessa —
un modulo che si ricopia i prefissi invece di chiedere a
``jenny.session.keys.session_kind``. Il prefisso di progetto è quello che costa
di più a lasciar copiare: sbagliarlo non fa sparire una funzione, mette la
conversazione di un progetto dentro ``MEMORY.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# I membri del vocabolario, nella forma testuale in cui li si scrive nel codice.
# ``project:`` sta nello stesso insieme e non in uno a parte: chi classifica una
# session key deve chiedere a ``keys.py``, e la domanda è una sola quale che sia
# la categoria che gli interessa.
#
# **Esteso al lato personale** con T4.10 (2026-08-23): da quando ``personal`` è
# una whitelist e non il residuo, i suoi membri — la sessione unica e i prefissi
# dei canali legacy — sono vocabolario di classificazione come gli altri, e
# ricopiarseli ha lo stesso costo di prima al contrario (una copia che dimentica
# le legacy fa sparire a Dream la storia già sul disco).
VOCABULARY = frozenset(
    {
        "subagent:",
        "cron:",
        "dream:",
        "gardener:",
        "internal:",
        "project:",
        "unified:default",
        "websocket:",
        "telegram:",
    }
)

# Chi può nominarne quanti vuole: il modulo canonico (è la sua definizione) e
# questo file (l'insieme qui sopra è il dato del test, non una quinta copia).
#
# ``test_keys.py`` è il terzo, e per una ragione scritta nella sua docstring: il
# suo mestiere è **inchiodare i valori letterali**, perché sono persistiti nei
# file di sessione su disco. Nomina ``"unified:default"`` e ``"dream:"`` in
# posizione di confronto per dire "questa stringa non deve cambiare", che è
# l'opposto di ricopiarsi la partizione.
EXEMPT = frozenset(
    {
        Path("jenny/session/keys.py"),
        Path("tests/session/test_internal_key_vocabulary.py"),
        Path("tests/session/test_keys.py"),
    }
)

MAX_VOCABULARY_MEMBERS = 1


def _string_constants(nodes) -> set[str]:
    return {
        node.value
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _classification_literals(tree: ast.AST) -> set[str]:
    """I letterali stringa che il modulo usa per classificare una session key."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.Set, ast.List)):
            found |= _string_constants(node.elts)
        elif isinstance(node, ast.Dict):
            found |= _string_constants(k for k in node.keys if k is not None)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"startswith", "endswith"}
        ):
            found |= _string_constants(node.args)
        elif isinstance(node, ast.Compare):
            found |= _string_constants([node.left, *node.comparators])
    return found


def _sources() -> list[Path]:
    paths: list[Path] = []
    for package in ("jenny", "tests"):
        paths.extend(sorted((REPO_ROOT / package).rglob("*.py")))
    return paths


def _offenders() -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _sources():
        relative = path.relative_to(REPO_ROOT)
        if relative in EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        members = _classification_literals(tree) & VOCABULARY
        if len(members) > MAX_VOCABULARY_MEMBERS:
            offenders[relative.as_posix()] = sorted(members)
    return offenders


class TestInternalKeyVocabulary:
    def test_no_module_reimplements_the_partition(self):
        """Nessun modulo fuori da ``keys.py`` classifica su ≥2 membri propri."""
        offenders = _offenders()
        assert offenders == {}, (
            "questi moduli si sono ricopiati il vocabolario delle sessioni "
            "interne invece di importarlo da jenny.session.keys: " + repr(offenders)
        )

    def test_sweep_actually_sees_an_ifelif_chain(self):
        """Lo sweep intercetta anche la forma che era sfuggita: ``if/elif``.

        Senza questo, un domani qualcuno può restringere ``_classification_literals``
        ai soli letterali di tupla e il test resterebbe verde pur avendo smesso
        di guardare la forma che aveva lasciato passare la quarta copia.
        """
        tree = ast.parse(
            "def f(key):\n"
            "    if key.startswith('dream:'):\n"
            "        return 'dream'\n"
            "    if key == 'unified:default':\n"
            "        return 'user'\n"
            "    return 'internal'\n"
        )
        assert _classification_literals(tree) & VOCABULARY == {"dream:", "unified:default"}

    def test_the_project_prefix_is_in_the_swept_vocabulary(self):
        """La terza categoria è difesa come le altre, non solo documentata.

        Senza questo, aggiungere ``project:`` all'insieme resta un gesto che
        qualcuno può disfare per far passare un modulo, e il costo lo si scopre
        dal contenuto di ``MEMORY.md``.
        """
        assert "project:" in VOCABULARY
        tree = ast.parse(
            "def f(key):\n"
            "    if key.startswith('project:'):\n"
            "        return 'project'\n"
            "    if key.startswith('cron:'):\n"
            "        return 'internal'\n"
            "    return 'personal'\n"
        )
        assert _classification_literals(tree) & VOCABULARY == {"project:", "cron:"}

    def test_sweep_ignores_producers_and_labels(self):
        """Un f-string produttore o un'etichetta non sono classificazione."""
        tree = ast.parse(
            "def f(job_id, label):\n"
            "    key = f'cron:{job_id}'\n"
            "    tag = f'cron:{label}'\n"
            "    job = dict(id='dream')\n"
            "    return key, tag, job\n"
        )
        assert _classification_literals(tree) & VOCABULARY == set()

    def test_sweep_covers_the_whole_tree(self):
        """Guardia sulla guardia: se lo sweep smette di trovare file, si accorge."""
        assert len(_sources()) > 300

    def test_the_swept_vocabulary_is_the_registered_one(self):
        """``VOCABULARY`` è un dato scritto a mano: qui si controlla che sia aggiornato.

        **Era già stale.** ``gardener:`` è registrato in
        ``_INTERNAL_KIND_BY_PREFIX`` da quando il giardiniere esiste e non era in
        questo insieme: lo sweep non l'ha mai cercato, quindi un modulo che si
        ricopiasse ``("cron:", "gardener:")`` sarebbe passato con un membro solo
        visto su due. È il difetto tipico di un elenco a mano che difende un
        elenco a mano, e si chiude derivandone uno dall'altro.

        Il verso è uno solo: ogni membro *registrato* deve essere spazzato. Il
        contrario no — ``"websocket:"`` e ``"telegram:"`` sono prefissi legacy che
        il modulo tiene in una tupla a parte, e la whitelist personale nomina
        ``unified:default`` per uguaglianza.
        """
        from jenny.session import keys

        registered = (
            {prefix for prefix, _ in keys._INTERNAL_KIND_BY_PREFIX}
            | set(keys._PERSONAL_SESSION_KEYS)
            | set(keys._LEGACY_CHANNEL_KEY_PREFIXES)
            | {keys.PROJECT_SESSION_PREFIX}
        )
        assert registered <= VOCABULARY, (
            "questi membri sono registrati in jenny.session.keys ma lo sweep non li "
            f"cerca: {sorted(registered - VOCABULARY)}"
        )
