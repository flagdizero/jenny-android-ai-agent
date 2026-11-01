"""Guardia: ogni modulo di `jenny/` regge di essere il primo import di un interprete.

Il 2026-08-17 `jenny/session/manager.py` importava `jenny.cron.session_turns` in
testa, e quel modulo importa `jenny.session.keys` (:8). Toccare una foglia di
`jenny.session` eseguiva `jenny/session/__init__.py`, che carica `manager`, che
tornava su `session_turns` ancora a metà inizializzazione: `ImportError` su
`CRON_HISTORY_META`. La correzione è l'import dentro la funzione che lo usa.

**Perché nessun controllo del progetto lo vedeva.** In una raccolta completa di
pytest qualcosa carica `jenny.session` prima di `jenny.cron`, quindi il ciclo non
scatta mai: la suite era verde, `pytest tests/session/` era verde (137 test), e
`npx pyright jenny/session` — che è nel sottoinsieme bloccante — era a zero errori,
perché pyright non modella i cicli di import. Si vedeva solo eseguendo
`tests/session/test_webui_turns.py` da solo, o importando quei moduli a freddo.

**Perché lo sweep è completo e non un elenco curato.** La prima versione di questo
file elencava a mano undici moduli, e un elenco scritto a mano ha un solo modo di
sbagliare: il modulo che nessuno ci aggiunge. Lo sweep prende ogni `.py` sotto
`jenny/` — 233 moduli — ognuno in un interprete nuovo, in parallelo: circa due
secondi in tutto. Un test in-process non potrebbe farlo comunque, perché
`sys.modules` è già caldo quando parte, ed è esattamente la condizione che
nascondeva il difetto.

Un ciclo non è di per sé fatale: Python ne tollera molti, a seconda di chi carica
per primo e di cosa si guarda durante l'import. Uno tollerato resta in questo
albero (`agent.memory` ↔ `agent.consolidator`) e non è il bersaglio: qui si misura
la rottura vera, cioè un import che *solleva*.

"Tollerato" però va letto per quel che vale: `utils.file_edit_events` ↔
`utils.file_edit_streaming` era in quella lista, e tollerato voleva dire
"funziona nell'ordine che qualcuno ha provato" — importare la seconda per prima
sollevava. È stato sciolto, insieme al ciclo `webui` ↔ `channels` che teneva fuori
altri tre moduli. Lo sweep ora è verde su tutti e 233, ed è la condizione da
mantenere.
"""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PKG = _REPO / "jenny"

# Moduli che NON reggono di essere importati per primi. **Vuoto**, e va tenuto
# vuoto: l'insieme esiste per nominare il debito, non per ospitarlo.
#
# Ci sono stati quattro nomi qui, tutti preesistenti a questo lavoro, e sono
# bastate due correzioni. Tre (`webui.gateway_services`, `webui.media_api`,
# `webui.media_gateway`) erano lo stesso ciclo: `media_api` importa
# `jenny.channels.http_utils`, che esegue `jenny/channels/__init__.py`, che carica
# `websocket` → `ws_sender`, che in testa importava `media_attachment_kind` da
# `media_api` — ancora a metà. Ora quell'import sta dentro la funzione che lo usa.
# Il quarto (`utils.file_edit_streaming`) era un re-export in coda a
# `file_edit_events`: funzionava entrando dal lato che qualcuno aveva provato e
# sollevava dall'altro. I due chiamanti ora importano dalla sede vera.
#
# Se devi rimetterci un nome, scrivi accanto **perché** e cos'è la correzione.
_KNOWN_COLD_IMPORT_FAILURES: set[str] = set()


def _module_names() -> list[str]:
    names = set()
    for path in _PKG.rglob("*.py"):
        # Gli script dentro le skill non sono importabili per nome (contengono `-`)
        # e non fanno parte del package.
        if "scripts" in path.parts:
            continue
        rel = path.relative_to(_REPO).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        names.add(".".join(parts))
    return sorted(names)


def _import_cold(module: str) -> tuple[str, int, str]:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    return module, result.returncode, result.stderr


