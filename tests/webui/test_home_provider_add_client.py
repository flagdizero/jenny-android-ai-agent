"""«Aggiungi chi risponde»: le due regole del foglio che non dipendono dal DOM.

``nameFromBase`` sceglie il nome che l'indirizzo suggerisce, e non deve mai
proporne uno gia' usato: il server rifiuterebbe (``name_taken``), e l'elenco dei
modelli di un nome gia' usato sarebbe quello del provider salvato
(``provider_models_payload``). ``stepTwoBlock`` dice quale campo ferma il passo
2: senza chiave la fabbrica dei provider non ne costruisce uno, quindi una
chiave vuota non arriva nemmeno alla prova.

Il giro intero, contro il gateway vero e un provider finto, e' stato provato sul
banco del Mac; qui le funzioni pure, ritagliate dal sorgente e girate in node.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, function, requires_node, run_js

pytestmark = requires_node

ADD_JS = ASSETS / "home-provider-add.js"
API_BASE_JS = ASSETS / "shared" / "api-base.js"


def _run(script: str) -> None:
    src = ADD_JS.read_text(encoding="utf-8")
    base = re.sub(r"^export ", "", API_BASE_JS.read_text(encoding="utf-8"), flags=re.M)
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"{base}\n"
        f"{function(src, 'nameFromBase')}\n"
        f"{function(src, 'stepTwoBlock')}\n"
        f"{script}\n"
    )


def test_the_name_is_the_brand_the_address_belongs_to() -> None:
    _run("""
      assert.equal(nameFromBase('https://api.deepseek.com/v1', 'openai_compat'), 'deepseek');
      assert.equal(nameFromBase('https://openrouter.ai/api/v1', 'openai_compat'), 'openrouter');
      assert.equal(nameFromBase('openrouter.ai/api/v1', 'openai_compat'), 'openrouter');
      assert.equal(nameFromBase('http://localhost:11434/v1', 'openai_compat'), 'localhost');
      assert.equal(nameFromBase('http://192.168.1.20:11434/v1', 'openai_compat'), '192.168.1.20');
      assert.equal(nameFromBase('HTTPS://API.Example.COM', 'openai_compat'), 'example');
    """)


def test_an_empty_address_is_named_after_the_format() -> None:
    _run("""
      assert.equal(nameFromBase('', 'openai_compat'), 'openai');
      assert.equal(nameFromBase('  ', 'anthropic'), 'anthropic');
    """)


def test_the_suggested_name_is_never_one_in_use() -> None:
    _run("""
      assert.equal(nameFromBase('https://api.deepseek.com', 'openai_compat', ['deepseek']),
                   'deepseek_2');
      assert.equal(nameFromBase('https://api.deepseek.com', 'openai_compat',
                                ['deepseek', 'deepseek_2']), 'deepseek_3');
      assert.equal(nameFromBase('', 'anthropic', ['anthropic']), 'anthropic_2');
    """)


def test_step_two_stops_at_the_first_field_that_is_wrong() -> None:
    _run("""
      const ok = { base: '', name: 'studio', key: 'sk-x' };
      assert.equal(stepTwoBlock(ok, ['groq']), null);
      assert.equal(stepTwoBlock({ ...ok, base: 'Http:/storpiato' }, []), 'base');
      assert.equal(stepTwoBlock({ ...ok, name: '  ' }, []), 'name');
      assert.equal(stepTwoBlock({ ...ok, name: ' groq ' }, ['groq']), 'nameTaken');
      assert.equal(stepTwoBlock({ ...ok, key: '' }, []), 'key',
                   'senza chiave la fabbrica non costruisce il provider');
      assert.equal(stepTwoBlock({ ...ok, base: 'http://localhost:11434/v1' }, []), null);
    """)
