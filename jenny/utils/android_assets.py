from __future__ import annotations

import hashlib
import stat
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from jenny.utils.path import atomic_write

if TYPE_CHECKING:
    pass

# File del workspace che appartengono all'utente o a Dream. Si creano al primo
# avvio e non si toccano mai più: `SOUL.md` e `USER.md` li riscrive Dream,
# `AGENTS.md` e `HEARTBEAT.md` li scrive l'utente. Sovrascriverli con la copia
# del pacchetto cancellerebbe la personalità del bot e le istruzioni scritte a
# mano.
_USER_OWNED_TEMPLATES = [
    "AGENTS.md", "SOUL.md", "USER.md", "HEARTBEAT.md",
    "memory/MEMORY.md",
]

# Prompt di sistema. Non li scrive nessun utente: sono il codice sorgente del
# comportamento dell'agente, scritto in italiano invece che in Python.
#
# Vanno riscritti a OGNI avvio, ed è la differenza fra un aggiornamento che
# arriva e uno che no. Trattandoli come i file dell'utente — la cosa che questo
# codice faceva prima — una correzione a un prompt raggiungeva solo le
# installazioni nuove: su un telefono aggiornato da mesi restava per sempre il
# testo della versione in cui era stato installato. Un file *nuovo* arrivava, un
# file *corretto* no, il che è il modo peggiore di sbagliare perché sembra che
# funzioni.
_SYSTEM_PROMPT_TEMPLATES = [
    "agent/identity.md", "agent/tool_contract.md", "agent/subagent_system.md",
    "agent/subagent_announce.md", "agent/skills_section.md", "agent/apps_section.md",
    "agent/platform_policy.md", "agent/cron_reminder.md", "agent/cron_monitor.md",
    "agent/max_iterations_message.md", "agent/consolidator_archive.md",
    "agent/dream.md", "agent/dream_project.md", "agent/dream_review.md",
    "agent/gardener.md",
    "agent/scheduling.md",
    "agent/project.md",
    "agent/readonly.md", "agent/project_init.md", "agent/tidy.md",
    "agent/orchestrator.md", "agent/tool_inventory.md",
    "agent/_snippets/untrusted_content.md",
    "agent/types/researcher.md", "agent/types/writer.md", "agent/types/coder.md",
    "agent/types/analyst.md", "agent/types/sysadmin.md", "agent/types/operator.md",
]

_TEMPLATES_MANIFEST = [*_USER_OWNED_TEMPLATES, *_SYSTEM_PROMPT_TEMPLATES]

