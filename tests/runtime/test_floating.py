"""Test per ``jenny/runtime/floating.py`` (la volontà, non la finestra).

Il bridge Chaquopy non esiste fuori dal telefono: si sostituisce il seam
``_resolve_bridge_class`` — lo stesso che sostituiscono i test del notifier — e
si guarda **cosa** viene chiamato e **con che cosa**.

Qui non si prova niente della finestra: dove sta, quanto è grande e se la
tastiera si alza sono domande sul Kotlin, e questa casa non ha test Kotlin. Si
prova il confine: che la config diventi una chiamata, che un bridge rotto non
faccia cadere un turno, e che i nomi degli sprite che il controller cerca
esistano davvero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.runtime import floating as fl

REPO = Path(__file__).resolve().parents[2]
CONTROLLER = REPO / "android/app/src/main/java/com/flagdizero/jenny/FloatingOverlayController.kt"
BRIDGE_KT = REPO / "android/app/src/main/java/com/flagdizero/jenny/FloatingBridge.kt"


class _FakeBridge:
    """Registra le chiamate e risponde ``True``, come farebbe Kotlin."""

    def __init__(self, *_args, result: bool = True) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.result = result

    def setEnabled(self, on, hold):  # noqa: N802 — è il nome del metodo Kotlin
        self.calls.append(("setEnabled", (on, hold)))
        return self.result

    def showReply(self, text):  # noqa: N802
        self.calls.append(("showReply", (text,)))
        return self.result

    def isActive(self):  # noqa: N802
        self.calls.append(("isActive", ()))
        return self.result


@pytest.fixture(autouse=True)
def _clean_state():
    fl.reset_floating_state()
    yield
    fl.reset_floating_state()


@pytest.fixture
def bridge(monkeypatch) -> _FakeBridge:
    """Monta un bridge finto e un contesto Android finto."""
    instance = _FakeBridge()
    monkeypatch.setattr(fl, "_resolve_bridge_class", lambda: (lambda _ctx: instance))
    monkeypatch.setattr(fl, "get_android_context", lambda: object())
    return instance


class TestOffThePhone:
    async def test_without_android_context_nothing_happens(self, monkeypatch):
        """Desktop e CI: nessuna finestra, nessun errore, nessun log d'allarme."""
        monkeypatch.setattr(fl, "get_android_context", lambda: None)
        assert await fl.show_reply("ciao") is False
        assert await fl.apply_floating_config() is False

    async def test_a_bridge_that_raises_does_not_drop_the_turn(self, monkeypatch):
        """È la ragione per cui il canale può restare a un solo tentativo: qui
        dentro non esce mai un'eccezione, quindi non c'è niente da ritentare."""

        def boom():
            raise RuntimeError("Chaquopy non c'è")

        monkeypatch.setattr(fl, "_resolve_bridge_class", boom)
        monkeypatch.setattr(fl, "get_android_context", lambda: object())
        assert await fl.show_reply("ciao") is False


class TestShowReply:
    async def test_the_text_reaches_the_bridge(self, bridge: _FakeBridge):
        assert await fl.show_reply("le 21:40") is True
        assert bridge.calls == [("showReply", ("le 21:40",))]

    async def test_the_text_is_cleaned(self, bridge: _FakeBridge):
        await fl.show_reply("  con spazi \n")
        assert bridge.calls[0][1] == ("con spazi",)

    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    async def test_an_empty_text_does_not_cross_the_boundary(
        self, bridge: _FakeBridge, text: str
    ):
        assert await fl.show_reply(text) is False
        assert bridge.calls == []

    async def test_an_unshown_speech_bubble_returns_false(self, monkeypatch):
        instance = _FakeBridge(result=False)
        monkeypatch.setattr(fl, "_resolve_bridge_class", lambda: (lambda _ctx: instance))
        monkeypatch.setattr(fl, "get_android_context", lambda: object())
        assert await fl.show_reply("ciao") is False


