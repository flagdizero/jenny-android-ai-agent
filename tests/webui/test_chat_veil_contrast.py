"""Il velo sotto la chat aperta sopra una pagina: leggibile in ogni tema.

Dal 05/10/2026 Jenny e' la porta della chat: toccata dove la chat non c'e', la
chat vera sale a tutto schermo sopra la pagina (`#home-over` in casa,
`:root.chat-over` nell'officina), e la pagina resta visibile sotto un velo
(`--chat-veil`) ed e' sfocata (`--chat-veil-blur`). Il velo deve lasciar
leggere la chat sopra **anche** dove sotto passa il testo della pagina.

Dal 06/10/2026 il campo dove scrivi e' di vetro (`--glass-*`), ovunque: sopra
il velo e sulla pagina chat. Il vetro e' un altro strato traslucido, e il conto
lo compone sopra il velo. Le risposte di Jenny no: stanno sul velo, e le conta
il primo banco.

Il caso peggiore contato qui: sotto il testo della chat c'e' un glifo della
pagina, del colore del testo del tema, **non sfocato**. La sfocatura spalma il
glifo sul suo fondo e lo attenua, quindi nel telefono il contrasto e' piu' alto
di questo: e' margine, non e' il conto.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import theme_tokens

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MOBILE_CSS = ASSETS / "mobile-style.css"
HOME_CSS = ASSETS / "home-style.css"

#: WCAG AA per il testo normale.
AA = 4.5


#: Il segnaposto del campo: sparisce appena scrivi, e TalkBack lo legge come
#: nome del campo, quindi basta che si veda (la soglia degli oggetti grafici).
PLACEHOLDER = 3.0


def _low(fg: str, *grounds: str, floor: float = AA) -> list[str]:
    """I temi in cui `fg` sulla pila `grounds` (nomi di token, dal basso) sta
    sotto `floor`."""
    low = []
    for theme in theme_tokens.themes():
        v = theme_tokens.tokens(theme)
        ratio = theme_tokens.layered(v[fg], *(v[g] for g in grounds))
        if ratio < floor:
            low.append(f"{theme}: {ratio:.2f}")
    return low


def test_the_chat_reads_over_the_veil_in_every_theme() -> None:
    # Il velo composto su un glifo della pagina sotto, poi il testo sopra.
    low = _low("text", "text", "chat-veil")
    assert not low, f"la chat sopra il velo non si legge: {low}"


def test_the_work_bubble_reads_on_its_glass() -> None:
    over = _low("text", "text", "chat-veil", "glass-bg")
    assert not over, f"la bolla del lavoro sopra il velo non si legge: {over}"
    page = _low("text", "bg", "glass-bg")
    assert not page, f"la bolla del lavoro sulla pagina chat non si legge: {page}"


def test_jennys_replies_have_no_card() -> None:
    """Le bolle le hai solo tu: lei scrive sul fondo, perche' le sue risposte
    sono lunghe e lo schermo e' quadrato (06/10/2026)."""
    css = re.sub(r"/\*.*?\*/", "", HOME_CSS.read_text(encoding="utf-8")
                 + MOBILE_CSS.read_text(encoding="utf-8"), flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"\.chat-msg-ai \.chat-content|\.home-msg-jenny( \.home-block)?\s*$", selectors.strip()):
            assert not re.search(r"\b(background|border|box-shadow)\s*:", body), selectors.strip()


def test_what_you_type_reads_on_the_glass_field() -> None:
    for grounds in (("text", "chat-veil", "glass-field-bg"), ("bg", "glass-field-bg")):
        low = _low("text", *grounds)
        assert not low, f"il campo di vetro su {grounds[:-1]} non si legge: {low}"
        hint = _low("text-muted", *grounds, floor=PLACEHOLDER)
        assert not hint, f"il segnaposto su {grounds[:-1]} non si vede: {hint}"