# Le versioni *ritirate* dei template dell'utente: digest sha256 del testo
# strippato → etichetta della finestra di release che le spediva.
#
# Stanno qui, accanto alle due liste sopra, perché rispondono alla stessa
# domanda — quali template esistono e che politica riceve ciascuno — e perché i
# consumatori sono due: ``ContextBuilder._is_template_content``, che con questi
# digest riconosce un file che l'utente non ha mai scritto, e
# ``retire_withdrawn_templates`` qui sotto, che quel file lo riscrive. Due copie
# di un insieme che deve restare allineato è il guasto che questo repo continua
# a dover riparare (tre copie della regola sui prefissi interni fra
# ``session/keys.py``, ``agent/memory.py`` e ``agent/autocompact.py`; v.
# ``roadmap/project-sessions.md``), quindi la definizione è una sola e chi la
# vuole la importa.
#
# Servono perché il riconoscimento del template è un confronto con la copia
# bundled **corrente**: riscrivere un template scollega ogni installazione
# seedata con quella precedente e mai toccata da Dream, che da un momento
# all'altro smette di essere "il modulo vuoto di serie" e diventa "roba scritta
# dall'utente" — modulo a caselle compreso, e senza nemmeno l'etichetta, perché
# quel ramo non la mette.
#
# Non è una finestra breve: un file dell'utente si crea una volta sola e non si
# tocca più, quindi un telefono porta per sempre la versione che era bundled al
# *suo* primo avvio, indipendentemente da quanti aggiornamenti ha preso dopo.
# Sono tutte candidate vive.
#
# Chi riscrive un template qui elencato deve aggiungere il digest della versione
# uscente. Non è un promemoria: ``test_current_user_template_digest_is_pinned``
# (e il suo gemello per ``AGENTS.md``) fallisce finché non lo si fa.
#
# Da 0.8.0 tre di questi template sono spediti **vuoti**, e il ritiro delle
# versioni con la prosa è ciò che tiene fuori dal prompt le installazioni già
# esistenti: senza, un ``USER.md`` mai toccato smetterebbe di combaciare con il
# bundle corrente e rientrerebbe in ogni turno come "scritto dall'utente", senza
# nemmeno l'etichetta. Il ritiro di quelle copie porta a zero byte anche il file
# sul disco, ed è voluto: v. la guardia ``if data is None`` in
# ``retire_withdrawn_templates``, che è ciò che distingue un asset spedito vuoto
# da uno che non si è riusciti a leggere.
_RETIRED_TEMPLATE_DIGESTS: dict[str, dict[str, str]] = {
    # "# User Profile" con i tre blocchi di caselle, "(your name)" e le sezioni
    # fra parentesi, e — dalla riscrittura dopo — lo scaffold di prosa senza
    # ancora la riga sui fatti che il runtime già calcola.
    "USER.md": {
        "db2c6d63e0b43e5ac414da85f86454e2614f6524d4ef92a291f11476e6e03deb":
            "v0.3.0 to v0.7.1 (8833b94)",
        # Mai uscita in una release, ma un'installazione da sorgente sul ramo
        # 0.8.0 in quella finestra ce l'ha.
        "89c4ab4bfcdafea11e59b1856c31e08f16ba80960d68596f6fb631386a93c609":
            "unreleased source builds (97d7b38)",
        # Lo scaffold completo, riga sui fatti del runtime inclusa: l'ultima
        # versione con del testo dentro, prima che il file passasse a zero byte.
        "e23a60be0336c5220d3d0dbd256907f66b590156459422a244dcd24685eb49b7":
            "unreleased source builds (04de3cc)",
    },
    # Le tre versioni di "# Agent Instructions", il manuale di cron e heartbeat
    # che si spediva dentro un file dell'utente. Oggi quel testo vive in
    # ``agent/scheduling.md``, dove un aggiornamento arriva davvero.
    "AGENTS.md": {
        # È quella sul Titan 2.
        "a7883c61338446966621d481f996d7585987142461f716f64e04e4d692a6b341":
            "v0.3.0 to v0.6.0 (8833b94)",
        # + il blocco reminder/monitor. Mai uscita in una release, ma
        # un'installazione da sorgente in quella finestra ce l'ha.
        "7573b397f15350b683bb6e87392d27a479e62bf1894a53fe2a60d29d813106c6":
            "unreleased source builds (6c5dba8)",
        # + il contratto di silenzio.
        "72d4bd718e70e16b9e6b7f5f9a0dc73a5b34d4a972bb43c0b6ebec5072d280c3":
            "v0.6.6 to v0.7.1 (1f23ef3)",
        # "# Workspace Conventions": il manuale se n'era già andato, restava la
        # prosa che spiegava il file a sé stesso. Ultima versione con del testo.
        "f7168ac0aacf6424203c6173e46ed333981f57c3d491f055fe1358c9b9614569":
            "unreleased source builds (007c60d)",
    },
    # Lo scaffold di ``memory/MEMORY.md``, invariato dalla 0.3.0. Le sue
    # intestazioni ``## User Information`` e ``## Preferences`` contraddicono il
    # routing di ``agent/dream.md``, che i fatti sull'utente li manda in
    # ``USER.md``: finché il file resta intatto ``_is_template_content`` lo
    # sopprime, ma se smettesse di combaciare col bundle entrerebbe in ogni
    # prompt a insegnare il contrario delle regole di sistema.
    #
    # Non è un file di bootstrap (v. ``ContextBuilder.BOOTSTRAP_FILES``): il suo
    # unico consumatore qui è il riconoscimento in ``build_system_prompt``.
    "memory/MEMORY.md": {
        "d7d84cc166a24465a19ad90da2aefc4cb579b3278a893c4adbd36c368aab427d":
            "v0.3.0 to v0.7.1 (8833b94)",
    },
}