class TestApplyConfig:
    async def test_pushes_config_and_dwell_time(self, bridge: _FakeBridge, monkeypatch):
        from jenny.config.schema import Config

        config = Config()
        config.floating.enabled = True
        config.floating.reply_hold_s = 45
        monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: config)

        assert await fl.apply_floating_config() is True
        assert bridge.calls == [("setEnabled", (True, 45))]

    async def test_must_be_pushed_even_when_off(self, bridge: _FakeBridge, monkeypatch):
        """Non è ridondanza: la finestra vive nel processo del service e
        sopravvive a un riavvio del gateway. Un ``False`` esplicito è l'unica
        cosa che smonta una mascotte rimasta a schermo da un giro precedente."""
        from jenny.config.schema import Config

        monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: Config())

        await fl.apply_floating_config()
        assert bridge.calls == [("setEnabled", (False, 20))]

    async def test_an_unreadable_config_leaves_the_window_alone(
        self, bridge: _FakeBridge, monkeypatch
    ):
        """Meglio una mascotte com'era che una spenta per un file rotto."""

        def boom(*_a, **_k):
            raise OSError("config illeggibile")

        monkeypatch.setattr("jenny.config.loader.load_config", boom)
        assert await fl.apply_floating_config() is False
        assert bridge.calls == []


class TestIsActive:
    async def test_asks_the_window(self, bridge: _FakeBridge):
        assert await fl.floating_active() is True
        assert bridge.calls == [("isActive", ())]

    async def test_without_android_context_is_false(self, monkeypatch):
        monkeypatch.setattr(fl, "get_android_context", lambda: None)
        assert await fl.floating_active() is False


class TestBoundaryWithKotlin:
    """Il punto in cui un rename rompe solo sul telefono.

    Python raggiunge il bridge per **nome** attraverso Chaquopy, e il
    controller raggiunge gli sprite per **percorso** dentro il workspace. Né il
    compilatore Kotlin né pyright vedono quei due legami.
    """

    def test_the_kotlin_class_name_exists(self):
        assert fl._BRIDGE.java_class == "com.flagdizero.jenny.FloatingBridge"
        assert BRIDGE_KT.is_file()
        assert "class FloatingBridge(" in BRIDGE_KT.read_text(encoding="utf-8")

    @pytest.mark.parametrize("method", ["setEnabled", "showReply", "isActive"])
    def test_the_called_methods_exist_in_kotlin(self, method: str):
        source = BRIDGE_KT.read_text(encoding="utf-8")
        assert f"fun {method}(" in source
        assert f'"{method}"' in Path(fl.__file__).read_text(encoding="utf-8")

    def test_the_sprites_the_controller_looks_for_exist(self):
        """Il controller li legge dalla copia estratta della WebUI
        (``workspace/ui/assets/``) invece di duplicarli in ``res/drawable``,
        così la mascotte flottante e quella in chat non possono divergere. Il
        prezzo è che un rename dell'arte la lascia senza faccia, in silenzio:
        questo test è il posto in cui quel prezzo si paga subito.
        """
        import re

        source = CONTROLLER.read_text(encoding="utf-8")
        assets = REPO / "jenny/templates/ui/assets"
        names = set(re.findall(r'"(jenny-[a-z0-9-]+)"', source))
        assert names, "nessuno sprite nominato nel controller: il parsing è da rivedere"
        # Le pose del bordo e del volo, non solo quelle frontali: una mascotte
        # senza `jenny-hang` non penzola, e non lo dice a nessuno.
        assert "jenny-side" in names
        assert {"jenny-hang", "jenny-fall", "jenny-ground", "jenny-walk1", "jenny-walk2"} <= names
        for name in sorted(names):
            assert (assets / f"{name}.webp").is_file(), f"sprite mancante: {name}.webp"

    def test_the_controller_reads_from_the_extracted_webui_copy(self):
        source = CONTROLLER.read_text(encoding="utf-8")
        assert '"workspace/ui/assets/$name.webp"' in source