def test_the_veil_still_lets_the_page_show_through() -> None:
    """Il velo non e' un muro: e' la pagina di cui stai parlando."""
    for theme in theme_tokens.themes():
        alpha = theme_tokens.rgba(theme_tokens.tokens(theme)["chat-veil"])[3]
        assert alpha <= 0.8, f"{theme}: il velo al {alpha:.0%} copre la pagina sotto"


def test_the_blur_is_on_the_still_page_never_animated_never_on_the_veil() -> None:
    mobile = MOBILE_CSS.read_text(encoding="utf-8")
    home = HOME_CSS.read_text(encoding="utf-8")
    blur = re.search(r"--chat-veil-blur:\s*([\d.]+)px;", mobile)
    assert blur, "la sfocatura sotto il velo non c'e'"
    # Forte dal 06/10/2026 (della pagina restano colori e masse, Vetro 2), ma
    # con un tetto: oltre i 24 px non si vede piu' differenza e il costo sale.
    assert 0 < float(blur.group(1)) <= 24, blur.group(0)
    # Sfocata la pagina, ferma, e non il velo: un `backdrop-filter` sul velo
    # si ricalcola a ogni scorrere della chat, e nell'officina starebbe sullo
    # scroller dei manici di selezione.
    css = re.sub(r"/\*.*?\*/", "", home + mobile, flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"home-over|chat-over|#view-chat", selectors):
            assert "backdrop-filter" not in body, f"{selectors.strip()}: v. la docstring"
    shop = re.search(
        r":root\.chat-over \.view-beneath,\s*:root\.chat-over-app \.app-frame-overlay \{([^}]*)\}",
        mobile,
    )
    assert shop and re.search(r"filter: blur\(var\(--chat-veil-blur\)\)", shop.group(1)), (
        "nell'officina la vista sotto la chat non e' sfocata"
    )
    house = re.search(r"\n\.home-beneath \{([^}]*)\}", home)
    assert house and re.search(r"filter: blur\(var\(--chat-veil-blur", house.group(1)), (
        "in casa la pagina sotto la chat non e' sfocata"
    )
    # Il filtro non si anima: a 18 px una transizione ricalcola la sfocatura a
    # ogni fotogramma dell'entrata, ed e' la parte piu' cara del velo.
    for rule in (shop.group(1), house.group(1)):
        assert "transition" not in rule, rule


def test_the_glass_never_blurs_what_is_behind_it() -> None:
    """Il vetro e' trasparenza sopra una pagina gia' sfocata e ferma: un
    `backdrop-filter` sulle schede o sul campo si ricalcolerebbe a ogni
    fotogramma di scorrimento e di risposta in arrivo."""
    css = re.sub(r"/\*.*?\*/", "", HOME_CSS.read_text(encoding="utf-8")
                 + MOBILE_CSS.read_text(encoding="utf-8"), flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "var(--glass-" in body:
            assert "backdrop-filter" not in body, selectors.strip()


def test_the_line_under_a_reply_reads_on_the_veil() -> None:
    """Copia e i secondi stanno fuori dalla scheda, sul velo: li' i grigi
    discreti della pagina chat non si leggono (sul caso peggiore stanno sotto
    3,5 nei temi chiari), quindi sopra il velo prendono il colore del testo, che
    il primo banco misura."""
    home = HOME_CSS.read_text(encoding="utf-8")
    mobile = MOBILE_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.home-over \.home-copy,\s*\.home-over \.home-seconds \{ color: var\(--text\); \}", home)
    assert re.search(
        r":root\.chat-over \.chat-msg-action,\s*:root\.chat-over \.chat-meta \{ color: var\(--text\); \}", mobile
    )


def test_the_workshop_bars_over_the_veil_hide_the_text_that_scrolls_under_them() -> None:
    """Nell'officina il filo scorre sotto la testata e il campo: sopra il velo
    sono una banda quasi opaca, perche' il testo sotto non traspaia."""
    for theme in theme_tokens.themes():
        alpha = theme_tokens.rgba(theme_tokens.tokens(theme)["chat-band"])[3]
        assert alpha >= 0.9, f"{theme}: la banda al {alpha:.0%} lascia passare il testo"