# Un BOM UTF-8 in testa al file. Non è testo — è un residuo di codifica che
# scrive qualunque editor Windows, e Notepad lo mette anche su un file che apri
# e salvi senza toccare — ma ``str.strip()`` non lo rimuove: ``"﻿"`` non è
# whitespace per Python (categoria Cf, ``isspace()`` è ``False``).
#
# Senza toglierlo, un template intatto salvato una volta da un editor smette di
# combaciare con qualunque digest: rientra in ogni prompt come prosa dell'utente,
# senza nemmeno l'etichetta "default intatto", e il ritiro non lo vede più.
#
# Da non confondere con i fine-riga, che *sono* già normalizzati: tutti i lettori
# passano da ``Path.read_text``, e con ``newline=None`` un CRLF diventa ``\n``
# prima dell'hash. Quella pista è stata seguita per sbaglio; l'innesco è questo.
_BOM = "﻿"


def normalized_template_text(content: str) -> str:
    """Il testo di un template nella forma su cui si confronta.

    Una stesura sola perché i confronti sono due e devono restare d'accordo:
    :func:`retire_withdrawn_templates` qui sotto e
    ``ContextBuilder._is_template_content``. Se divergessero, un file sarebbe
    riconosciuto come template da uno dei due e non dall'altro — cioè tenuto
    fuori dal prompt e mai migrato, o migrato e intanto iniettato.
    """
    return content.lstrip(_BOM).strip()


def template_digest(content: str) -> str:
    """Il digest con cui si cerca *content* fra le versioni ritirate."""
    return hashlib.sha256(normalized_template_text(content).encode("utf-8")).hexdigest()


_SKILLS_MANIFEST = [
    "cron/SKILL.md",
    "long-goal/SKILL.md",
    # llm-wiki
    "llm-wiki/SKILL.md",
    "llm-wiki/scripts/audit_review.py",
    "llm-wiki/scripts/lint_wiki.py",
    "llm-wiki/scripts/reindex_wikis.py",
    "llm-wiki/scripts/scaffold.py",
    "llm-wiki/references/article-guide.md",
    "llm-wiki/references/audit-guide.md",
    "llm-wiki/references/log-guide.md",
    "llm-wiki/references/schema-guide.md",
    "llm-wiki/references/tooling-tips.md",
    # my
    "my/SKILL.md",
    "my/references/examples.md",
    # http-client
    "http-client/SKILL.md",
    # skill-creator
    "skill-creator/SKILL.md",
    "skill-creator/scripts/init_skill.py",
    "skill-creator/scripts/quick_validate.py",
    "skill-creator/scripts/package_skill.py",
    # app-creator
    "app-creator/SKILL.md",
    "app-creator/references/manifest.md",
    "app-creator/scripts/validate_app.py",
    # memory
    "memory/SKILL.md",
    # data-processing
    "data-processing/SKILL.md",
    # ssh
    "ssh/SKILL.md",
]

