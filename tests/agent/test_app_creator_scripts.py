"""I controlli dello schermo di un'app: stanno nella bozza del tool ``app``.

Erano in ``skills/app-creator/scripts/validate_app.py``, che l'agente avrebbe
dovuto eseguire e non eseguiva nessuno; il 07/10/2026 Jenny se l'e' letto per
capire le regole e ne ha dedotto che reader e summary non si validano. Lo script
e' rimasto come stub (un file tolto resterebbe sul telefono) e i controlli
vivono dove la bozza si valida davvero.
"""

from pathlib import Path

from jenny.agent.tools.app_tool import AppTool, _silent_screen_failure

SCRIPT = (Path(__file__).resolve().parents[2] / "jenny" / "skills" / "app-creator"
          / "scripts" / "validate_app.py")


def test_the_script_is_a_stub_that_points_at_the_draft() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "app action=draft" in text and "def validate_app" not in text


def test_a_form_is_refused_even_with_a_submit_handler() -> None:
    html = ('<form id="f"><input id="t"></form><script>f.addEventListener("submit", '
            'e => { e.preventDefault(); jenny.action("add", {}); });</script>')
    assert "allow-forms" in _silent_screen_failure(html)
    assert "allow-forms" in _silent_screen_failure("<FORM><INPUT></FORM>")


def test_the_word_form_in_prose_is_fine() -> None:
    assert _silent_screen_failure("<p>Fill in the form below to add a task.</p>") is None


def test_modals_and_direct_api_calls_are_refused() -> None:
    assert "allow-modals" in _silent_screen_failure('if (confirm("sure?")) {}')
    assert "allow-modals" in _silent_screen_failure("window.alert(1)")
    assert "jenny.action" in _silent_screen_failure("fetch('/api/apps/todo/actions/add')")
    assert _silent_screen_failure("confirmBox(); const prompt = 1; x.alert(2)") is None


def test_a_good_screen_passes_the_draft_checks() -> None:
    html = ('<button id="b" type="button">Add</button><script>b.addEventListener("click", '
            '() => jenny.action("add_task", {text: "x"}));</script>')
    assert AppTool._check_files({"app/index.html": html}) is None
    refused = AppTool._check_files({"app/index.html": "<form></form>"})
    assert refused and refused.startswith("app/index.html:")
