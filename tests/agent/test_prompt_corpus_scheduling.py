"""La regola su dove va un lavoro ricorrente, cercata in *tutto* il testo che il modello legge.

``test_no_other_system_prompt_teaches_scheduling`` (``test_context_builder.py``)
esiste per impedire che quella regola si riaccumuli, ed è nata dopo che una sua
quinta copia era sopravvissuta in ``agent/tool_contract.md`` per il motivo più
banale: nessuno aveva guardato in quel file. Aveva però due buchi, entrambi
verificati, ed entrambi la stessa forma del difetto che doveva chiudere.

**(a) Non vedeva le stringhe Python.** Setacciava ``_SYSTEM_PROMPT_TEMPLATES``,
ma il prompt di sistema non è fatto solo di template: ``_UPDATE_PREAMBLE`` sta
in ``jenny/runtime/cron_dispatch.py``, il blocco del seguito di un monitor
delegato in ``jenny/cron/monitor_followup.py``, e ogni
``Tool.description`` con le description del proprio schema di parametri sta nei
moduli di ``jenny/agent/tools/`` — dove ``cron.py`` da solo tiene quattro copie
della regola sui modi. Tutto questo raggiunge il modello, e la guardia non
poteva vederlo: il prompt di sistema non è fatto solo di template.

**(b) Cercava letterali sensibili alle virgolette.** Cercava ``mode='reminder'``
con l'apice singolo; ``skills/cron/SKILL.md`` scrive ``mode="reminder"``. Una
sezione riaggiunta nello stile della skill, o che dicesse ``use `cron` `` invece
di "use the cron tool", non conteneva nessuna delle cinque frasi e passava
pulita. La guardia riconosceva il testo che era stato cancellato, non la regola.

Qui il corpus è l'unione di tutto, e i pattern sono regex indifferenti alle
virgolette. Le due case legittime restano due:

* ``jenny/templates/agent/scheduling.md`` — nel prompt, ma **solo** nei turni in
  cui il tool ``cron`` esiste davvero (v. ``TestSchedulingBlock``);
* ``jenny/skills/cron/SKILL.md`` — il manuale, che si legge su richiesta.

Più una terza voce che non è una copia ma lo schema stesso del tool: v.
``_ALLOWED``, che si spiega da sé.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from jenny.agent.tools.loader import ToolLoader
from jenny.agent.tools.registry import ToolRegistry
from jenny.utils.android_assets import _SKILLS_MANIFEST, _SYSTEM_PROMPT_TEMPLATES
from jenny.utils.helpers import load_bundled_template

_JENNY = Path(__file__).resolve().parents[2] / "jenny"

# Le due case della regola. Non stanno in ``_ALLOWED`` perché non sono
# eccezioni: sono il posto dove la regola deve stare, e un test che non le
# vedesse più sarebbe rotto, non contento (v.
# ``test_the_sweep_still_finds_the_rule_where_it_lives``).
_HOMES = ("template agent/scheduling.md", "skill cron/SKILL.md")


# ---------------------------------------------------------------------------
# Il corpus: tutto il testo che raggiunge il modello
# ---------------------------------------------------------------------------

# Sotto questa soglia una stringa Python è un frammento (una chiave, un
# separatore, un pezzo di f-string), non prosa rivolta al modello. Il filtro è
# volutamente generoso: nel corpus finiscono anche righe di log, che
# semplicemente non fanno match e non costano niente.
_PROMPT_SHAPED_MIN_LEN = 40

# I package i cui letterali Python sono prompt a tutti gli effetti. Non è
# l'intero repo: sono i due posti che compongono a mano il testo di un turno
# schedulato, cioè esattamente quelli che il roadmap indica come il perimetro da
# setacciare insieme ai template.
_PROMPT_PACKAGES = ("cron", "runtime")


def _literal_text(node: ast.AST) -> str | None:
    """Il testo di una costante str, di una f-string o di una concatenazione.

    Delle f-string si prendono solo le parti letterali: i valori interpolati non
    sono testo sorgente e cambiano a ogni run.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        return None if left is None or right is None else left + right
    return None