_UI_MANIFEST = [
    "assets/apps/jenny-charts.js",
    "assets/apps/jenny-kit.css",
    "assets/apps/jenny-sdk.js",
    "assets/bootstrap.js",
    "assets/i18n/en.json",
    "assets/i18n/it.json",
    "assets/jenny-body-front-hand.webp",
    "assets/jenny-body-front-idle.webp",
    "assets/jenny-body-front-think.webp",
    "assets/jenny-face-front-angry.webp",
    "assets/jenny-face-front-happy.webp",
    "assets/jenny-face-front-normal-talk.webp",
    "assets/jenny-face-front-normal.webp",
    "assets/jenny-face-front-sad.webp",
    "assets/jenny-face-front-thinking.webp",
    "assets/jenny-fall.webp",
    "assets/jenny-ground.webp",
    "assets/jenny-hang.webp",
    "assets/jenny-hello1.webp",
    "assets/jenny-hello2.webp",
    "assets/jenny-idle.webp",
    "assets/jenny-side-talk.webp",
    "assets/jenny-side.webp",
    "assets/jenny-walk1.webp",
    "assets/jenny-walk2.webp",
    "assets/mobile-app.js",
    "assets/mobile-apps.js",
    "assets/mobile-chat.js",
    "assets/mobile-drawer.js",
    "assets/mobile-graph.js",
    "assets/mobile-header.js",
    "assets/mobile-jenny.js",
    "assets/mobile-launcher.js",
    "assets/mobile-onboarding.js",
    "assets/mobile-settings.js",
    "assets/mobile-style.css",
    "assets/mobile-ui-query.js",
    "assets/mobile-wiki.js",
    "assets/mobile-workspace.js",
    "assets/shared/advanced-mode.js",
    "assets/shared/api-client.js",
    "assets/shared/backup-flow.js",
    "assets/shared/battery-exemption.js",
    "assets/shared/commands-chip.js",
    "assets/shared/cron-view.js",
    "assets/shared/dialog.js",
    "assets/shared/home-view.js",
    "assets/shared/i18n.js",
    "assets/shared/image-handler.js",
    "assets/shared/image-lightbox.js",
    "assets/shared/keyboard.js",
    "assets/shared/launcher-rank.js",
    "assets/shared/longpress.js",
    "assets/shared/mascot.js",
    "assets/shared/pinch-zoom.js",
    "assets/shared/project-delete.js",
    "assets/shared/provider-brand.js",
    "assets/shared/rpc-client.js",
    "assets/shared/scope-chip.js",
    "assets/shared/write-switch.js",
    "assets/shared/session-manager.js",
    "assets/shared/state.js",
    "assets/shared/subagent-policy.js",
    "assets/shared/telegram-pairing.js",
    "assets/shared/theme.js",
    "assets/shared/tree-renderer.js",
    "assets/shared/type-ahead.js",
    "assets/shared/utils.js",
    "assets/shared/wiki-search.js",
    "assets/shared/ws-manager.js",
    "assets/vendor/@tabler/icons-webfont@3.19.0/LICENSE",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/fonts/tabler-icons.ttf",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/fonts/tabler-icons.woff",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/fonts/tabler-icons.woff2",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css",
    "assets/vendor/codemirror@5.65.16/LICENSE",
    "assets/vendor/codemirror@5.65.16/addon/mode/simple.min.js",
    "assets/vendor/codemirror@5.65.16/lib/codemirror.min.css",
    "assets/vendor/codemirror@5.65.16/lib/codemirror.min.js",
    "assets/vendor/codemirror@5.65.16/mode/clike/clike.min.js",
    "assets/vendor/codemirror@5.65.16/mode/css/css.min.js",
    "assets/vendor/codemirror@5.65.16/mode/go/go.min.js",
    "assets/vendor/codemirror@5.65.16/mode/javascript/javascript.min.js",
    "assets/vendor/codemirror@5.65.16/mode/markdown/markdown.min.js",
    "assets/vendor/codemirror@5.65.16/mode/python/python.min.js",
    "assets/vendor/codemirror@5.65.16/mode/rust/rust.min.js",
    "assets/vendor/codemirror@5.65.16/mode/shell/shell.min.js",
    "assets/vendor/codemirror@5.65.16/mode/xml/xml.min.js",
    "assets/vendor/codemirror@5.65.16/mode/yaml/yaml.min.js",
    "assets/vendor/codemirror@5.65.16/theme/darcula.min.css",
    "assets/vendor/codemirror@5.65.16/theme/eclipse.min.css",
    "assets/vendor/d3@7/LICENSE",
    "assets/vendor/d3@7/d3.min.js",
    "assets/vendor/dompurify@3/LICENSE",
    "assets/vendor/dompurify@3/purify.min.js",
    "assets/vendor/fonts/LICENSE.txt",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa0ZL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1ZL7.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1pL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa25L7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa2JL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa2ZL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa2pL7SUc.woff2",
    "assets/vendor/fonts/google-fonts.css",
    "assets/vendor/fonts/baloo-2-700.woff2",
    "assets/vendor/fonts/bangers-400.woff2",
    "assets/vendor/fonts/comic-neue-400.woff2",
    "assets/vendor/fonts/comic-neue-700.woff2",
    "assets/vendor/fonts/fredoka-600.woff2",
    "assets/vendor/fonts/marcellus-400.woff2",
    "assets/vendor/fonts/orbitron-500.woff2",
    "assets/vendor/fonts/orbitron-700.woff2",
    "assets/vendor/fonts/shippori-mincho-500.woff2",
    "assets/vendor/fonts/tenor-sans-400.woff2",
    "assets/vendor/fonts/theme-fonts.css",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh09SDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh0NSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh0dSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh2dSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh3dSD.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh3tSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bhZ_Wmh2uX.woff2",
    "assets/vendor/highlight.js@11.11.1/LICENSE",
    "assets/vendor/highlight.js@11.11.1/build/highlight.min.js",
    "assets/vendor/highlight.js@11.11.1/styles/github-dark.min.css",
    "assets/vendor/highlight.js@11.11.1/styles/github.min.css",
    "assets/vendor/katex@0.16.10/LICENSE",
    "assets/vendor/katex@0.16.10/dist/contrib/auto-render.min.js",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_AMS-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_AMS-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_AMS-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-BoldItalic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-BoldItalic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-BoldItalic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Italic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Italic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Italic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-BoldItalic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-BoldItalic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-BoldItalic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-Italic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-Italic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-Italic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Italic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Italic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Italic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Script-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Script-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Script-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size1-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size1-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size1-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size2-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size2-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size2-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size3-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size3-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size3-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size4-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size4-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size4-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Typewriter-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Typewriter-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Typewriter-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/katex.min.css",
    "assets/vendor/katex@0.16.10/dist/katex.min.js",
    "assets/vendor/marked@15.0.7/LICENSE",
    "assets/vendor/marked@15.0.7/marked.min.js",
    "assets/vendor/mermaid@10/LICENSE",
    "assets/vendor/mermaid@10/dist/mermaid.min.js",
    "index.html",
]

