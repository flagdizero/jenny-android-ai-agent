"""La casa aperta da un altro dispositivo: niente comandi che agiscono sul telefono.

Nella casa vera (jsdom). Il bootstrap dice ``remote: true`` quando la pagina
arriva dall'ascoltatore Tailscale; da li' la pagina App mostra solo le mini-app
(le app Android non si chiedono nemmeno), l'aggiornamento si vede ma «si
installa dal telefono», e una mini-app con vista esterna spiega perche' da qui
non si apre. Senza ``remote`` tutto resta com'era. Il lato server e' in
``test_phone_only_remote.py``.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, routes, calls, toasts, locales, $ } from './boot.mjs';
routes['/api/webui/apps'] = { apps: [{ slug: 'todo', name: 'Todo', icon: null }] };
routes['/api/webui/android-apps'] = { apps: [{ packageName: 'com.example', label: 'Example' }] };
const androidCalls = () => calls.filter((c) => c.startsWith('/api/webui/android-apps')).length;
const U = locales.en.settings.update;
"""


def test_from_remote_the_app_page_never_asks_for_android_apps() -> None:
    run_home(_HEAD + """
routes['/webui/bootstrap'] = { ok: true, remote: true };
const app = await boot();
await tick(30);
const source = app.appsSource();
await source.loadAndroidApps();
await tick(10);
assert.equal(androidCalls(), 0, 'da fuori ha chiesto le app del telefono');
assert.deepEqual(source.androidApps, []);
assert.equal(source._failed.android, false, 'una lista vuota voluta non e\\' un guasto');
""")


def test_on_the_phone_android_apps_are_still_there() -> None:
    run_home(_HEAD + """
const app = await boot();
await tick(30);
const source = app.appsSource();
await source.loadAndroidApps();
assert.ok(androidCalls() >= 1);
assert.deepEqual(source.androidApps.map((a) => a.packageName), ['com.example']);
""")


def test_from_remote_an_update_is_shown_but_installs_on_the_phone() -> None:
    run_home(_HEAD + """
routes['/webui/bootstrap'] = { ok: true, remote: true };
const app = await boot();
await tick(30);
app.updatesRoom.setVersion({ update_available: true, latest: '9.9.9', critical: true });
await tick(10);
const btn = $('home-update-install');
assert.equal(btn.hidden, false);
assert.equal(btn.disabled, true);
assert.equal(btn.textContent, U.installOnPhone);
assert.equal(btn.classList.contains('is-critical'), false);
""")


def test_on_the_phone_the_update_installs_as_before() -> None:
    run_home(_HEAD + """
const app = await boot();
await tick(30);
app.updatesRoom.setVersion({ update_available: true, latest: '9.9.9' });
await tick(10);
const btn = $('home-update-install');
assert.equal(btn.disabled, false);
assert.equal(btn.textContent, U.install);
""")


def test_from_remote_an_external_view_says_why_it_does_not_open() -> None:
    run_home(_HEAD + """
routes['/webui/bootstrap'] = { ok: true, remote: true };
const app = await boot();
await tick(30);
await app.appsActions()._openExternalView('site', { name: 'Site', view_kind: 'external' });
await tick(10);
const expected = locales.en.apps.viewPhoneOnly.replace('{name}', 'Site');
assert.ok(toasts().includes(expected), JSON.stringify(toasts()));
assert.equal(calls.filter((c) => c.includes('/view')).length, 0, 'ha chiesto il proxy da fuori');
assert.equal(document.querySelector('iframe[src*="127.0.0.1"]'), null);
""")
