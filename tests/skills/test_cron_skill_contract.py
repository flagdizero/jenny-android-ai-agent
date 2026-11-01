"""La skill `cron` è il manuale: sintassi, fusi orari, esempi, semantica di ``list``.

Nasce come rimedio a un'asimmetria — le skill si ri-estraggono a OGNI avvio senza
``skip_existing`` (``jenny/utils/helpers.py``), ``AGENTS.md`` è in
``_USER_OWNED_TEMPLATES`` e si crea una volta sola, quindi una regola scritta solo
lì non raggiungeva mai un'installazione esistente. Il difetto è sopravvissuto
proprio così: la skill descriveva ancora "Three Modes" (una tassonomia che precede
il parametro ``mode``) e portava come esempio l'anti-pattern esatto, mentre la
guida corretta viveva in un file mai aggiornato sul telefono dell'utente.

Quel rimedio non serve più. La regola di *instradamento* — un singolo o un
ricorrente, `reminder` o `monitor` — sta in ``jenny/templates/agent/scheduling.md``, che è codice e si
riscrive a ogni boot; ``AGENTS.md`` non la contiene più affatto. Le asserzioni qui
sotto restano, ma cambiano di mestiere: non presidiano più una scorciatoia, tengono
fermo il posto della skill nel confine fra prompt di sistema e manuale.
Il manuale sta qui perché lo si legge su richiesta, mentre ``agent/scheduling.md`` si
paga a ogni turno; ed è ancora questa la pagina che il prompt dice di leggere *prima*
di schedulare.
"""

from __future__ import annotations

import re
from pathlib import Path

from jenny.agent.tools.cron import _JOB_MODES
from jenny.utils.android_assets import _SKILLS_MANIFEST, _USER_OWNED_TEMPLATES

SKILL = Path(__file__).resolve().parents[2] / "jenny" / "skills" / "cron" / "SKILL.md"


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_the_skill_is_shipped() -> None:
    assert "cron/SKILL.md" in _SKILLS_MANIFEST


def test_it_documents_every_real_mode() -> None:
    text = _skill()
    for mode in _JOB_MODES:
        assert f'mode="{mode}"' in text, f"la skill non nomina mode={mode!r}"


def test_the_stale_taxonomy_is_gone() -> None:
    """"Three Modes: Reminder / Task / One-time" precede il parametro ``mode``.

    Finché era lì, l'agente leggeva che il parametro non esisteva.
    """
    text = _skill()
    assert "Three Modes" not in text
    assert not re.search(r"^\s*2\.\s*\*\*Task\*\*", text, re.M)


def test_it_does_not_teach_the_anti_pattern() -> None:
    """L'esempio storico era ``message="Check GitHub stars and report"`` *senza*
    ``mode``: un controllo ricorrente creato come reminder, che poi parla a ogni
    ciclo. Se un esempio ricorrente resta senza ``mode``, torna il difetto."""
    for block in re.findall(r"cron\(action=\"add\"[^)]*\)", _skill(), re.S):
        if "every_seconds" in block or "cron_expr" in block:
            assert "mode=" in block, f"esempio ricorrente senza mode: {block}"


def test_it_says_that_a_conditional_request_is_a_monitor() -> None:
    # Whitespace normalizzato: l'asserzione è sul contenuto, non su dove il
    # markdown va a capo.
    text = re.sub(r"\s+", " ", _skill().lower())
    assert "conditional" in text
    assert "only tell me if" in text
    assert "any request phrased as a condition is a `monitor`" in text


def test_it_forbids_the_filler_the_user_actually_received() -> None:
    text = _skill()
    for filler in ("All clear.", "All done.", "nothing to report"):
        assert filler in text, f"la skill non nomina il riempitivo {filler!r}"
    assert "never send filler" in text.lower()


def test_it_calls_silence_a_success() -> None:
    text = _skill().lower()
    assert "silence is a **correct, successful outcome**" in _skill()
    assert "silenced" in text, "action='list' riporta 'silenced': va spiegato"


def test_it_covers_both_kinds_and_the_new_parameters() -> None:
    """Due tipi soli: un singolo (``at`` o ``date``) o un ricorrente, che può
    portare una ``condition``. L'heartbeat non c'è più (31/10/2026): un manuale
    che lo nominasse manderebbe a scrivere in un file che nessuno legge."""
    text = _skill()
    assert "HEARTBEAT.md" not in text
    assert 'date="' in text, "manca un esempio di singolo senza ora"
    assert "condition=" in text, "manca un esempio di ricorrente con condizione"
    assert "disappears once it has fired" in text


def test_agents_md_is_not_the_only_home_of_the_rule() -> None:
    """La regola non deve tornare a vivere in un file che non si aggiorna.

    Nata come guardia su un'asimmetria — ``AGENTS.md`` fermo, la skill no — e
    sopravvissuta alla sua causa: la metà di sistema di ``AGENTS.md`` è stata
    spostata in ``agent/scheduling.md``, che si riscrive a ogni avvio. Resta
    perché il vincolo che pone è ancora quello giusto: ``AGENTS.md`` è e resta
    un file dell'utente, quindi la regola operativa non può abitare lì.
    """
    assert "AGENTS.md" in _USER_OWNED_TEMPLATES
    # Scegliere il mode è profondità operativa: vive nella skill, che si legge
    # su richiesta, non nel prompt che si paga a ogni turno.
    assert 'mode="monitor"' in _skill()