class TestThePhysicsDoesNotDiverge:
    """Le costanti del volo vivono in due posti, e devono restare identiche.

    `FloatingFlight.kt` porta in Kotlin la macchina che il JS fa girare per la
    mascotte in chat. È una duplicazione deliberata — v.
    `roadmap/02-mascotte-flottante-piano.md`, S1 — e il suo prezzo è esattamente
    questo: qualcuno ritocca una costante da una parte, e la mascotte comincia a
    oscillare in due modi diversi a seconda di dove la si guarda.

    **Il lato JS si è spostato** il 18/09/2026: la fisica stava in
    `mobile-jenny.js`, ora è in `shared/mascot-drag.js` perché la usano in due
    (la casa e l'officina, v. `.agent/casa-plan.md`). I consumatori di queste
    costanti sono quindi tre, e questo test è l'unico posto in cui due di loro
    si guardano in faccia.

    Nessun elenco scritto a mano: i nomi si leggono dal sorgente Kotlin, quindi
    una costante nuova entra da sola nel confronto.
    """

    FLIGHT_KT = REPO / "android/app/src/main/java/com/flagdizero/jenny/FloatingFlight.kt"
    COMPANION_JS = REPO / "jenny/templates/ui/assets/shared/mascot-drag.js"

    @staticmethod
    def _js_numbers(source: str) -> dict[str, float]:
        import re

        out: dict[str, float] = {}
        # `export const` oltre a `const`: nel modulo condiviso alcune costanti
        # sono esportate (le usano i gusci), e senza questo il confronto le
        # perderebbe in silenzio — che è il difetto contro cui esiste la
        # guardia sul numero minimo di confronti.
        for name, raw in re.findall(
            r"^(?:export )?const ([A-Z][A-Z0-9_]*) = ([-0-9.]+)", source, re.M
        ):
            out[name] = float(raw)
        # ``MAX_TILT`` è scritto in radianti come espressione: si confronta il
        # valore in gradi, che è la forma in cui il Kotlin lo tiene.
        if re.search(r"^(?:export )?const MAX_TILT = \(78 \* Math\.PI\) / 180", source, re.M):
            out["MAX_TILT_DEG"] = 78.0
        return out

    @staticmethod
    def _kt_numbers(source: str) -> dict[str, float]:
        import re

        out: dict[str, float] = {}
        for name, raw in re.findall(r"const val ([A-Z][A-Z0-9_]*) = ([-0-9_.]+)f?L?", source):
            out[name] = float(raw.replace("_", ""))
        return out

    def test_every_kotlin_constant_has_the_same_in_js(self):
        kt = self._kt_numbers(self.FLIGHT_KT.read_text(encoding="utf-8"))
        js = self._js_numbers(self.COMPANION_JS.read_text(encoding="utf-8"))

        # I nomi che in Kotlin portano il suffisso dell'unità: là sono px CSS,
        # qui px del dispositivo, e il valore di partenza è lo stesso.
        aliases = {
            "MAX_SPEED_CSS": "MAX_SPEED",
            "FALL_G_CSS": "FALL_G",
            "WALK_SPEED_CSS": "WALK_SPEED",
            "DIR_MIN_CSS": "DIR_MIN",
        }
        compared = 0
        for name, value in kt.items():
            js_name = aliases.get(name, name)
            if js_name not in js:
                continue
            compared += 1
            assert value == pytest.approx(js[js_name]), (
                f"{name} è {value} in FloatingFlight.kt e {js[js_name]} "
                f"({js_name}) in shared/mascot-drag.js: la mascotte flottante e quella "
                f"in chat si muoverebbero in due modi diversi."
            )
        assert compared >= 12, (
            f"solo {compared} costanti confrontate: il parsing di uno dei due "
            "sorgenti è da rivedere, e un test che non confronta niente passa sempre"
        )

    def test_the_pivot_is_the_same(self):
        """La punta della manica alzata di `jenny-hang`. Sbagliarlo non rompe
        niente: la fa solo ruotare attorno al punto sbagliato."""
        kt = self.FLIGHT_KT.read_text(encoding="utf-8")
        js = self.COMPANION_JS.read_text(encoding="utf-8")
        assert "const val PIVOT_X = 0.5083f" in kt
        assert "const val PIVOT_Y = 0.4333f" in kt
        assert "export const PIVOT_X = 0.5083;" in js
        assert "export const PIVOT_Y = 0.4333;" in js
