"""Parità delle chiavi fra i due file i18n della WebUI.

Una chiave presente solo in ``it.json`` (o solo in ``en.json``) non fallisce da
nessuna parte: ``i18n.t()`` ritorna la chiave grezza, e l'utente dell'altra
lingua si ritrova ``subagents.relaunch`` stampato nell'interfaccia. Questo test
è il posto dove quella divergenza si nota.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_I18N_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "i18n"


def _flatten(value: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, child in value.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            keys |= _flatten(child, full)
        else:
            keys.add(full)
    return keys


def _load(locale: str) -> dict:
    return json.loads((_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def test_it_and_en_have_the_same_keys() -> None:
    it_keys = _flatten(_load("it"))
    en_keys = _flatten(_load("en"))
    assert it_keys - en_keys == set(), "chiavi presenti solo in it.json"
    assert en_keys - it_keys == set(), "chiavi presenti solo in en.json"


def test_subagent_panel_strings_exist_in_both_locales() -> None:
    """Il pannello subagent non deve avere stringhe hardcoded nel JS."""
    expected = {
        "subagents.title",
        # Nessuna intestazione "RUNNING"/"RECENT": in carosello il conteggio
        # dell'header e il colore di stato delle card le rendevano superflue.
        # Due varianti del conteggio: senza card terminali "0 appena conclusi"
        # sarebbe rumore, ed è il caso normale.
        "subagents.headCount",
        "subagents.headCountFinished",
        "subagents.untitled",
        "subagents.openDetail",
        "subagents.idle",
        "subagents.attempt",
        "subagents.stalledHint",
        "subagents.autoCapReached",
        "subagents.stop",
        "subagents.relaunch",
        "subagents.stopped",
        "subagents.relaunched",
        "subagents.actionFailed",
    }
    # Nessuna chiave "subagents.empty": il pannello non ha più uno stato vuoto —
    # zero card significa elemento `hidden`, non un placeholder.
    # Etichette della modale di dettaglio: la card mostra una riga, il resto è lì.
    # Manca chi ha perso la sua etichetta nel telaio: stato e orologi stanno nel
    # riepilogo, dove il senso è nell'accostamento e non in una chiave incolonnata
    # ("in corso · 4m 10s · fermo 2s"), e la coda tool non è più un blocco a sé.
    expected |= {
        f"subagents.detail.{field}"
        for field in (
            "type", "attempt", "phase", "iteration", "stopReason",
            "task", "diagnostics", "outcome",
        )
    }
    # Ogni stato e ogni fase che il backend può mandare ha la sua etichetta.
    expected |= {
        f"subagents.state.{state}"
        for state in ("running", "stalled", "done", "failed", "cancelled")
    }
    expected |= {
        f"subagents.phase.{phase}"
        for phase in (
            "initializing",
            "awaiting_tools",
            "tools_completed",
            "final_response",
            "done",
            "error",
        )
    }
    for locale in ("it", "en"):
        keys = _flatten(_load(locale))
        assert expected <= keys, f"chiavi mancanti in {locale}.json: {sorted(expected - keys)}"


def test_workspace_file_help_strings_exist_in_both_locales() -> None:
    """La spiegazione dei file del workspace non vive più dentro i file.

    ``AGENTS.md``, ``USER.md`` e ``memory/MEMORY.md`` nascono
    vuoti: a cosa servono — e cosa *non* ci va scritto — lo dice lo sheet della
    WebUI. ``SOUL.md`` nasce pieno, ma è la destinazione indicata dagli
    altri testi, quindi la sua voce non è meno obbligatoria delle altre.
    La parità fra le due lingue non basta a proteggerlo: una chiave
    cancellata in tutt'e due resta simmetrica, e l'unico segno sarebbe uno
    sheet che non compare più. Qui l'elenco è esplicito.
    """
    expected = {
        # Azione primaria: lo sheet spiega, ma l'editor deve restare raggiungibile.
        "workspace.fileHelp.open",
        "workspace.fileHelp.agents",
        "workspace.fileHelp.user",
        "workspace.fileHelp.soul",
        "workspace.fileHelp.memory",
    }
    for locale in ("it", "en"):
        keys = _flatten(_load(locale))
        assert expected <= keys, f"chiavi mancanti in {locale}.json: {sorted(expected - keys)}"


def test_workspace_file_help_map_points_at_existing_keys() -> None:
    """Il mapping path→chiave nel JS e i due JSON devono dire la stessa cosa.

    ``fileHelpText()`` ripiega su stringa vuota quando la chiave non esiste, e
    una chiave sbagliata quindi non rompe niente: fa sparire lo sheet in
    silenzio. Questo test la vede.
    """
    import re

    js = (_I18N_DIR.parent / "mobile-workspace.js").read_text(encoding="utf-8")
    block = re.search(r"const FILE_HELP_KEYS = \{(.*?)\n\};", js, re.S)
    assert block is not None, "FILE_HELP_KEYS non trovato in mobile-workspace.js"
    mapping = dict(re.findall(r"'([^']+)':\s*'([^']+)'", block.group(1)))

    assert set(mapping) == {"AGENTS.md", "USER.md", "SOUL.md", "memory/MEMORY.md"}
    for locale in ("it", "en"):
        keys = _flatten(_load(locale))
        missing = set(mapping.values()) - keys
        assert missing == set(), f"chiavi referenziate dal JS ma assenti in {locale}.json: {sorted(missing)}"


def test_dead_subagent_keys_are_gone_from_both_locales() -> None:
    """Una chiave che nessuno legge più è debito: la parità la terrebbe in vita.

    ``subagents.empty`` serviva al placeholder di un pannello vuoto, che non
    esiste più (zero card = elemento ``hidden``). Le quattro etichette della
    modale sono cadute con il telaio: stato e orologi vivono accostati nel
    riepilogo, senza chiave davanti, e "Tool recenti" non è più un blocco perché
    la coda dello snapshot è diventata il ripiego della lista di attività.
    """
    ui_assets = _I18N_DIR.parent
    chat_js = (ui_assets / "mobile-chat.js").read_text(encoding="utf-8")
    for dead in ("subagents.empty", "subagents.detail.state", "subagents.detail.elapsed",
                 "subagents.detail.idle", "subagents.detail.toolEvents"):
        assert dead not in chat_js
        for locale in ("it", "en"):
            assert dead not in _flatten(_load(locale)), f"{dead} è ancora in {locale}.json"


def test_placeholders_match_between_locales() -> None:
    """Gli stessi ``{segnaposto}`` nelle due lingue: uno mancante stampa ``{n}``."""
    import re

    def placeholders(text: str) -> set[str]:
        return set(re.findall(r"\{(\w+)\}", text))

    def flat_strings(value: dict, prefix: str = "") -> dict[str, str]:
        out: dict[str, str] = {}
        for key, child in value.items():
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict):
                out.update(flat_strings(child, full))
            elif isinstance(child, str):
                out[full] = child
        return out

    it_strings = flat_strings(_load("it"))
    en_strings = flat_strings(_load("en"))
    mismatched = [
        key
        for key, text in it_strings.items()
        if key in en_strings and placeholders(text) != placeholders(en_strings[key])
    ]
    assert mismatched == []


def _leaves(value: dict, prefix: str = ""):
    for key, child in value.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            yield from _leaves(child, full)
        else:
            yield full, child


def test_a_notebook_is_never_called_a_project_on_screen() -> None:
    """La cartella con le sue pagine e la sua conversazione è un quaderno, in casa
    come in officina; «progetto» e «scope» restano nomi interni (`project:`,
    `PROJECT_SESSION_PREFIX`). Erano tre vocabolari per una cosa sola.

    Fa eccezione ``workspace.fileHelp.*``, dove «progetto» non è la cartella ma
    quel che l'utente sta facendo (il contesto di lavoro in MEMORY.md)."""
    word = re.compile(r"progett|project|\bscope\b", re.IGNORECASE)
    for locale in ("it", "en"):
        for key, text in _leaves(_load(locale)):
            if key.startswith("workspace.fileHelp."):
                continue
            assert not word.search(str(text)), f"{locale}: {key} dice ancora «progetto»/«scope»"


def test_no_text_sends_to_a_screen_that_no_longer_exists() -> None:
    """«Tu e Jenny» / «You and Jenny» era la pagina delle impostazioni della
    casa; oggi il backup sta in Impostazioni › Backup, e il testo di «Local
    history» mandava ancora là (collaudo del 27/09/2026)."""
    gone = re.compile(r"Tu e Jenny|You and Jenny")
    for locale in ("it", "en"):
        for key, text in _leaves(_load(locale)):
            assert not gone.search(str(text)), f"{locale}: {key} manda a «Tu e Jenny»"
