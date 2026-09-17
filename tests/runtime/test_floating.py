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


class TestFuoriDalTelefono:
    async def test_senza_contesto_android_non_succede_niente(self, monkeypatch):
        """Desktop e CI: nessuna finestra, nessun errore, nessun log d'allarme."""
        monkeypatch.setattr(fl, "get_android_context", lambda: None)
        assert await fl.show_reply("ciao") is False
        assert await fl.apply_floating_config() is False

    async def test_un_bridge_che_solleva_non_fa_cadere_il_turno(self, monkeypatch):
        """È la ragione per cui il canale può restare a un solo tentativo: qui
        dentro non esce mai un'eccezione, quindi non c'è niente da ritentare."""

        def boom():
            raise RuntimeError("Chaquopy non c'è")

        monkeypatch.setattr(fl, "_resolve_bridge_class", boom)
        monkeypatch.setattr(fl, "get_android_context", lambda: object())
        assert await fl.show_reply("ciao") is False


class TestShowReply:
    async def test_il_testo_arriva_al_bridge(self, bridge: _FakeBridge):
        assert await fl.show_reply("le 21:40") is True
        assert bridge.calls == [("showReply", ("le 21:40",))]

    async def test_il_testo_viene_ripulito(self, bridge: _FakeBridge):
        await fl.show_reply("  con spazi \n")
        assert bridge.calls[0][1] == ("con spazi",)

    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    async def test_un_testo_vuoto_non_attraversa_il_confine(
        self, bridge: _FakeBridge, text: str
    ):
        assert await fl.show_reply(text) is False
        assert bridge.calls == []

    async def test_un_fumetto_non_mostrato_torna_falso(self, monkeypatch):
        instance = _FakeBridge(result=False)
        monkeypatch.setattr(fl, "_resolve_bridge_class", lambda: (lambda _ctx: instance))
        monkeypatch.setattr(fl, "get_android_context", lambda: object())
        assert await fl.show_reply("ciao") is False


class TestApplyConfig:
    async def test_spinge_config_e_tempo_di_permanenza(self, bridge: _FakeBridge, monkeypatch):
        from jenny.config.schema import Config

        config = Config()
        config.floating.enabled = True
        config.floating.reply_hold_s = 45
        monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: config)

        assert await fl.apply_floating_config() is True
        assert bridge.calls == [("setEnabled", (True, 45))]

    async def test_va_spinto_anche_da_spento(self, bridge: _FakeBridge, monkeypatch):
        """Non è ridondanza: la finestra vive nel processo del service e
        sopravvive a un riavvio del gateway. Un ``False`` esplicito è l'unica
        cosa che smonta una mascotte rimasta a schermo da un giro precedente."""
        from jenny.config.schema import Config

        monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: Config())

        await fl.apply_floating_config()
        assert bridge.calls == [("setEnabled", (False, 20))]

    async def test_una_config_illeggibile_lascia_stare_la_finestra(
        self, bridge: _FakeBridge, monkeypatch
    ):
        """Meglio una mascotte com'era che una spenta per un file rotto."""

        def boom(*_a, **_k):
            raise OSError("config illeggibile")

        monkeypatch.setattr("jenny.config.loader.load_config", boom)
        assert await fl.apply_floating_config() is False
        assert bridge.calls == []


class TestConfineConKotlin:
    """Il punto in cui un rename rompe solo sul telefono.

    Python raggiunge il bridge per **nome** attraverso Chaquopy, e il
    controller raggiunge gli sprite per **percorso** dentro il workspace. Né il
    compilatore Kotlin né pyright vedono quei due legami.
    """

    def test_il_nome_della_classe_kotlin_esiste(self):
        assert fl._BRIDGE.java_class == "com.flagdizero.jenny.FloatingBridge"
        assert BRIDGE_KT.is_file()
        assert "class FloatingBridge(" in BRIDGE_KT.read_text(encoding="utf-8")

    @pytest.mark.parametrize("method", ["setEnabled", "showReply"])
    def test_i_metodi_chiamati_esistono_in_kotlin(self, method: str):
        source = BRIDGE_KT.read_text(encoding="utf-8")
        assert f"fun {method}(" in source
        assert f'"{method}"' in Path(fl.__file__).read_text(encoding="utf-8")

    def test_gli_sprite_che_il_controller_cerca_esistono(self):
        """Il controller li legge dalla copia estratta della WebUI
        (``workspace/ui/assets/``) invece di duplicarli in ``res/drawable``,
        così la mascotte flottante e quella in chat non possono divergere. Il
        prezzo è che un rename dell'arte la lascia senza faccia, in silenzio:
        questo test è il posto in cui quel prezzo si paga subito.
        """
        source = CONTROLLER.read_text(encoding="utf-8")
        assets = REPO / "jenny/templates/ui/assets"
        names = [
            line.split('"')[1]
            for line in source.splitlines()
            if '"jenny-' in line and "front" in line
        ]
        assert names, "nessuno sprite nominato nel controller: il parsing è da rivedere"
        for name in names:
            assert (assets / f"{name}.webp").is_file(), f"sprite mancante: {name}.webp"

    def test_il_controller_legge_dalla_copia_estratta_della_webui(self):
        source = CONTROLLER.read_text(encoding="utf-8")
        assert '"workspace/ui/assets/$name.webp"' in source