_extracted_registry: dict[str, Path] = {}


def read_asset(module: str, path: str) -> bytes | None:
    try:
        from importlib.resources import files as _files

        return (_files(module) / path).read_bytes()
    except (TypeError, AttributeError, FileNotFoundError, ModuleNotFoundError):
        pass
    try:
        import pkgutil

        data = pkgutil.get_data(module, path)
        if data is not None:
            return data
    except Exception:
        pass
    if path.endswith(".py"):
        # Best-effort fallback — try to read .py sources from the APK.
        # May fail on Android 11+ where /data/app/ and /proc/self/maps
        # are restricted. Skill scripts are copied as raw assets via
        # Gradle; this fallback catches edge cases.
        try:
            import glob
            import zipfile

            apks = glob.glob("/data/app/*/base.apk")
            if not apks:
                with open("/proc/self/maps") as _f:
                    for _line in _f:
                        if "base.apk" in _line:
                            _parts = _line.split()
                            if len(_parts) > 5:
                                apks = [_parts[-1]]
                                break
            for _apk in apks:
                try:
                    with zipfile.ZipFile(_apk, "r") as _zf:
                        _asset_path = f"assets/skills/{path}"
                        try:
                            _info = _zf.getinfo(_asset_path)
                            return _zf.read(_info)
                        except KeyError:
                            continue
                except Exception:
                    continue
        except Exception:
            pass
        logger.debug("Could not read source for {} {}", module, path)
    return None


def _get_manifest(package: str) -> list[str] | None:
    if package == "jenny.templates":
        return _TEMPLATES_MANIFEST
    if package == "jenny.templates.ui":
        return _UI_MANIFEST
    if package == "jenny.skills":
        return _SKILLS_MANIFEST
    return None


def _write_bytes_force(target: Path, data: bytes) -> None:
    """Scrive *data* su *target*, sopravvivendo a un file mirror read-only.

    Il mirror ``workspace/ui`` non è più autoritativo (le UI attive sono servite
    dai byte impacchettati), ma la sync lo riscrive ad ogni boot. Se un file era
    stato reso read-only, ``write_bytes`` fallirebbe con ``PermissionError`` e
    manderebbe in crash-loop il gateway al boot: qui riportiamo il file
    scrivibile (o lo rimuoviamo, la dir è nostra) e riscriviamo.
    """
    try:
        target.write_bytes(data)
        return
    except PermissionError:
        pass
    try:
        target.chmod(0o644)
    except OSError:
        target.unlink(missing_ok=True)
    target.write_bytes(data)


