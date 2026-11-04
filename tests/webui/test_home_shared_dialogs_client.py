"""I dialoghi condivisi sopra la casa: Indietro e Home li chiudono per primi.

«Elimina quaderno?», «Nuovo nome per …» e il dettaglio sono i `<dialog>` di
``shared/dialog.js`` (``oc-confirm``, ``oc-prompt``, ``oc-detail``): stanno
nel top layer, sopra tutto. La casa chiudeva i suoi fogli e non questi, quindi
Indietro portava via la pagina **sotto** la domanda e la domanda restava
aperta sopra un'altra pagina — un «Conferma» che cancellava un quaderno da
una stanza in cui non eri piu'. E la chat che ci stava
sotto contava come vista, cancellando avvisi mai letti.

Come l'officina (``shared/dialog.js``, ``dismissTopDialog``): il dialogo si
congeda con la semantica di Esc — l'evento ``cancel`` — cosi' chi aspettava la
risposta la riceve, ed e' un no.
"""

from __future__ import annotations

import json

from support.home_dom import UI, requires_jsdom, run_home

pytestmark = requires_jsdom

# Il percorso della UI, gia' quotato per il modulo ES del banco.
UI_JSON = json.dumps(str(UI))

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, $, sent } from './boot.mjs';
const app = await boot();
const deletes = () => sent.filter((f) => f.type === 'rpc' && /delete/i.test(f.method || ''));
"""


def test_back_closes_the_delete_question_before_the_page_under_it() -> None:
    run_home(_HEAD + """
app.homePages.goToId('notebooks');
await tick(20);
const asked = app.deleteNotebook('orto');
await tick(20);
assert.equal($('oc-confirm-dialog').open, true, 'la domanda non si e\\u2019 aperta');

app.handleHardwareBack();
await tick(20);
assert.equal($('oc-confirm-dialog').open, false, 'Indietro ha lasciato la domanda aperta');
assert.equal(app._entry.id, 'notebooks', 'una pressione ha tolto anche la pagina');
assert.equal(await asked, false, 'chiudere la domanda non e\\u2019 un no');
assert.deepEqual(deletes(), []);
""")


def test_home_closes_the_rename_prompt_and_goes_home() -> None:
    run_home(_HEAD + """
app.homePages.goToId('notebooks');
await tick(20);
const asked = app.renameNotebook('orto');
await tick(20);
assert.equal($('oc-prompt-dialog').open, true);
app.goHome();
await tick(20);
assert.equal($('oc-prompt-dialog').open, false, 'Home ha lasciato la domanda aperta');
assert.equal(app._entry.kind, 'chat');
assert.equal(await asked, false);
""")


def test_the_chat_under_a_shared_dialog_is_not_on_screen() -> None:
    run_home(_HEAD + """
assert.equal(app.isChatOnScreen(), true);
const asked = app.renameNotebook('orto');
await tick(20);
assert.equal(app.isChatOnScreen(), false, 'una domanda aperta copre la chat');
app.handleHardwareBack();
await asked;
assert.equal(app.isChatOnScreen(), true);
""")


def test_back_closes_the_backup_passphrase_before_the_room_under_it() -> None:
    """Le due finestre di ``shared/backup-flow.js`` nascono al volo, non stanno
    nel DOM: senza un id nella lista, Indietro non le vedeva e portava via la
    stanza Backup lasciando la passphrase aperta sopra Impostazioni."""
    run_home(_HEAD + f"""
const {{ promptPassphrase }} = await import({UI_JSON} + '/assets/shared/backup-flow.js');
app._setView('backup');
await tick(20);
assert.equal(app.view, 'backup', 'la stanza Backup non si e\\u2019 aperta');
const asked = promptPassphrase({{ confirm: true }});
await tick(20);
assert.equal($('oc-backup-passphrase-dialog').open, true, 'la passphrase non si e\\u2019 aperta');
assert.equal(app.hasOverlayAbove(), true, 'la passphrase non conta come strato sopra');

app.handleHardwareBack();
await tick(20);
assert.equal($('oc-backup-passphrase-dialog'), null, 'Indietro ha lasciato la passphrase aperta');
assert.equal(app.view, 'backup', 'una pressione ha tolto anche la stanza');
assert.equal(await asked, null, 'chiudere la domanda non e\\u2019 un no');
""")


def test_back_is_consumed_by_the_restart_dialog_without_closing_it() -> None:
    """Il riavvio dopo un ripristino rifiuta ``cancel``: la pressione e'
    consumata, la finestra resta, e la stanza sotto non si muove."""
    run_home(_HEAD + f"""
const {{ showRestartDialog }} = await import({UI_JSON} + '/assets/shared/backup-flow.js');
app._setView('backup');
await tick(20);
showRestartDialog();
await tick(20);
assert.equal($('oc-backup-restart-dialog').open, true);

app.handleHardwareBack();
await tick(20);
assert.equal($('oc-backup-restart-dialog').open, true, 'il riavvio non deve chiudersi');
assert.equal(app.view, 'backup', 'Indietro e\\u2019 passato sotto il riavvio');
""")


def test_back_closes_the_import_passphrase_too() -> None:
    """Il ripristino chiede la passphrase una volta sola (``confirm`` spento):
    e' lo stesso dialog dell'export, ma un altro ramo del markup, e non era
    coperto. Il 29/09/2026 l'utente lo ricordava come ancora rotto."""
    run_home(_HEAD + f"""
const {{ promptPassphrase }} = await import({UI_JSON} + '/assets/shared/backup-flow.js');
app._setView('backup');
await tick(20);
const asked = promptPassphrase();
await tick(20);
assert.equal($('oc-backup-passphrase-dialog').open, true, 'la passphrase non si e\\u2019 aperta');

app.handleHardwareBack();
await tick(20);
assert.equal($('oc-backup-passphrase-dialog'), null, 'Indietro ha lasciato la passphrase aperta');
assert.equal(app.view, 'backup', 'una pressione ha tolto anche la stanza');
assert.equal(await asked, null);
""")
