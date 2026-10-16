"""Le estensioni: pezzi di Jenny che solo alcune build impacchettano.

Il codice di un'estensione non sta sotto ``jenny/`` ma nel pacchetto fratello
``jenny_extensions/``, e Gradle lo mette nell'APK solo nei flavor che lo vogliono
(``android/app/build.gradle.kts``). Una build senza estensioni non ne contiene
**nemmeno il codice**: non è una funzione spenta, è una funzione che non c'è.

Questo modulo è l'unico punto del core che conosce quel pacchetto, e lo conosce
solo per nome. Il core non sa cosa fa un'estensione: le offre dei ganci generici
e lei li riempie.

- **Job di sistema** (:class:`SystemJob`): un giro periodico registrato dal
  container e instradato dal ``CronDispatcher`` per id, prima del controllo
  sull'agente (un'estensione può non aver bisogno del modello).
- **Route** (``routers``): oggetti con ``dispatch(request, path)``, accodati alle
  route della WebUI dietro lo stesso controllo del token.
- **WebUI** (:class:`ExtensionUi`): file serviti sotto ``assets/ext/<nome>/``,
  con un modulo d'ingresso che le shell caricano con ``import()`` e montano
  passandogli i servizi del core, più le traduzioni da fondere con le loro.

Un'estensione importa il core quanto vuole; il core non importa mai
un'estensione se non da qui.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# Il pacchetto che contiene le estensioni. Fuori da ``jenny/`` di proposito:
# vedi la docstring del modulo.
EXTENSIONS_PACKAGE = "jenny_extensions"


@dataclass(frozen=True)
class ExtensionHost:
    """Quel che il core mette a disposizione di un giro periodico."""

    # ``SnapshotService`` o ``None`` (snapshot spenti o non partiti).
    snapshot: Any
    # L'agente di adesso, o ``None`` prima dell'onboarding / senza provider.
    get_agent: Callable[[], Any]


@dataclass(frozen=True)
class SystemJob:
    """Un lavoratore periodico portato da un'estensione."""

    # Id del job nello store del cron: unico fra core ed estensioni.
    id: str
    every_ms: int
    # La riga che presenta il job all'utente e al modello (``cron/purposes.py``).
    purpose: str
    run: Callable[[ExtensionHost], Awaitable[str | None]]


@dataclass(frozen=True)
class ExtensionUi:
    """I file WebUI di un'estensione, letti dal suo pacchetto."""

    # Il pacchetto Python che contiene i file (``importlib.resources``).
    package: str
    # I file, relativi a ``package``: è il manifest, come ``_UI_MANIFEST``.
    files: tuple[str, ...]
    # Il modulo che le shell importano e su cui chiamano ``mount(ctx)``.
    entry: str
    # I fogli di stile da agganciare alla pagina.
    css: tuple[str, ...] = ()
    # ``{locale: file}``: traduzioni fuse in quelle del core prima del primo render.
    i18n: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Extension:
    name: str
    system_jobs: tuple[SystemJob, ...] = ()
    # ``make_routers(snapshot_service, log)`` → oggetti con ``dispatch``.
    make_routers: Callable[[Any, Any], list[Any]] | None = None
    ui: ExtensionUi | None = None

    def ui_url(self, rel: str) -> str:
        """Il percorso, sotto ``ui/``, a cui un suo file viene servito."""
        return f"{EXT_UI_DIR}/{self.name}/{rel}"


# Dove stanno i file WebUI delle estensioni, sotto ``workspace/ui/``.
EXT_UI_DIR = "assets/ext"

_loaded: tuple[Extension, ...] | None = None


def load() -> tuple[Extension, ...]:
    """Le estensioni impacchettate in questa build (cache per processo).

    Il pacchetto che manca è il caso normale di una build senza estensioni, e
    vale «nessuna». Un'estensione che c'è ma non si importa è un difetto: si
    logga e si va avanti senza, invece di fermare il gateway.
    """
    global _loaded
    if _loaded is None:
        _loaded = _import_extensions()
    return _loaded


def _import_extensions() -> tuple[Extension, ...]:
    import importlib

    try:
        package = importlib.import_module(EXTENSIONS_PACKAGE)
    except ModuleNotFoundError as exc:
        if exc.name != EXTENSIONS_PACKAGE:
            logger.opt(exception=True).error("Extensions failed to import")
        return ()
    except Exception:  # noqa: BLE001 — un'estensione rotta non ferma Jenny
        logger.opt(exception=True).error("Extensions failed to import")
        return ()
    found: tuple[Extension, ...] = tuple(getattr(package, "EXTENSIONS", None) or ())
    if found:
        logger.info("Extensions: {}", ", ".join(ext.name for ext in found))
    return found


def reset_for_tests(extensions: tuple[Extension, ...] | None = None) -> None:
    """Sostituisce la cache: ``()`` simula una build senza estensioni."""
    global _loaded
    _loaded = extensions


def system_jobs() -> dict[str, SystemJob]:
    return {job.id: job for ext in load() for job in ext.system_jobs}


def ui_sources() -> dict[str, tuple[str, str]]:
    """``percorso sotto ui/`` → ``(pacchetto, file)`` per ogni file delle estensioni."""
    sources: dict[str, tuple[str, str]] = {}
    for ext in load():
        if ext.ui is None:
            continue
        for rel in ext.ui.files:
            sources[ext.ui_url(rel)] = (ext.ui.package, rel)
    return sources


def bootstrap_info() -> list[dict[str, Any]]:
    """Cosa le shell devono caricare, nella risposta di ``/webui/bootstrap``."""
    info: list[dict[str, Any]] = []
    for ext in load():
        if ext.ui is None:
            continue
        info.append({
            "name": ext.name,
            "entry": "/html-mobile/" + ext.ui_url(ext.ui.entry),
            "css": ["/html-mobile/" + ext.ui_url(css) for css in ext.ui.css],
            "i18n": {
                locale: "/html-mobile/" + ext.ui_url(rel) for locale, rel in ext.ui.i18n.items()
            },
        })
    return info
