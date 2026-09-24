"""«Tu e Jenny»: la striscia dei temi, e la riga della versione.

Il tema e' l'unica impostazione della casa il cui effetto e' tutto a schermo,
e per questo non apre una stanza: si tocca e c'e'. Le due cose che qui si
misurano non si vedrebbero guardando lo schermo una volta sola.

**Il quadrato porta tre tinte, non l'accento.** Fra ``chanel`` e ``pietra``
l'accento e' quasi lo stesso: con un pallino solo i due temi sarebbero due
pastiglie identiche, e la striscia smetterebbe di essere una scelta.

**La striscia si disegna una volta.** I sette temi non cambiano mentre guardi,
e ridisegnarli a ogni tocco butterebbe via lo scorrimento di lato — chi ha
appena scelto il settimo si ritroverebbe riportato al primo.

I membri si ritagliano dal sorgente e girano in node su un DOM finto:
``casa-tu.js`` importa ``theme.js``, che al caricamento tocca il documento e il
``localStorage``, e importarlo davvero vorrebbe dire montare il guscio per
provare una striscia. ``THEMES`` e ``swatchGradient`` invece sono **veri**: i
sette temi e i loro colori sono proprio cio' che non deve essere ricostruito a
mano.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
TU_JS = ASSETS / "casa-tu.js"
THEME_JS = ASSETS / "shared" / "theme.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


def _const_block(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export const {re.escape(name)} = (?:\{{.*?^\}}|\[.*?^\]);$", source)
    assert m, f"const {name} non trovata"
    return m.group(0).removeprefix("export ")


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    hidden: false,
    dataset: {},
    attrs: {},
    style: {},
    children: [],
    listeners: {},
    isFragment: tag === '#fragment',
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { (el.listeners[type] ||= []).push(fn); },
    appendChild(child) {
      if (child.isFragment) { el.children.push(...child.children); child.children = []; }
      else el.children.push(child);
      return child;
    },
    classList: {
      add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
      remove(n) {
        el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
      },
      contains(n) { return String(el.className).split(' ').includes(n); },
      toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
    },
  };
  return el;
}

const nodi = {};
const document = {
  createElement: (tag) => makeEl(tag),
  createDocumentFragment: () => makeEl('#fragment'),
  getElementById: (id) => (nodi[id] ||= makeEl('div')),
};

/* I sette temi sono veri; il motore che li applica no — `setTheme` scrive su
   `<html>`, sul `localStorage` e sulle barre di sistema di Android. Qui conta
   che la striscia chieda il tema giusto e si rimetta a posto dopo. */
__THEMES__
let acceso = 'chanel';
const applicati = [];
function currentTheme() { return THEMES.find((t) => t.id === acceso) || THEMES[0]; }
function setTheme(id) {
  applicati.push(id);
  acceso = id;
  return currentTheme();
}

__SWATCH__
__SHORT_NAME__

class CasaTu {
  __CTOR__
  __OPEN__
  __APPLY_TRANSLATIONS__
  __PICK_THEME__
  __PAINT_THEMES__
  __THEME_CARD__
  __MARK_THEME__
  __SAY_THEME__
  __SHOW_VERSION__
  __SAY_JENNY__
}

function stanza() {
  for (const k of Object.keys(nodi)) delete nodi[k];
  acceso = 'chanel';
  applicati.length = 0;
  return new CasaTu({});
}
"""