def _sweep() -> dict[str, str]:
    """`modulo -> stderr` per ogni modulo che non regge un import a freddo."""
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = pool.map(_import_cold, _module_names())
    return {name: err for name, code, err in results if code != 0}


def test_no_new_module_fails_a_cold_import() -> None:
    failures = _sweep()
    unexpected = sorted(set(failures) - _KNOWN_COLD_IMPORT_FAILURES)

    assert not unexpected, (
        "Questi moduli non reggono di essere il primo import di un interprete:\n"
        + "\n".join(f"  {name}\n{failures[name].rstrip()}" for name in unexpected)
        + "\n\nQuasi sempre un ciclo di import a livello di modulo. È verde in una "
        "raccolta completa e fatale a freddo — il gateway non parte — e la "
        "correzione più piccola è spostare uno dei due import dentro la funzione "
        "che lo usa."
    )


def test_the_known_failures_are_still_failing() -> None:
    """Se ne chiudi uno, togli la sua riga da `_KNOWN_COLD_IMPORT_FAILURES`.

    Con l'insieme vuoto questo test non asserisce niente, e resta comunque: è ciò
    che impedisce a un nome di restare nell'elenco dopo essere stato corretto —
    cioè a un'esenzione di sopravvivere alla propria ragione. Il giorno in cui
    qualcuno ne aggiunge uno, torna a servire.

    Con l'insieme vuoto non rifà nemmeno il giro: 233 import a freddo, quasi
    cinque secondi, per confrontarli con niente.
    """
    if not _KNOWN_COLD_IMPORT_FAILURES:
        return
    failures = _sweep()
    fixed = sorted(_KNOWN_COLD_IMPORT_FAILURES - set(failures))

    assert not fixed, (
        "Questi moduli ora reggono un import a freddo — togli la loro riga da "
        "`_KNOWN_COLD_IMPORT_FAILURES`:\n" + "\n".join(f"  {name}" for name in fixed)
    )


@pytest.mark.parametrize(
    "package,name",
    [
        ("jenny.session", "SessionManager"),
        ("jenny.session", "Session"),
    ],
)
def test_package_attribute_resolves_in_a_cold_interpreter(package: str, name: str) -> None:
    """Le forme `from <package> import <nome>`, che lo sweep non copre.

    Lo sweep importa il *modulo*; un package che ri-esporta può importarsi bene e
    lasciare comunque l'attributo irrisolto, quindi va provato a parte.

    `jenny.cron` stava qui con `CronService` e `CronJob`: il suo `__init__`
    risolveva `CronService` con una `__getattr__` pigra — che non passa dal
    modulo, ed è il caso in cui questa distinzione morde davvero. Quella
    `__getattr__` non c'è più (nessuno importava per quella via, e una
    `__getattr__` di modulo costava il controllo dei nomi di pyright su tutto il
    package, come dice il test qui sotto). Senza re-export non c'è più niente da
    risolvere: `from jenny.cron import CronService` ora fallisce subito e in
    chiaro, che è il modo in cui *non* serve una guardia. Restano i due nomi di
    `jenny.session`, che il package esporta davvero.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"from {package} import {name}; print({name}.__name__)"],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"`from {package} import {name}` a freddo:\n{result.stderr}"
    assert result.stdout.strip() == name


def test_the_session_package_does_not_reach_into_cron() -> None:
    """L'invariante che chiude il ciclo, e il motivo per cui non è una `__getattr__`.

    `jenny/session/__init__.py` resta *eager*: renderlo pigro chiuderebbe il ciclo,
    ma una `__getattr__` di modulo fa diventare ``Any`` ogni attributo sconosciuto
    del package — misurato: `jenny.session.SessionManagr` smette di essere un errore
    — e `jenny/session` sta nel sottoinsieme **bloccante** di pyright. Si
    perderebbe il controllo dei nomi su tutto il package per chiudere un ciclo che
    si chiude altrove in tre righe.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "import jenny.session\n"
            "print(any(m.startswith('jenny.cron') for m in sys.modules))\n",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        "Importare `jenny.session` tira dentro `jenny.cron`: l'import è tornato in "
        "testa a un modulo invece di stare nella funzione, e il ciclo con "
        "`jenny.cron.session_turns` è di nuovo raggiungibile."
    )
