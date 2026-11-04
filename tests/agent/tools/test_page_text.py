"""Il corpo di una pagina senza il contorno (``page_text.extract``)."""

from jenny.agent.tools import page_text

_PARAGRAPH = (
    "La calathea vuole luce filtrata, terriccio umido ma mai fradicio, e acqua "
    "senza calcare; le foglie si arricciano quando l'aria è secca, e ingialliscono "
    "quando le radici restano bagnate troppo a lungo. "
)

_NAV = """
<header><a href="/">Casa</a><nav><ul>
  <li><a href="/telefonia">Telefonia</a></li><li><a href="/tv">TV</a></li>
  <li><a href="/cucina">Cucina e caffè</a></li>
</ul></nav></header>
"""
_FOOTER = '<footer><a href="/lavora">Lavora con noi</a> Copyright</footer>'
_COOKIE = '<div class="cookie-banner">Usiamo i cookie. Accetta tutto</div>'


def _page(body: str, title: str = "Guida") -> str:
    return f"<html><head><title>{title}</title><style>p{{}}</style></head><body>{body}</body></html>"


def test_an_article_comes_without_menus_footer_and_cookie_banner():
    html = _page(
        _COOKIE + _NAV
        + f"<article><h1>Calathea</h1><p>{_PARAGRAPH}</p><p>{_PARAGRAPH}</p>"
        + f"<p>{_PARAGRAPH}</p><p>{_PARAGRAPH}</p></article>"
        + '<aside><a href="/correlati">Articoli correlati</a></aside>' + _FOOTER
    )
    text = page_text.extract(html, "https://example.com/guida")
    assert text.startswith("# Calathea")
    assert "luce filtrata" in text
    for chrome in ("Telefonia", "Lavora con noi", "cookie", "correlati", "p{}"):
        assert chrome not in text


def test_a_link_keeps_its_words_inline_and_its_address_at_the_end():
    html = _page(
        f'<article><p>{_PARAGRAPH} Vedi la <a href="/pagina-2">seconda pagina</a>.</p>'
        f"<p>{_PARAGRAPH}</p><p>{_PARAGRAPH}</p><p>{_PARAGRAPH}</p></article>"
    )
    text = page_text.extract(html, "https://example.com/guida")
    body, links = text.split("\n\nLinks:\n")
    assert "Vedi la seconda pagina." in body
    assert "](" not in body and "https://" not in body
    assert links == "- seconda pagina: https://example.com/pagina-2"


def test_the_links_are_few_and_never_fragments_or_scripts():
    anchors = "".join(f'<a href="/p{i}">pagina {i}</a> ' for i in range(40))
    html = _page(
        f'<article><p>{_PARAGRAPH}</p><p>{_PARAGRAPH}</p><p>{_PARAGRAPH}</p>'
        f'<p>{_PARAGRAPH} <a href="#top">su</a> <a href="javascript:void(0)">apri</a> '
        f"{anchors}</p></article>"
    )
    links = page_text.extract(html, "https://example.com/").split("\n\nLinks:\n")[1]
    assert len(links.splitlines()) == page_text._MAX_LINKS
    assert "#top" not in links and "javascript" not in links


def test_without_article_or_main_the_block_with_the_prose_wins():
    sidebar = "<div>" + "".join(f'<a href="/x{i}">Offerta {i}</a><br>' for i in range(30)) + "</div>"
    content = "<div>" + "".join(f"<p>{_PARAGRAPH}</p>" for i in range(5)) + "</div>"
    text = page_text.extract(_page(_NAV + sidebar + content))
    assert "luce filtrata" in text
    assert "Offerta 3" not in text


def test_a_list_of_teasers_is_not_one_article():
    teasers = "".join(
        f"<article><h2>Telefono {i}</h2><p>{_PARAGRAPH}</p></article>" for i in range(4)
    )
    text = page_text.extract(_page(f"<main>{teasers}</main>"))
    for i in range(4):
        assert f"## Telefono {i}" in text


def test_a_table_keeps_its_rows():
    table = (
        "<table><tr><th>Modello</th><th>Prezzo</th></tr>"
        "<tr><td>Alfa 5G</td><td>249 €</td></tr><tr><td>Beta</td><td>289 €</td></tr></table>"
    )
    html = _page(f"<article><p>{_PARAGRAPH}</p><p>{_PARAGRAPH}</p>{table}<p>{_PARAGRAPH}</p></article>")
    text = page_text.extract(html)
    assert "Modello | Prezzo\nAlfa 5G | 249 €\nBeta | 289 €" in text


def test_a_wrapper_named_like_chrome_but_full_of_prose_stays():
    html = _page(f'<div class="has-sidebar"><div>{"".join(f"<p>{_PARAGRAPH}</p>" for _ in range(4))}</div></div>')
    assert "luce filtrata" in page_text.extract(html)


def test_unclosed_paragraphs_and_stray_end_tags_do_not_lose_text():
    html = _page(f"<article><p>{_PARAGRAPH}<p>Secondo paragrafo, {_PARAGRAPH}</span></div><p>Fine.</article>")
    text = page_text.extract(html)
    assert "Secondo paragrafo" in text and "Fine." in text


def test_nothing_to_say_is_an_empty_string():
    assert page_text.extract("") == ""
    assert page_text.extract("<html><body><script>x()</script></body></html>") == ""
    assert page_text.extract("<p>x</p>" * (page_text._MAX_HTML_CHARS // 8 + 1)) == ""