def _harness() -> str:
    src = TU_JS.read_text(encoding="utf-8")
    theme = THEME_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__THEMES__", _const_block(theme, "THEMES"))
        .replace("__SWATCH__", function(src, "swatchGradient"))
        .replace("__SHORT_NAME__", function(src, "shortThemeName"))
        .replace("__CTOR__", member(src, "constructor"))
        .replace("__OPEN__", member(src, "open"))
        .replace("__APPLY_TRANSLATIONS__", member(src, "applyTranslations"))
        .replace("__PICK_THEME__", member(src, "pickTheme"))
        .replace("__PAINT_THEMES__", member(src, "_paintThemes"))
        .replace("__THEME_CARD__", member(src, "_themeCard"))
        .replace("__MARK_THEME__", member(src, "_markTheme"))
        .replace("__SAY_THEME__", member(src, "_sayTheme"))
        .replace("__SHOW_VERSION__", member(src, "sayUpdates"))
        .replace("__SAY_JENNY__", member(src, "sayJenny"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── I tre colori ────────────────────────────────────────────────────────────


def test_a_swatch_carries_all_three_tints() -> None:
    """Non l'accento soltanto: e' lo sfondo a dire di che tema si tratta."""
    _run_js("""
      const g = swatchGradient(['#111111', '#222222', '#333333']);
      for (const c of ['#111111', '#222222', '#333333']) {
        assert.ok(g.includes(c), 'manca una tinta: ' + c);
      }
      assert.ok(/34%/.test(g) && /67%/.test(g), 'le tinte non sono divise in tre: ' + g);
    """)


def test_the_strip_shows_every_theme_with_its_own_colours() -> None:
    """Sette pastiglie, nell'ordine del registro, e ognuna coi suoi colori.

    Se il quadrato prendesse i colori di un tema solo — il primo, o quello
    acceso — la striscia sarebbe sette volte la stessa immagine.
    """
    _run_js("""
      const tu = stanza();
      tu.open();
      assert.equal(tu.themesEl.children.length, THEMES.length);
      THEMES.forEach((tema, i) => {
        const card = tu.themesEl.children[i];
        assert.equal(card.dataset.theme, tema.id, 'ordine diverso da quello del registro');
        const nome = card.children[card.children.length - 1];
        assert.equal(nome.textContent, shortThemeName(tema.label));
        const quadrato = card.children[0];
        for (const c of tema.swatch) {
          assert.ok(quadrato.style.background.includes(c),
                    tema.id + ' non porta ' + c + ': ' + quadrato.style.background);
        }
      });
    """)


# ── Quale e' acceso ─────────────────────────────────────────────────────────


def test_the_ring_marks_exactly_the_one_that_is_on() -> None:
    _run_js("""
      const tu = stanza();
      acceso = 'kyoto';
      tu.open();
      const accese = tu.themesEl.children.filter((c) => c.classList.contains('is-on'));
      assert.equal(accese.length, 1, 'anelli accesi: ' + accese.length);
      assert.equal(accese[0].dataset.theme, 'kyoto');
      for (const card of tu.themesEl.children) {
        assert.equal(card.attrs['aria-checked'],
                     String(card.dataset.theme === 'kyoto'),
                     card.dataset.theme + ': quel che si legge e quel che si vede non coincidono');
      }
    """)


def test_picking_a_theme_moves_the_ring_without_redrawing_the_strip() -> None:
    """Ridisegnare butterebbe via lo scorrimento di lato: chi ha appena scelto
    il settimo si ritroverebbe riportato al primo."""
    _run_js("""
      const tu = stanza();
      tu.open();
      const prima = [...tu.themesEl.children];
      tu.pickTheme('y2k');
      assert.deepEqual(applicati, ['y2k'], 'il tema non e\\u2019 stato applicato');
      assert.equal(tu.themesEl.children.length, prima.length);
      tu.themesEl.children.forEach((card, i) => {
        assert.equal(card, prima[i], 'la striscia e\\u2019 stata ridisegnata');
      });
      const accese = tu.themesEl.children.filter((c) => c.classList.contains('is-on'));
      assert.equal(accese.length, 1);
      assert.equal(accese[0].dataset.theme, 'y2k', 'l\\u2019anello e\\u2019 rimasto sul tema di prima');

      /* E nemmeno riaprendo la stanza: da «Tu e Jenny» ci si torna a ogni
         giro, e senza la guardia le sette pastiglie diventano quattordici —
         un secondo elenco identico in coda al primo. */
      tu.open();
      assert.equal(tu.themesEl.children.length, THEMES.length, 'la striscia si accumula');
      tu.themesEl.children.forEach((card, i) => {
        assert.equal(card, prima[i], 'ridisegnata alla riapertura');
      });
    """)


def test_the_name_and_the_phrase_follow_the_theme_that_is_on() -> None:
    """Il nome non si traduce — «Jenny Kyoto» e' un nome — ma la frase che lo
    racconta si', ed e' quella dell'officina: gia' tradotta, e una sola."""
    _run_js("""
      const tu = stanza();
      tu.open();
      assert.equal(tu.themeValue.textContent, THEMES.find((t) => t.id === 'chanel').label);
      assert.equal(tu.themeDesc.textContent, i18n.t('themes.chanel.desc'));
      assert.ok(!tu.themeDesc.textContent.startsWith('themes.'), 'la chiave grezza a schermo');

      tu.pickTheme('pietra');
      assert.equal(tu.themeValue.textContent, THEMES.find((t) => t.id === 'pietra').label);
      assert.equal(tu.themeDesc.textContent, i18n.t('themes.pietra.desc'));
      assert.notEqual(i18n.t('themes.pietra.desc'), i18n.t('themes.chanel.desc'),
                      'due temi con la stessa frase: il banco non misura piu\\u2019 niente');
    """)


def test_the_words_come_back_when_the_language_changes() -> None:
    """Cambiata la lingua, la stanza si ridice: l'etichetta, la scheda
    dell'officina e la frase del tema."""
    _run_js("""
      const tu = stanza();
      tu.open();
      tu.themeLabel.textContent = '';
      tu.workshopName.textContent = '';
      tu.workshopHint.textContent = '';
      tu.themeDesc.textContent = '';
      tu.jennyLabel.textContent = '';
      tu.applyTranslations();
      assert.equal(tu.themeLabel.textContent, i18n.t('settings.themeLabel'));
      assert.equal(tu.workshopName.textContent, i18n.t('casa.workshop'));
      assert.equal(tu.workshopHint.textContent, i18n.t('casa.tu.workshopHint'));
      assert.equal(tu.jennyLabel.textContent, i18n.t('casa.jenny.title'));
      assert.equal(tu.themeDesc.textContent, i18n.t('themes.chanel.desc'));
    """)


# ── La versione ─────────────────────────────────────────────────────────────


def test_the_pill_keeps_the_word_that_tells_the_themes_apart() -> None:
    """Sei nomi su sette cominciano per «Jenny»: in una fila di pastiglie larghe
    56 px quella parola mangia il posto di quella che distingue, e a schermo
    restavano «Jenny Ky…», «Jenny Sti…», «Jenny Fu…» — tre pastiglie diverse
    che dicono la stessa cosa. Il nome intero resta nelle schede larghe
    dell'officina."""
    _run_js("""
      assert.equal(shortThemeName('Jenny Kyoto'), 'Kyoto');
      assert.equal(shortThemeName("Synthwave '84"), 'Synthwave', 'l\u2019anno e\u2019 la coda del nome');
      assert.equal(shortThemeName('Chanel'), 'Chanel', 'un nome senza «Jenny» resta intero');
      const corti = THEMES.map((t) => shortThemeName(t.label));
      assert.equal(new Set(corti).size, THEMES.length, 'due temi con lo stesso nome corto');
      for (const nome of corti) {
        assert.ok(nome.length <= 9, 'non ci sta nella pastiglia: ' + nome);
        assert.ok(!nome.startsWith('Jenny'), 'e\u2019 rimasto il nome di lei: ' + nome);
      }
    """)


def test_the_version_rides_on_the_row_that_opens_the_updates() -> None:
    """Stava su una riga muta in fondo alla pagina, e un numero e basta non e'
    un'impostazione: e' un'etichetta. Adesso e' il valore della riga che porta
    agli aggiornamenti, come «grande» lo e' della riga che porta da lei.

    Il patto resta quello: un numero che non si sa non si scrive — una stringa
    vuota, non «versione {version}» con la graffa dentro.
    """
    _run_js("""
      const tu = stanza();
      tu.sayUpdates('');
      assert.equal(nodi['casa-updates-value'].textContent, '',
        'una versione che non si sa e\u2019 finita a schermo');

      tu.sayUpdates('0.11.0 \u00b7 aggiornata');
      assert.equal(nodi['casa-updates-value'].textContent, '0.11.0 \u00b7 aggiornata');
      assert.ok(!nodi['casa-updates-value'].textContent.includes('{'),
        'il segnaposto e\u2019 rimasto dentro');
    """)


def test_the_row_that_leads_to_her_says_how_she_is_now() -> None:
    """Una riga che non porta il suo valore e' un collegamento, non
    un'impostazione. Il valore lo compone la stanza di lei; qui si misura che
    arrivi a schermo, e che svuotarlo non lasci a mezz'aria quello di prima."""
    _run_js("""
      const tu = stanza();
      tu.sayJenny('piccola \u00b7 flottante');
      assert.equal(tu.jennyValue.textContent, 'piccola \u00b7 flottante');
      tu.sayJenny(undefined);
      assert.equal(tu.jennyValue.textContent, '', 'il valore di prima e\u2019 rimasto');
    """)
