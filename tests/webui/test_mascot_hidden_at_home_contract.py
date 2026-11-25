"""Jenny si nasconde in casa, e la chat non resta senza porta.

Dal 05/10/2026 al 25/11/2026 «Mostra la mascotte» non c'era: Jenny era la porta
della chat (il tocco la porta su sopra la pagina), e una porta che si spegne
lasciava la chat senza strada. E' tornata con un'altra porta accanto: con lei
nascosta, nelle stanze compare la chiave della chat in testa, e nel velo la
freccia che lo chiude c'e' sempre. Vale solo per la casa: le regole stanno in
``home-style.css``, che l'officina non carica.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, UI


def test_the_old_visibility_keys_stay_dead() -> None:
    """La preferenza ha una chiave nuova: un «0» rimasto da prima del 05/10/2026
    non deve nasconderla di sorpresa."""
    src = (ASSETS / "shared" / "mascot.js").read_text(encoding="utf-8")
    dead = re.search(r"const DEAD_KEYS = \[(.*?)\];", src, re.S).group(1)
    for key in ("'jenny-mascot-visible'", "'jenny-mascotte-visible'"):
        assert key in dead, f"{key} non e' fra le chiavi morte"
    assert "const HIDDEN_KEY = 'jenny-mascot-hidden';" in src
    assert "export function mascotHidden" in src
    assert "export function setMascotHidden" in src


def test_only_the_house_hides_her() -> None:
    """L'attributo lo legge solo il foglio della casa; l'officina la tiene."""
    home = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert ":root[data-jenny-hidden] .home-shell > .jenny-duo { display: none; }" in home
    for name in ("mobile-style.css", "mobile-jenny.js"):
        text = (ASSETS / name).read_text(encoding="utf-8")
        for word in ("data-jenny-hidden", "mascotHidden", "hidden-mode"):
            assert word not in text, f"{name} parla di {word}: nasconderebbe Jenny in officina"


def test_hidden_she_leaves_no_room_at_the_bottom() -> None:
    """Lo spazio che le si lascia in fondo e' scritto con ``--jenny-art-h``: a
    zero, la fascia del filo e il fondo delle pagine la lasciano fuori."""
    home = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert ":root[data-jenny-hidden] .home-shell { --jenny-art-h: 0; }" in home
    assert ":root[data-jenny-hidden] .home-band { display: none; }" in home
    assert ":root:not([data-jenny-hidden]) .home-msg-jenny.home-at-band > .home-tail" in home


def test_the_chat_has_another_door_when_she_is_hidden() -> None:
    """La chiave in testa apre il velo come il tocco su di lei, e porta il suo
    pallino; nel velo la freccia per chiuderlo c'e' sempre."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'id="home-chat-key"' in html
    assert 'id="home-chat-key-dot"' in html
    assert 'id="home-jenny-visible"' in html, "manca l'interruttore nella stanza Jenny"
    app = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    assert "this.chatKey?.addEventListener('click', () => this.openChatOver());" in app
    assert "this.chatKey.hidden = !mascotHidden() || this.view === 'chat';" in app
    assert "if (mascotHidden() && this.overBack) this.overBack.hidden = false;" in app
    assert "if (this.chatKeyDot) this.chatKeyDot.hidden = !on;" in app


def test_the_first_paint_knows_she_is_hidden() -> None:
    """Senza l'attributo al primo fotogramma comparirebbe per un istante."""
    boot = (ASSETS / "bootstrap.js").read_text(encoding="utf-8")
    assert "read('jenny-mascot-hidden') === '1'" in boot
    assert "setAttribute('data-jenny-hidden', '')" in boot