def _string_literals(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Ogni letterale stringa dell'albero, docstring escluse.

    Le docstring sono in italiano e non le legge nessun modello: includerle
    riempirebbe il corpus di commenti che parlano di cron *a proposito* del
    codice, che è precisamente il falso positivo da non generare.
    """
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
    }

    def walk(node: ast.AST) -> Iterator[tuple[int, str]]:
        if id(node) in docstrings:
            return
        text = _literal_text(node)
        if text is not None:
            # Non si scende: i pezzi di una concatenazione sono già qui dentro,
            # e riportarli anche singolarmente duplicherebbe ogni match.
            yield getattr(node, "lineno", 0), text
            return
        for child in ast.iter_child_nodes(node):
            yield from walk(child)

    yield from walk(tree)


def _schema_descriptions(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Ogni ``description`` annidata in uno schema JSON di parametri."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield path, value
            else:
                yield from _schema_descriptions(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _schema_descriptions(item, f"{path}[{index}]")


def _model_facing_corpus() -> Iterator[tuple[str, str]]:
    """``(source, text)`` per ogni pezzo di testo che il modello può leggere."""
    for name in _SYSTEM_PROMPT_TEMPLATES:
        yield f"template {name}", load_bundled_template(name) or ""

    for rel in _SKILLS_MANIFEST:
        if not rel.endswith(".md"):
            continue
        path = _JENNY / "skills" / rel
        if path.is_file():
            yield f"skill {rel}", path.read_text(encoding="utf-8")

    # Il ctx finto tiene ``enabled()`` permissivo: serve il perimetro completo
    # dei tool, non quelli attivi in una configurazione particolare. Stesso
    # motivo, e stessa forma, di ``tests/agent/tools/test_schema_wire_limits.py``.
    registry = ToolRegistry()
    for tool_name in ToolLoader().load(MagicMock(), registry, scope="core"):
        tool = registry.get(tool_name)
        yield f"tool {tool_name}.description", tool.description
        for path_, description in _schema_descriptions(tool.parameters):
            yield f"tool {tool_name}.parameters{path_}", description

    for package in _PROMPT_PACKAGES:
        for source in sorted((_JENNY / package).rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for lineno, text in _string_literals(tree):
                if len(text) >= _PROMPT_SHAPED_MIN_LEN and text.strip().count(" ") >= 1:
                    yield f"py {source.relative_to(_JENNY.parent)}:{lineno}", text


# ---------------------------------------------------------------------------
# I pattern: la regola, non il testo che la esprimeva
# ---------------------------------------------------------------------------

# Qualunque virgoletta, o nessuna. È il buco (b): la lista di frasi cercava
# ``mode='reminder'`` e la skill scrive ``mode="reminder"``.
_Q = r"['\"`‘’“”]?"

_TEACHING = {
    # Il parametro `mode` legato a un valore: la forma più diretta di "ecco come
    # si crea un job".
    "mode=<valore>": re.compile(rf"\bmode\s*[=:]\s*{_Q}(reminder|monitor){_Q}", re.I),
    # I due modi nominati *come valori* (virgoletta obbligatoria). Distingue
    # `` `monitor` `` — il modo — da "monitor" parola comune, che compare in
    # ``cron_monitor.md`` e in ``bound_runner.py`` senza insegnare niente.
    "modo citato": re.compile(r"['\"`‘’“”](reminder|monitor)['\"`‘’“”]", re.I),
    "chiamata a cron()": re.compile(r"\bcron\s*\(\s*action", re.I),
    "parametri di schedulazione": re.compile(r"\b(every_seconds|cron_expr)\b"),
    # `cron` indicato come *destinazione* di un lavoro. Il suffisso
    # `tool`/`job` obbligatorio e il ``(?![_\w])`` tengono fuori "cron_expr" e
    # la prosa dei job già creati ("Execute this scheduled cron job now").
    "cron come destinazione": re.compile(
        rf"\b(use|using|call|invoke|creat\w+|add|register|is|are)\b[^.\n]{{0,30}}"
        rf"{_Q}cron(?![_\w]){_Q}\s*(tool|job)\b",
        re.I,
    ),
    # Sostituisce il letterale "recurring jobs": qui conta il verbo di
    # creazione, non il sostantivo. "this recurring check has not been working"
    # (``cron_monitor.md``) parla di guasti, non di come si crea un job.
    "creare lavoro ricorrente": re.compile(
        r"\b(schedul\w+|creat\w+|add|set up|register)\b[^.\n]{0,60}\b(recurring|repeating)\b"
        r"[^.\n]{0,25}\b(job|task|check|work)s?\b",
        re.I,
    ),
    # Il test di riconoscimento — "only tell me if…" — accanto a una
    # destinazione. È la parte della regola che più costa riscoprire, e quella
    # che una copia parziale copia per prima.
    "condizionale → destinazione": re.compile(
        r"(only tell me if|warn me when|let me know if)[\s\S]{0,240}?"
        r"['\"`](monitor|reminder)['\"`]",
        re.I,
    ),
}

# Le eccezioni, e il perché di ognuna. Un'allowlist che non si spiega diventa in
# fretta il posto dove si mette quello che non si ha voglia di sistemare.
_ALLOWED: dict[str, str] = {
    # Lo schema di un tool NON è una copia della regola: è la firma del tool, e
    # il modello la legge nel momento in cui compone la chiamata. Toglierla da
    # lì significherebbe chiedergli di indovinare i valori ammessi di `mode`.
    # Il consolidamento fra `skills/cron/SKILL.md` e la description del tool.
    #
    # Attenzione: è una deroga sulla *descrizione dei parametri*, non sulla
    # regola di instradamento, che resta nelle due case.
    "tool cron.description": "lo schema del tool: il modello lo legge mentre compone la chiamata",
    "tool cron.parameters": "idem — description del blocco parametri",
    "tool cron.parameters.properties.mode": "idem — i valori ammessi di `mode`",
    "tool cron.parameters.properties.tz": "idem — nomina `cron_expr` come campo, non come regola",
    "tool cron.parameters.properties.condition": (
        "idem — dice con quali schedulazioni vale il filtro, non dove va un lavoro"
    ),
}


def _violations() -> list[tuple[str, str, str]]:
    """``(source, pattern, context)`` per ogni match fuori dalle due case."""
    found: list[tuple[str, str, str]] = []
    for source, text in _model_facing_corpus():
        if source in _HOMES or source in _ALLOWED:
            continue
        for label, pattern in _TEACHING.items():
            match = pattern.search(text)
            if match is None:
                continue
            lo, hi = max(0, match.start() - 90), min(len(text), match.end() + 90)
            found.append((source, label, " ".join(text[lo:hi].split())))
    return found


def test_the_routing_rule_lives_nowhere_else() -> None:
    violations = _violations()
    assert not violations, (
        "Questo testo insegna dove va un lavoro ricorrente, e non è uno dei due posti "
        "in cui quella regola può stare:\n"
        + "\n".join(f"  {src}\n    [{label}] …{ctx}…" for src, label, ctx in violations)
        + "\n\nLe case sono `jenny/templates/agent/scheduling.md` — l'unica resa solo quando il "
        "tool `cron` esiste nel turno — e `jenny/skills/cron/SKILL.md`, che si legge su "
        "richiesta invece che a ogni turno. Una terza copia non resta allineata: è già "
        "successo con `agent/tool_contract.md`. Se il match è un uso innocuo di `cron` o "
        "`monitor` come parole comuni, la strada giusta è restringere il pattern; "
        "`_ALLOWED` solo quando il testo la regola la dice davvero e ha una ragione per farlo."
    )


def test_the_sweep_still_finds_the_rule_where_it_lives() -> None:
    """Anti-verde-fasullo: se i pattern smettono di riconoscere le due case, il
    test sopra passa senza aver controllato niente.

    È lo stesso modo in cui la guardia precedente era diventata cieca: cercava
    ``mode='reminder'`` mentre la skill scriveva ``mode="reminder"``.
    """
    by_source = dict(_model_facing_corpus())
    for home in _HOMES:
        assert home in by_source, f"{home} non è più nel corpus"
        matched = {label for label, p in _TEACHING.items() if p.search(by_source[home])}
        assert len(matched) >= 3, (
            f"{home} fa match solo su {sorted(matched)}: i pattern non riconoscono più la "
            "regola nemmeno dove è scritta apposta, quindi non la riconoscerebbero altrove."
        )


def test_the_sweep_reaches_every_kind_of_source() -> None:
    """Il corpus deve contenere tutte e quattro le famiglie.

    Il buco (a) era esattamente questo: un setaccio che guardava solo i template
    e sembrava completo. Un import spostato o un loader che smette di restituire
    tool lo riaprirebbe in silenzio.
    """
    sources = [source for source, _ in _model_facing_corpus()]
    counts = {
        kind: sum(1 for source in sources if source.startswith(kind))
        for kind in ("template ", "skill ", "tool ", "py ")
    }
    assert counts["template "] == len(_SYSTEM_PROMPT_TEMPLATES)
    assert counts["skill "] >= 8, counts
    assert counts["tool "] >= 40, counts
    assert counts["py "] >= 30, counts
    # Le stringhe Python che il roadmap nomina una per una devono essere dentro.
    assert any(source.startswith("py jenny/runtime/cron_dispatch.py") for source in sources)
    assert any(source.startswith("py jenny/cron/monitor_followup.py") for source in sources)


# ---------------------------------------------------------------------------
# I marcatori del prompt di un monitor
# ---------------------------------------------------------------------------


def test_the_monitor_prompt_uses_the_real_marker_values() -> None:
    """``cron_monitor.md`` scrive i marcatori a mano, il parser li legge dalle
    costanti di ``jenny/cron/could_not_check.py``. Cambiare il valore di una
    costante lascerebbe il prompt a chiedere al modello una riga che il parser
    non riconosce più.
    """
    from jenny.cron.could_not_check import (
        COULD_NOT_CHECK_MARKER,
        DELEGATED_MARKER,
        WARNED_MARKER,
    )

    text = load_bundled_template("agent/cron_monitor.md") or ""
    for marker in (COULD_NOT_CHECK_MARKER, DELEGATED_MARKER, WARNED_MARKER):
        assert marker in text, (
            f"il prompt del monitor non chiede più una riga `{marker}`: il valore della "
            "costante in `jenny/cron/could_not_check.py` è cambiato e la copia scritta a "
            "mano è rimasta indietro."
        )