def extract_package_dir(
    package: str,
    dest: Path,
    *,
    skip_existing: bool = False,
    only: list[str] | None = None,
) -> int:
    """Estrae i file di *package* in *dest*.

    ``only`` restringe l'estrazione a un sottoinsieme del manifest, e serve a
    chi deve applicare politiche diverse a gruppi di file dello stesso package
    (i prompt di sistema si riscrivono sempre, i file dell'utente mai). Resta un
    sottoinsieme *dichiarato*: una voce fuori dal manifest è un errore, non un
    file che appare in silenzio.
    """
    manifest = _get_manifest(package)
    if manifest is None:
        # Design a manifest esplicito (vedi .agent/gotchas.md): un package senza
        # manifest non deve mai finire in un walk silenzioso, che sul dispositivo
        # farebbe arrivare/mancare file senza traccia. Fallire esplicito.
        raise ValueError(
            f"no static manifest registered for package {package!r} "
            "(add it to _TEMPLATES_MANIFEST/_UI_MANIFEST/_SKILLS_MANIFEST)"
        )
    files = manifest
    if only is not None:
        unknown = sorted(set(only) - set(manifest))
        if unknown:
            raise ValueError(
                f"files not in the manifest for package {package!r}: {', '.join(unknown)}"
            )
        files = list(only)
    logger.debug("Using static manifest for {}", package)

    if not files:
        logger.warning("Manifest for package {} is empty", package)
        return 0

    count = 0
    for rel_path in files:
        if rel_path.endswith(".DS_Store") or "__pycache__" in rel_path or rel_path.endswith(".pyc"):
            continue
        target = dest / rel_path
        if skip_existing and target.exists():
            continue
        data = read_asset(package, rel_path)
        if data is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes_force(target, data)
            count += 1

    logger.info("Extracted {} files from {} to {}", count, package, dest)
    return count


def retire_withdrawn_templates(dest: Path) -> list[str]:
    """Riscrive i file dell'utente rimasti a una *nostra* versione ritirata.

    La terza politica della sync, e l'unica che scrive dentro un file
    dell'utente. Riconoscere una versione ritirata (che è quel che fa
    ``ContextBuilder._is_template_content``) tiene quel testo fuori dal prompt,
    ma lo lascia sul disco: basta che l'utente ci aggiunga una riga sua perché
    l'intero manuale ritirato diventi "scritto dall'utente", per sempre e senza
    etichetta. Il riconoscimento è al sicuro oggi ed è a un tasto dall'essere
    peggio di prima; l'unico modo di chiuderla è che quel testo se ne vada.

    La condizione è un digest **esatto** del testo strippato, e non è una
    prudenza: è ciò che rende impossibile per costruzione calpestare una riga
    dell'utente, ed è per questo che qui non serve né uno snapshot né un
    consenso. Non allargarla — niente match approssimati, niente "quasi uguale".

    Un file già allineato al bundle corrente non ha nulla da migrare e non viene
    toccato: nemmeno riscritto con gli stessi byte, perché una riscrittura
    identica è comunque una mtime nuova e una riga di log che mente.

    Ciò che il file **è** non lo cambia nessuna migrazione: i permessi
    sopravvivono alla riscrittura, e su un symlink il ritiro rinuncia. Il ritiro
    porta via del testo nostro; trasformare un link in un file regolare, o
    riaprire a tutti un file che l'utente teneva a 0600, sarebbe un secondo
    effetto che nessuno ha chiesto.
    """
    rewritten: list[str] = []
    for name, retired in _RETIRED_TEMPLATE_DIGESTS.items():
        target = dest / name
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # Assente, illeggibile o non testuale. Nessuno dei tre è un guasto
            # qui: il file assente lo crea l'estrazione subito dopo, e su un
            # file che non sappiamo leggere l'unica mossa sicura è non toccarlo.
            continue
        label = retired.get(template_digest(content))
        if label is None:
            continue
        if target.is_symlink():
            # ``atomic_write`` finisce in un ``os.replace`` sul path, quindi al
            # posto del link ci resterebbe un file regolare: l'utente perde il
            # collegamento che aveva scelto e le due copie divergono in silenzio.
            # Qui si rinuncia — il ritiro è un'ottimizzazione, il link è una
            # decisione esplicita — invece di risolverlo e scrivere dall'altra
            # parte, che farebbe scrivere questa funzione fuori dal workspace.
            logger.warning(
                "Not retiring {}: it is a symlink, and rewriting it would replace "
                "the link with a regular file; leaving the withdrawn {} copy in place",
                name, label,
            )
            continue
        data = read_asset("jenny.templates", name)
        if data is None:
            # Prima si legge, poi si scrive: senza i byte nuovi si lascia stare.
            # L'ordine è il motivo per cui non esiste un istante in cui il file
            # dell'utente non c'è.
            #
            # ``is None`` e non ``not data``, ed è la correzione del 2026-08-17.
            # Da 0.8.0 un template bundled vuoto è la **normalità**: ``AGENTS.md``,
            # ``USER.md`` e ``memory/MEMORY.md`` spediscono zero byte apposta (la
            # prosa che si spiegava da sola si pagava a ogni turno, non la leggeva
            # nessuno, e nel caso di ``MEMORY.md`` insegnava il contrario del
            # routing di ``agent/dream.md``). Con ``not data`` questo ramo si
            # prendeva tutti e tre i digest ritirati elencati sopra: la migrazione
            # non avveniva più, la copia con la prosa restava sul disco a un tasto
            # dal diventare "scritta dall'utente", e ogni boot di ogni
            # installazione stampava tre di questi warning per sempre.
            #
            # La distinzione che serviva esiste già nel tipo di ritorno:
            # ``read_asset`` dà ``None`` quando non è riuscita a leggere e ``b""``
            # per un asset che è davvero vuoto. Non serve un elenco di eccezioni.
            #
            # E il presidio contro la perdita di dati non era questa guardia: è il
            # digest **esatto** poche righe sopra, che ha già dimostrato che nel
            # file non c'è una riga dell'utente. Un file che l'utente avesse
            # riempito nel frattempo non combacerebbe con nessun digest e non
            # arriverebbe mai qui.
            logger.warning(
                "Cannot retire {}: the bundled template could not be read, "
                "leaving the withdrawn {} copy in place",
                name, label,
            )
            continue
        # ``atomic_write`` sostituisce il file con uno nuovo, e un file nuovo
        # nasce con il umask del processo: un ``AGENTS.md`` a 0600 tornerebbe
        # 0644, cioè il ritiro allargherebbe i permessi di un file dell'utente
        # senza che nessuno l'abbia chiesto. Stesso motivo per cui
        # ``config/store.py`` rimette il chmod a mano dopo ogni scrittura.
        previous_mode = stat.S_IMODE(target.stat().st_mode)
        atomic_write(target, data)
        with suppress(OSError):
            target.chmod(previous_mode)
        rewritten.append(name)
        # Su un dispositivo questa riga è l'unica traccia che la migrazione è
        # avvenuta, quindi nomina il file e *quale* versione è stata riconosciuta:
        # senza l'etichetta, un domani, non si saprebbe dire da dove veniva quel
        # workspace. Resta una riga di diagnostica — logcat non lo legge l'utente.
        logger.info(
            "Retired template {}: matched our withdrawn {} copy byte for byte, "
            "rewritten to the current bundled version (no user content to lose)",
            name, label,
        )
    return rewritten


