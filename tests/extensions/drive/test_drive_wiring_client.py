"""Come l'estensione Drive si aggancia: al nativo e al primo passo dell'onboarding.

- Il consenso passa dal comando generico del ponte,
  ``extensionCall("drive", "connectDrive")``: il ponte del core non nomina il
  Drive (``shared/native-bridge.js``), e il Kotlin che risponde sta solo nel
  flavor ``play`` (``DriveExtension.kt``).
- Nel primo passo dell'onboarding la domanda «Ci conosciamo già?» sta sopra il
  ripristino da file, e quel ripristino entra nello stesso blocco.
"""

from __future__ import annotations

import re

from support.js_harness import function, requires_node, run_js
from support.kotlin_source import read_source

from .ui_paths import EXT_UI, ROOT

CLOUD_JS = (EXT_UI / "cloud-backup.js").read_text(encoding="utf-8")
ONBOARDING_JS = (EXT_UI / "onboarding.js").read_text(encoding="utf-8")
KOTLIN = ROOT / "android" / "app" / "src" / "play" / "java" / "com" / "flagdizero" / "jenny"

pytestmark = requires_node


def test_consent_goes_through_the_generic_extension_command() -> None:
    run_js(f"""
      import assert from 'node:assert/strict';
      const sent = [];
      globalThis.window = {{ JennyNative: {{ extensionCall: (...a) => sent.push(a) }} }};
      globalThis.document = {{ visibilityState: 'visible', addEventListener() {{}}, removeEventListener() {{}} }};
      const NATIVE_ANSWER_GRACE_MS = 60000;
      const EXTENSION = 'drive';
      const _waiting = {{}};
      {function(CLOUD_JS, "cloudNativeAvailable")}
      {function(CLOUD_JS, "_native")}
      {function(CLOUD_JS, "connectDrive")}
      assert.equal(cloudNativeAvailable(), true);
      const answer = connectDrive();
      assert.deepEqual(sent, [['drive', 'connectDrive']]);
      _waiting.onDriveConnected('granted');
      assert.equal(await answer, 'granted');

      globalThis.window = {{}};
      assert.equal(cloudNativeAvailable(), false, 'fuori dal guscio promette il consenso');
    """)


def test_the_kotlin_side_answers_the_same_names() -> None:
    drive = read_source(KOTLIN / "DriveExtension.kt")
    assert 'override val name = "drive"' in drive
    assert '"connectDrive" ->' in drive
    assert 'notifyJs("jennyCloud", "onDriveConnected"' in drive
    callbacks = re.search(r"const CALLBACKS = \[([^\]]+)\]", CLOUD_JS).group(1)
    assert "'onDriveConnected'" in callbacks
    assert "listOf(DriveExtension)" in read_source(KOTLIN / "Extensions.kt")


def test_the_welcome_block_takes_the_file_restore_inside() -> None:
    run_js(f"""
      import assert from 'node:assert/strict';
      const i18n = {{ t: (k) => k }};
      const escapeHtml = (s) => String(s);
      let native = true;
      const cloudNativeAvailable = () => native;
      function makeEl(id) {{
        const el = {{ id, className: '', innerHTML: '', children: [], classes: [], attrs: {{ style: 'x' }},
          classList: {{ add: (...c) => el.classes.push(...c) }},
          removeAttribute: (k) => {{ delete el.attrs[k]; }},
          appendChild: (c) => {{ el.children.push(c); return c; }},
          querySelector: (sel) => (sel === '#btn-find-copy' && el.innerHTML.includes('btn-find-copy')
            ? {{ addEventListener() {{}} }} : null),
        }};
        return el;
      }}
      {function(ONBOARDING_JS, "renderWelcome")}
      const slot = makeEl('slot');
      const fileButton = makeEl('file');
      const fileHint = makeEl('hint');
      renderWelcome(slot, {{ fileButton, fileHint }});
      assert.equal(slot.className, 'onboarding-met');
      assert.ok(slot.innerHTML.includes('cloudBackup.restore.question'));
      assert.ok(slot.innerHTML.includes('btn-find-copy'));
      assert.deepEqual(slot.children, [fileButton, fileHint], 'il file resta fuori dal blocco');
      assert.ok(fileButton.classes.includes('onboarding-met-file'));
      assert.equal(fileButton.attrs.style, undefined, 'lo stile in linea del core e rimasto');

      native = false;
      const bare = makeEl('slot');
      renderWelcome(bare, {{}});
      assert.ok(!bare.innerHTML.includes('btn-find-copy'), 'fuori dal guscio offre la copia su Drive');
    """)
