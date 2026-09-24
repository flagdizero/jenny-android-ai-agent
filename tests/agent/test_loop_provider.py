"""Test per lo switch runtime di provider/preset in ``jenny.agent.loop_provider``.

``ProviderPresetMixin`` è mixato in ``AgentLoop``; qui viene esercitato
attraverso un ``AgentLoop`` reale costruito con ``make_loop`` (vedi
``tests/agent/conftest.py``). ``tests/agent/test_model_preset.py`` copre già i
percorsi felici di ``set_model_preset``/``model_preset``: questo file si
concentra sul contratto interno (``_apply_provider_switch``,
``_sync_subagent_runtime_limits``, ``_preset_field``) e sui casi limite dei
preset (oggetti di config reali, preset parziali, provider senza supporto
``generation``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from support.agent import make_loop, make_provider

from jenny.agent.loop_provider import ProviderPresetMixin
from jenny.config.schema import ModelPresetConfig


class TestApplyProviderSwitch:
    """``_apply_provider_switch`` propaga il nuovo provider a tutte le dipendenze."""

    def test_updates_model_and_all_dependents(self, tmp_path):
        loop = make_loop(tmp_path, model="old-model", context_window_tokens=1000, patch_deps=True)
        new_provider = make_provider(default_model="new-model")

        loop._apply_provider_switch(new_provider, "new-model", 50_000)

        assert loop.provider is new_provider
        assert loop.model == "new-model"
        assert loop.context_window_tokens == 50_000
        assert loop.runner.provider is new_provider
        # SubagentManager è mockato (patch_deps=True): verifichiamo la chiamata.
        loop.subagents.set_provider.assert_called_once_with(new_provider, "new-model")
        # Consolidator è reale: verifichiamo lo stato aggiornato direttamente.
        assert loop.consolidator.provider is new_provider
        assert loop.consolidator.model == "new-model"
        assert loop.consolidator.context_window_tokens == 50_000

    def test_publish_update_true_emits_runtime_model_changed(self, tmp_path):
        loop = make_loop(tmp_path, patch_deps=True)
        loop.runtime_event_publisher = MagicMock()
        new_provider = make_provider(default_model="new-model")
        new_provider.provider_name = "prov-x"

        loop._apply_provider_switch(new_provider, "new-model", 1000, publish_update=True)

        loop.runtime_event_publisher.runtime_model_changed.assert_called_once_with(
            "new-model", loop._active_preset, provider="prov-x"
        )

    def test_publish_update_false_suppresses_event(self, tmp_path):
        loop = make_loop(tmp_path, patch_deps=True)
        loop.runtime_event_publisher = MagicMock()
        new_provider = make_provider(default_model="new-model")

        loop._apply_provider_switch(new_provider, "new-model", 1000, publish_update=False)

        loop.runtime_event_publisher.runtime_model_changed.assert_not_called()

    def test_model_preset_name_overrides_active_preset_in_event(self, tmp_path):
        loop = make_loop(tmp_path, patch_deps=True)
        loop.runtime_event_publisher = MagicMock()
        new_provider = make_provider(default_model="new-model")
        new_provider.provider_name = None

        loop._apply_provider_switch(
            new_provider, "new-model", 1000, model_preset_name="explicit-preset"
        )

        loop.runtime_event_publisher.runtime_model_changed.assert_called_once_with(
            "new-model", "explicit-preset", provider=None
        )


class TestSyncSubagentRuntimeLimits:
    """``_sync_subagent_runtime_limits`` allinea i limiti mutabili del loop."""

    def test_propagates_max_iterations_to_subagents(self, tmp_path):
        loop = make_loop(tmp_path, patch_deps=True)
        loop.max_iterations = 7

        loop._sync_subagent_runtime_limits()

        assert loop.subagents.max_iterations == 7


class TestPresetField:
    """Metodo statico: legge sia dict che oggetti con attributi (config reale)."""

    def test_reads_from_dict(self):
        assert ProviderPresetMixin._preset_field({"model": "m1"}, "model") == "m1"

    def test_missing_key_in_dict_returns_none(self):
        assert ProviderPresetMixin._preset_field({"model": "m1"}, "temperature") is None

    def test_reads_from_object_attribute(self):
        preset = SimpleNamespace(model="m2", temperature=0.5)
        assert ProviderPresetMixin._preset_field(preset, "model") == "m2"
        assert ProviderPresetMixin._preset_field(preset, "temperature") == 0.5

    def test_missing_attribute_on_object_returns_none(self):
        preset = SimpleNamespace(model="m2")
        assert ProviderPresetMixin._preset_field(preset, "reasoning_effort") is None


class TestApplyModelPresetRealConfigObject:
    """I preset in produzione sono ``ModelPresetConfig`` (pydantic-compat), non dict."""

    async def test_config_object_preset_applies_all_fields(self, tmp_path):
        preset = ModelPresetConfig(
            model="cfg-model",
            context_window_tokens=50_000,
            max_tokens=1500,
            temperature=0.55,
            reasoning_effort="low",
        )
        loop = make_loop(
            tmp_path,
            model_presets={"cfgpreset": preset},
            patch_deps=True,
        )

        await loop.set_model_preset("cfgpreset")

        assert loop.model == "cfg-model"
        assert loop.context_window_tokens == 50_000
        assert loop.provider.generation.max_tokens == 1500
        assert loop.provider.generation.temperature == 0.55
        assert loop.provider.generation.reasoning_effort == "low"
        assert loop.model_preset == "cfgpreset"


class TestApplyModelPresetPartial:
    """Un preset parziale non deve azzerare i campi non specificati."""

    async def test_partial_preset_keeps_current_model_and_window(self, tmp_path):
        loop = make_loop(
            tmp_path,
            model="orig-model",
            context_window_tokens=12_345,
            model_presets={"partial": {"reasoning_effort": "high"}},
            patch_deps=True,
        )
        original_max_tokens = loop.provider.generation.max_tokens
        original_temperature = loop.provider.generation.temperature

        await loop.set_model_preset("partial")

        assert loop.model == "orig-model"
        assert loop.context_window_tokens == 12_345
        assert loop.provider.generation.reasoning_effort == "high"
        # max_tokens/temperature non menzionati nel preset: restano invariati.
        assert loop.provider.generation.max_tokens == original_max_tokens
        assert loop.provider.generation.temperature == original_temperature


class TestApplyModelPresetWithoutGenerationSupport:
    """Provider senza ``generation`` (getattr fallback a None) in ``loop_provider``.

    ``_apply_model_preset`` protegge con ``getattr(self.provider, "generation",
    None)`` prima di toccare max_tokens/temperature/reasoning_effort, ma la
    stessa chiamata propaga sempre a ``Consolidator.set_provider``, che accede
    a ``provider.generation.max_tokens`` senza guardia (jenny/agent/
    consolidator.py). Risultato: il guard di loop_provider.py non basta a
    evitare il crash end-to-end se un provider reale arrivasse senza
    ``generation`` — comportamento sospetto, non corretto qui (fuori scope:
    tocca consolidator.py, non loop_provider.py)."""

    async def test_model_and_window_still_switch_without_generation(self, tmp_path):
        loop = make_loop(
            tmp_path,
            model_presets={
                "x": {
                    "model": "new-m",
                    "context_window_tokens": 999,
                    "max_tokens": 111,
                    "temperature": 0.9,
                }
            },
            patch_deps=True,
        )
        # Simula un provider che non espone `generation` a runtime.
        loop.provider.generation = None

        # Il guard locale in loop_provider.py non basta: Consolidator.set_provider
        # accede comunque a provider.generation.max_tokens senza controllo.
        with pytest.raises(AttributeError):
            await loop.set_model_preset("x")


class TestApplyModelPresetUnknown:
    """Messaggio d'errore utile: elenca i preset disponibili in ordine alfabetico."""

    async def test_unknown_preset_lists_available_sorted(self, tmp_path):
        loop = make_loop(
            tmp_path,
            model_presets={"zeta": {"model": "z"}, "alpha": {"model": "a"}},
            patch_deps=True,
        )
        try:
            await loop.set_model_preset("missing")
        except KeyError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected KeyError")
        assert "alpha, zeta" in message

    async def test_unknown_preset_with_no_presets_configured_says_none(self, tmp_path):
        loop = make_loop(tmp_path, patch_deps=True)
        try:
            await loop.set_model_preset("missing")
        except KeyError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected KeyError")
        assert "(none configured)" in message