_JENNY_SRC_KEY = "jenny_src"


def _find_apk_paths() -> list[str]:
    """Locate the installed APK (Android only). Mirrors read_asset's lookup."""
    import glob

    apks = glob.glob("/data/app/*/base.apk")
    if apks:
        return apks
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                if "base.apk" in line:
                    parts = line.split()
                    if len(parts) > 5:
                        return [parts[-1]]
    except OSError:
        pass
    return []


def extract_jenny_source(dest: Path) -> int:
    """Extract jenny's plain .py sources bundled as APK assets.

    Chaquopy compiles ``jenny/**`` into an .imy archive, so the readable
    sources are mirrored as raw assets under ``assets/jenny_src/`` by the
    Gradle ``copyPackageSourceAssets`` task. This extracts them to a real
    on-device directory so filesystem tools and get_source can read them.
    """
    import zipfile

    asset_prefix = "assets/jenny_src/"
    count = 0
    for apk in _find_apk_paths():
        try:
            with zipfile.ZipFile(apk, "r") as zf:
                for info in zf.infolist():
                    if not info.filename.startswith(asset_prefix) or info.is_dir():
                        continue
                    rel = info.filename[len(asset_prefix):]
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(info))
                    count += 1
        except Exception:
            continue
        if count:
            break
    if count:
        _extracted_registry[_JENNY_SRC_KEY] = dest
        logger.info("Extracted {} jenny source files to {}", count, dest)
    else:
        logger.debug("No jenny source assets found to extract")
    return count


def get_package_source_root() -> Path | None:
    """Return the directory holding jenny's readable .py sources, if any.

    On Android this is the directory populated by ``extract_jenny_source``;
    on desktop/dev it is the installed package directory itself. Returns None
    when no plain-source tree exists (e.g. packaged build before extraction).
    """
    extracted = _extracted_registry.get(_JENNY_SRC_KEY)
    if extracted is not None and extracted.exists():
        return extracted
    try:
        import jenny

        pkg_dir = Path(jenny.__file__).resolve().parent
    except Exception:
        return None
    if (pkg_dir / "__init__.py").is_file():
        return pkg_dir
    return None
