"""Le frasi che il server dice all'utente, in italiano e in inglese, in un posto solo.

Prima erano stringhe inglesi sparse fra runner, loop, comandi e notifiche, e in
un'interfaccia italiana arrivavano come corpi estranei («Stopped 1 task(s).»,
«Error calling LLM: ...»). Qui ogni frase ha una chiave e due stesure; chi la
dice chiama :func:`say` con la chiave e i campi.

Solo testo **per l'utente**: quel che legge il modello (prompt, segnaposto nella
cronologia, risultati dei tool) resta in inglese dove sta, perché il modello
imita la lingua di quel che legge.

La lingua è ``agents.defaults.language``, letta da ``config.json`` al momento
della frase (come fa ``telegram_api``): un cambio vale subito, senza riavvio.
Una lingua che la tabella non conosce ripiega sull'inglese; una chiave che non
c'è solleva ``KeyError`` — è un errore di programmazione, e un test lo trova.
"""

from __future__ import annotations

from loguru import logger

_FALLBACK_LANGUAGE = "en"

_TEXT: dict[str, dict[str, str]] = {
    "en": {
        # /stop
        "stop.done": "I stopped.",
        "stop.none": "There was nothing to stop.",
        "stop.helper_running": (
            "One helper keeps working in the background: stop it from its own bubble, "
            "or with /stop all."
        ),
        "stop.helpers_running": (
            "{count} helpers keep working in the background: stop them from their own "
            "bubbles, or with /stop all."
        ),
        # Errori del provider, uno per tipo: ognuno dice cosa fare.
        "error.connection": (
            "I can't reach the model. Check your connection, or the provider in "
            "Settings → Who answers."
        ),
        "error.timeout": (
            "The model didn't answer in time. Try again in a moment; if it keeps "
            "happening, try another model in Settings → Who answers."
        ),
        "error.auth": (
            "The provider rejected the key. Check it in Settings → Who answers."
        ),
        "error.rate_limit": (
            "The provider says too many requests were sent. Wait a minute and try again."
        ),
        "error.server": (
            "The provider is having trouble on its side. Try again in a few minutes."
        ),
        "error.credit": (
            "The provider refused the request: the key is out of credit or the account "
            "needs paying. Top it up on the provider's website and try again."
        ),
        "error.generic": (
            "The model answered with an error. Try again; if it keeps happening, check "
            "the provider in Settings → Who answers."
        ),
        "error.internal": "Something went wrong on my side. Try again.",
        # Fine turno senza una risposta vera.
        "turn.empty": (
            "I finished the steps but couldn't write an answer. Try again, or ask me "
            "for something smaller."
        ),
        "turn.truncated": (
            "I ran out of room for the answer before getting to the point. Ask me for "
            "a smaller piece at a time."
        ),
        "turn.max_iterations": (
            "I took too many steps ({max_iterations}) without finishing. Try splitting "
            "the request into smaller pieces."
        ),
        "turn.images_dropped": (
            "⚠️ I couldn't look at the attached images: the active model can't read images."
        ),
        # Notifiche di sistema: il tipo nel titolo, e il corpo di ripiego.
        "notify.reminder": "⏰ Reminder",
        "notify.monitor": "Monitoring",
        "notify.update": "Update",
        "notify.new_message": "New message",
        # Allarmi di sistema: dicono dove guardare nell'interfaccia.
        "alarm.gardener_label": "Gardener · {name}",
        "alarm.gardener_stuck": (
            "The gardener has failed {failures} passes in a row on the notebook "
            "'{name}' ({status}): its journal is not becoming pages. Open the notebook "
            "and ask me what's wrong."
        ),
        "alarm.check_label": "{name} · check",
        "alarm.check_silent": (
            "'{name}' has not completed its check for {runs} runs in a row. Ask me in "
            "chat what's wrong."
        ),
        "alarm.checks_silent": (
            "{count} checks in '{name}' have not completed for up to {runs} runs in a "
            "row. Ask me in chat what's wrong."
        ),
        "alarm.dream_label": "Memory",
        "alarm.dream_stuck": (
            "My memory is stuck: for {stuck} runs in a row nothing could be saved, "
            "because the memory files are full. Workshop → Memory shows the sizes."
        ),
        "alarm.update_critical": (
            "Security update {version} is available. Find it in Settings → Updates."
        ),
        # Le risposte dei comandi slash (``command/builtin.py``, ``command/scope.py``).
        "cmd.moved_to_settings": (
            "`{command}` does not take arguments any more: those settings live in "
            "**Settings → Workshop → Memory**, where they sit next to the numbers they "
            "act on.\n\n{still}"
        ),
        "cmd.new.done": "New session started.",
        "cmd.status.body": (
            "\U0001f408 jenny v{version}\n"
            "\U0001f9e0 Model: {model}\n"
            "\U0001f4ca Tokens: {prompt} in / {completion} out{cached}\n"
            "\U0001f4da Context: {used}/{total} ({pct}% of input budget)\n"
            "\U0001f4ac Session: {messages} messages\n"
            "⏱ Uptime: {uptime}\n"
            "⚡ Tasks: {tasks} active"
        ),
        "cmd.status.cached": " ({pct}% cached)",
        "cmd.model.none_configured": "(none configured)",
        "cmd.model.no_preset": "(none)",
        "cmd.model.status": (
            "## Model\n"
            "- Current model: `{model}`\n"
            "- Current preset: `{preset}`\n"
            "- Available presets: {presets}"
        ),
        "cmd.model.usage": "Usage: `/model [preset]`",
        "cmd.model.switch_failed": (
            "Could not switch model preset: {error}\n\nAvailable presets: {presets}"
        ),
        "cmd.model.switched": (
            "Switched model preset to `{preset}`.\n"
            "- Model: `{model}`\n"
            "- Context window: {window}"
        ),
        "cmd.model.max_output": "- Max output tokens: {max_tokens}",
        "cmd.dream.moved": "`/dream` on its own still runs a consolidation now.",
        "cmd.dream.started": "Dreaming...",
        # Dice il costo vero, token spesi due volte: l'archivio al confine del file
        # rende la seconda revisione costosa, non distruttiva, e un rifiuto che
        # spaventa più del dovuto è un rifiuto che mente.
        "cmd.dream.already_running": (
            "A Dream cycle is already running, so this one did not start — Dream runs one "
            "cycle at a time, or both take the same batch off the same cursor and each pays "
            "for its own review pass. Nothing would be lost, only spent twice. Try again once "
            "it has finished."
        ),
        "cmd.dream.done": "Dream completed in {elapsed}s.",
        "cmd.dream.held": (
            "Dream completed in {elapsed}s but consolidated nothing from its batch (no memory "
            "file grew); memory cursor was not advanced, so those entries come back next run."
        ),
        "cmd.dream.blocked": (
            "Dream completed in {elapsed}s but wrote nothing (attempts blocked/refused); "
            "memory cursor was not advanced."
        ),
        "cmd.dream.incomplete": (
            "Dream did not complete after {elapsed}s; memory cursor was not advanced."
        ),
        "cmd.dream.failed": "Dream failed after {elapsed}s: {error}",
        "cmd.dream.no_input": (
            "Dream has no conversation history to process yet.\n"
            "\n"
            "Dream reads new entries from `memory/history.jsonl` after the current Dream "
            "cursor.\n"
            "Short chats only reach that file after token compaction or idle auto-compact, "
            "so a fresh or short WebUI chat may leave Dream with no input.\n"
            "\n"
            "Next steps:\n"
            "- Enable `agents.defaults.idleCompactAfterMinutes` so completed chats become "
            "Dream input automatically.\n"
            "- Compact the current chat into memory once that manual action is available.\n"
            "- If you expected history to exist, check whether `memory/history.jsonl` has new "
            "entries after the Dream cursor."
        ),
        "cmd.dream.review_failed": (
            "A memory review pass ran first but did not complete cleanly; {freed} chars "
            "freed across {files}."
        ),
        "cmd.dream.review_freed": (
            "A memory review pass ran first and freed {freed} chars across {files}."
        ),
        "cmd.dream.review_nothing": (
            "A memory review pass ran first; nothing was freed across {files}."
        ),
        "cmd.dream.refused": (
            "{count} write(s) were refused by their size budget and never landed — "
            "**Settings → Workshop → Memory** shows which file is full, as a gauge against "
            "its cap: raise it or prune the file, then run `/dream` again."
        ),
        "cmd.dream.demoted": (
            "It also moved {count} fact(s) into `memory/archive/` instead of deleting them — "
            "ask me to recall {ids}{more} to read them back."
        ),
        "cmd.dream.demoted_more": ", and {more} more",
        "cmd.gardener.moved": (
            "A pass runs on the project you are in: open it and send `/gardener` there."
        ),
        "cmd.gardener.started": "Gardening {name}...",
        "cmd.gardener.not_a_project": (
            "`{name}` is not a project I can garden: I look for `{path}`, and there is "
            "nothing there."
        ),
        "cmd.gardener.error": "The gardener failed: {error}",
        "cmd.gardener.no_delta_board": "Nothing new in {name}'s journal for the pages. {board}",
        "cmd.gardener.no_board.research": (
            "{name} has no board, on purpose: its pages are in concepts/ and entities/ and it "
            "has no journal, so it counts as a research archive. Once the journal has a line, "
            "the next pass proposes a first board."
        ),
        "cmd.gardener.no_board.no_pages": "{name} has no board because it has no pages yet.",
        "cmd.gardener.no_board.proposed": (
            "A first board for {name} is waiting in the envelope for you to accept."
        ),
        "cmd.gardener.no_board.declined": (
            "{name} has no board because you turned down the first one proposed."
        ),
        "cmd.gardener.no_board.later": (
            "{name}'s first board was postponed; a later pass proposes it again."
        ),
        "cmd.gardener.no_board.next_pass": (
            "{name} has no board yet; a later pass proposes the first one."
        ),
        "cmd.gardener.board.done": (
            "Then it went over {name}'s board: anything it proposes, a first board included, "
            "is waiting in the envelope."
        ),
        "cmd.gardener.board.incomplete": (
            "Then it started on {name}'s board but did not finish; the next pass retries."
        ),
        "cmd.gardener.board.failed": (
            "Then the board pass over {name} failed; the next pass retries."
        ),
        "cmd.gardener.skipped_no_delta": (
            "Nothing new in {name}'s journal since the last pass, so there was nothing to "
            "promote — no tokens spent."
        ),
        "cmd.gardener.written": (
            "The gardener read {lines} journal lines in {name} and wrote {writes} times, in "
            "{elapsed}. See `wiki/index.md` and today's `log/`."
        ),
        "cmd.gardener.nothing_to_promote": (
            "The gardener read {lines} journal lines in {name} in {elapsed} and judged that "
            "none of them earned a page. The journal is marked as read."
        ),
        "cmd.gardener.partial_write": (
            "The gardener wrote {writes} pages in {name} in {elapsed}, but some writes were "
            "refused, so the journal was left unread — the next pass will see those lines "
            "again."
        ),
        "cmd.gardener.commit_failed": (
            "The gardener wrote {writes} pages in {name} in {elapsed}, but could not record "
            "how far it had read: {detail}. The pages are on disk and the journal was left "
            "unread, so the next pass will see those lines again."
        ),
        "cmd.gardener.aborted_user_active": (
            "You came back to {name} while the gardener was working there, so it stood down "
            "after {elapsed} rather than write over you ({writes} pages had already landed). "
            "The journal was left unread, so the next pass will see those lines again."
        ),
        "cmd.gardener.already_running": (
            "A pass on {name} is already running, so this one did not start — the gardener "
            "works on one project one pass at a time, or two passes overwrite each other's "
            "pages. Try again once it has finished."
        ),
        "cmd.gardener.no_write": (
            "The gardener finished in {elapsed} without writing (attempts blocked or "
            "refused); the journal was left unread, so the next pass will try again."
        ),
        "cmd.gardener.incomplete": (
            "The gardener did not finish after {elapsed}; nothing was changed."
        ),
        "cmd.gardener.failed": "The gardener failed after {elapsed}: {detail}",
        "cmd.gardener.map_lede": (
            "Nothing new in {name}'s journal, so the gardener went in for the map alone"
        ),
        "cmd.gardener.map_moved": (
            "`wiki/index.md` went from {before} to {after} characters in {elapsed}, against "
            "a ceiling of {ceiling}"
        ),
        "cmd.gardener.map_fits": (
            "{lede}: {moved} — it fits now, so every turn sees all of it again."
        ),
        "cmd.gardener.map_still_over": (
            "{lede}: {moved} — still over, and it will not be tried again until the map "
            "grows past that."
        ),
        "cmd.gardener.map_unchanged": (
            "{lede}, and after {elapsed} it is still {after} characters against a ceiling of "
            "{ceiling}: nothing was moved out of it. It will not be tried again until the map "
            "grows past that."
        ),
        "cmd.history.you": "\U0001f464 You",
        "cmd.history.bot": "\U0001f916 Bot",
        "cmd.history.usage": "Usage: /history [count] — e.g. /history 5 (default: 10, max: 50)",
        "cmd.history.empty": "No conversation history yet.",
        "cmd.history.header": "Last {count} message(s):",
        "cmd.goal.usage": "Usage: /goal <long-running task description>",
        "cmd.goal.busy": (
            "A task is already running for this chat. Use `/stop` first, then send "
            "`/goal <long-running task description>` again."
        ),
        "cmd.skill.none": "No skills available.",
        "cmd.skill.header": "Available skills ({count}):",
        "cmd.help.header": "✿ jenny commands:",
        "cmd.unavailable": "`{command}` is unavailable: its prompt template could not be loaded.",
        "cmd.refusal.project": (
            "`{command}` works on one project, and this conversation is not a project.\n\n"
            "Open the project — the chip above the composer does it — and send `{command}` "
            "there."
        ),
        "cmd.refusal.personal": (
            "`{command}` does not act on this project, so it does nothing here.\n\n"
            "Send it from the personal chat — the chip above the composer switches back."
        ),
        "cmd.help.new": "Stop the current task and start a fresh conversation.",
        "cmd.help.stop": (
            "Cancel the active agent turn for this chat. Helpers working in the background keep "
            "going: stop each from its own bubble, or send /stop all to stop everything."
        ),
        "cmd.help.status": "Display runtime, provider, and channel status.",
        "cmd.help.model": "Show or switch the active model preset.",
        "cmd.help.history": "Print the last N persisted conversation messages.",
        "cmd.help.goal": "Tell the agent to treat the request as a long-running goal.",
        "cmd.help.dream": (
            "Manually trigger memory consolidation now. The budgets and the review cadence live "
            "in Settings → Workshop → Memory."
        ),
        "cmd.help.gardener": (
            "Inside a project: turn its new journal lines into pages and update its map — or, "
            "with nothing new to promote, bring an oversized map back under its ceiling. The "
            "periodic pass is set in Settings → Workshop → Memory."
        ),
        "cmd.help.skill": "List enabled skills and their descriptions.",
        "cmd.help.tidy": (
            "Inside a project: restructure its wiki now — split what has outgrown the per-turn "
            "budget, move prose out of an oversized map, realign the page list. Runs in this "
            "conversation, with its pages and your answers in hand; the gardener's periodic pass "
            "cannot do that."
        ),
        "cmd.help.init": "Inside a project: read the wiki and write its AGENTS.md.",
        "cmd.help.help": "List available slash commands.",
        "cmd.scope_note.model": (
            "It switches the preset for the whole installation, not for one project."
        ),
        "cmd.scope_note.dream": (
            "It consolidates the personal memory. A project conversation feeds it only with what "
            "it says about you, and only into USER.md: the rest stays in that project's pages."
        ),
        "cmd.scope_note.gardener": "The periodic pass is set in Settings → Workshop → Memory.",
        "cmd.scope_note.skill": "It lists what the installation has, not what this project holds.",
    },
    "it": {
        "stop.done": "Mi sono fermata.",
        "stop.none": "Non stavo facendo niente da fermare.",
        "stop.helper_running": (
            "Un aiutante continua a lavorare in background: lo fermi dalla sua bolla, "
            "o con /stop all."
        ),
        "stop.helpers_running": (
            "{count} aiutanti continuano a lavorare in background: li fermi dalla loro "
            "bolla, o con /stop all."
        ),
        "error.connection": (
            "Non riesco a raggiungere il modello. Controlla la connessione, o il "
            "provider in Impostazioni → Chi risponde."
        ),
        "error.timeout": (
            "Il modello non ha risposto in tempo. Riprova fra poco; se succede spesso, "
            "prova un altro modello in Impostazioni → Chi risponde."
        ),
        "error.auth": (
            "Il provider ha rifiutato la chiave. Controllala in Impostazioni → Chi risponde."
        ),
        "error.rate_limit": (
            "Il provider dice che sono arrivate troppe richieste. Aspetta un minuto e riprova."
        ),
        "error.server": (
            "Il provider ha un problema dalla sua parte. Riprova fra qualche minuto."
        ),
        "error.credit": (
            "Il provider ha rifiutato la richiesta: la chiave ha finito il credito o il "
            "conto è da saldare. Ricaricala dal sito del provider e riprova."
        ),
        "error.generic": (
            "Il modello ha risposto con un errore. Riprova; se continua, controlla il "
            "provider in Impostazioni → Chi risponde."
        ),
        "error.internal": "Qualcosa è andato storto da parte mia. Riprova.",
        "turn.empty": (
            "Ho finito i passaggi ma non sono riuscita a scrivere una risposta. Riprova, "
            "o chiedimi una cosa più piccola."
        ),
        "turn.truncated": (
            "Ho finito lo spazio per la risposta prima di arrivare al punto. Chiedimi un "
            "pezzo più piccolo alla volta."
        ),
        "turn.max_iterations": (
            "Ho fatto troppi passaggi ({max_iterations}) senza finire. Prova a dividere "
            "la richiesta in pezzi più piccoli."
        ),
        "turn.images_dropped": (
            "⚠️ Non ho potuto guardare le immagini allegate: il modello attivo non legge "
            "immagini."
        ),
        "notify.reminder": "⏰ Promemoria",
        "notify.monitor": "Monitoraggio",
        "notify.update": "Aggiornamento",
        "notify.new_message": "Nuovo messaggio",
        "alarm.gardener_label": "Giardiniere · {name}",
        "alarm.gardener_stuck": (
            "Il giardiniere non riesce a sistemare il quaderno «{name}» da {failures} "
            "passate di fila ({status}): il suo diario non diventa pagine. Apri il "
            "quaderno e chiedimi cosa non va."
        ),
        "alarm.check_label": "{name} · controllo",
        "alarm.check_silent": (
            "«{name}» non riesce a fare il suo controllo da {runs} volte di fila. "
            "Chiedimi in chat cosa non va."
        ),
        "alarm.checks_silent": (
            "{count} controlli di «{name}» non riescono da fino a {runs} volte di fila. "
            "Chiedimi in chat cosa non va."
        ),
        "alarm.dream_label": "Memoria",
        "alarm.dream_stuck": (
            "La mia memoria è ferma: da {stuck} passate di fila non riesco a salvare "
            "niente, perché i file di memoria sono pieni. Officina → Memoria mostra le "
            "dimensioni."
        ),
        "alarm.update_critical": (
            "È disponibile l'aggiornamento di sicurezza {version}. Lo trovi in "
            "Impostazioni → Aggiornamenti."
        ),
        # Le risposte dei comandi slash (``command/builtin.py``, ``command/scope.py``).
        "cmd.moved_to_settings": (
            "`{command}` non accetta più argomenti: quelle impostazioni stanno in "
            "**Impostazioni → Officina → Memoria**, accanto ai numeri su cui agiscono."
            "\n\n{still}"
        ),
        "cmd.new.done": "Nuova sessione iniziata.",
        "cmd.status.body": (
            "\U0001f408 jenny v{version}\n"
            "\U0001f9e0 Modello: {model}\n"
            "\U0001f4ca Token: {prompt} in entrata / {completion} in uscita{cached}\n"
            "\U0001f4da Contesto: {used}/{total} ({pct}% del budget di input)\n"
            "\U0001f4ac Sessione: {messages} messaggi\n"
            "⏱ Attiva da: {uptime}\n"
            "⚡ Lavori: {tasks} in corso"
        ),
        "cmd.status.cached": " ({pct}% dalla cache)",
        "cmd.model.none_configured": "(nessuno configurato)",
        "cmd.model.no_preset": "(nessuno)",
        "cmd.model.status": (
            "## Modello\n"
            "- Modello attuale: `{model}`\n"
            "- Preset attuale: `{preset}`\n"
            "- Preset disponibili: {presets}"
        ),
        "cmd.model.usage": "Uso: `/model [preset]`",
        "cmd.model.switch_failed": (
            "Non sono riuscita a cambiare preset: {error}\n\nPreset disponibili: {presets}"
        ),
        "cmd.model.switched": (
            "Preset cambiato in `{preset}`.\n"
            "- Modello: `{model}`\n"
            "- Finestra di contesto: {window}"
        ),
        "cmd.model.max_output": "- Token massimi in uscita: {max_tokens}",
        "cmd.dream.moved": "`/dream` da solo fa ancora partire un riordino della memoria.",
        "cmd.dream.started": "Riordino la memoria...",
        "cmd.dream.already_running": (
            "Sto già riordinando la memoria, quindi questo giro non è partito: Dream fa un "
            "giro alla volta, altrimenti due giri prenderebbero lo stesso blocco e pagherebbero "
            "due revisioni. Non si perderebbe niente, si spenderebbe solo due volte. Riprova "
            "quando ha finito."
        ),
        "cmd.dream.done": "Memoria riordinata in {elapsed} s.",
        "cmd.dream.held": (
            "Dream ha finito in {elapsed} s ma non ha consolidato niente del suo blocco "
            "(nessun file di memoria è cresciuto); il cursore è rimasto dov'era, quindi "
            "quelle voci tornano al prossimo giro."
        ),
        "cmd.dream.blocked": (
            "Dream ha finito in {elapsed} s ma non ha scritto niente (tentativi bloccati o "
            "rifiutati); il cursore è rimasto dov'era."
        ),
        "cmd.dream.incomplete": (
            "Dream non ha finito dopo {elapsed} s; il cursore è rimasto dov'era."
        ),
        "cmd.dream.failed": "Dream non è riuscito dopo {elapsed} s: {error}",
        "cmd.dream.no_input": (
            "Dream non ha ancora conversazioni da elaborare.\n"
            "\n"
            "Dream legge le voci nuove di `memory/history.jsonl` dopo il suo cursore.\n"
            "Una chat breve ci arriva solo dopo una compattazione, per lunghezza o per "
            "inattività, quindi una chat appena iniziata può lasciare Dream senza niente da "
            "leggere.\n"
            "\n"
            "Cosa fare:\n"
            "- Attiva `agents.defaults.idleCompactAfterMinutes`, così le chat concluse "
            "diventano materiale per Dream da sole.\n"
            "- Compatta la chat in memoria quando quel comando sarà disponibile.\n"
            "- Se ti aspettavi delle voci, controlla se `memory/history.jsonl` ne ha di nuove "
            "dopo il cursore di Dream."
        ),
        "cmd.dream.review_failed": (
            "Prima è partita una revisione della memoria, ma non si è chiusa bene; liberati "
            "{freed} caratteri fra {files}."
        ),
        "cmd.dream.review_freed": (
            "Prima è partita una revisione della memoria: liberati {freed} caratteri fra "
            "{files}."
        ),
        "cmd.dream.review_nothing": (
            "Prima è partita una revisione della memoria; non ha liberato niente fra {files}."
        ),
        "cmd.dream.refused": (
            "{count} scritture rifiutate dal limite di dimensione non sono mai arrivate nei "
            "file: **Impostazioni → Officina → Memoria** mostra quale file è pieno rispetto "
            "al suo limite. Alzalo o sfoltisci il file, poi rilancia `/dream`."
        ),
        "cmd.dream.demoted": (
            "Ha anche spostato {count} fatti in `memory/archive/` invece di cancellarli: "
            "chiedimi di richiamare {ids}{more} per rileggerli."
        ),
        "cmd.dream.demoted_more": " e altri {more}",
        "cmd.gardener.moved": (
            "Una passata lavora sul quaderno in cui sei: aprilo e manda `/gardener` da lì."
        ),
        "cmd.gardener.started": "Curo {name}...",
        "cmd.gardener.not_a_project": (
            "`{name}` non è un quaderno che posso curare: cerco `{path}`, e lì non c'è niente."
        ),
        "cmd.gardener.error": "Il giardiniere non è riuscito: {error}",
        "cmd.gardener.no_delta_board": (
            "Niente di nuovo nel diario di {name} per le pagine. {board}"
        ),
        "cmd.gardener.no_board.research": (
            "{name} non ha una bacheca, di proposito: le sue pagine stanno in concepts/ ed "
            "entities/ e non ha un diario, quindi vale come archivio di ricerca. Quando il "
            "diario avrà una riga, la passata successiva proporrà una prima bacheca."
        ),
        "cmd.gardener.no_board.no_pages": "{name} non ha una bacheca perché non ha ancora pagine.",
        "cmd.gardener.no_board.proposed": (
            "Una prima bacheca per {name} ti aspetta nella busta, da accettare."
        ),
        "cmd.gardener.no_board.declined": (
            "{name} non ha una bacheca perché hai rifiutato la prima proposta."
        ),
        "cmd.gardener.no_board.later": (
            "La prima bacheca di {name} è stata rimandata; una passata più avanti la "
            "riproporrà."
        ),
        "cmd.gardener.no_board.next_pass": (
            "{name} non ha ancora una bacheca; una passata più avanti proporrà la prima."
        ),
        "cmd.gardener.board.done": (
            "Poi è passato sulla bacheca di {name}: quello che propone, una prima bacheca "
            "compresa, ti aspetta nella busta."
        ),
        "cmd.gardener.board.incomplete": (
            "Poi ha iniziato la bacheca di {name} ma non ha finito; la prossima passata "
            "riprova."
        ),
        "cmd.gardener.board.failed": (
            "Poi la passata sulla bacheca di {name} non è riuscita; la prossima riprova."
        ),
        "cmd.gardener.skipped_no_delta": (
            "Niente di nuovo nel diario di {name} dall'ultima passata, quindi non c'era "
            "niente da trasformare in pagine: nessun token speso."
        ),
        "cmd.gardener.written": (
            "Il giardiniere ha letto {lines} righe del diario di {name} e ha scritto {writes} "
            "volte, in {elapsed}. Guarda `wiki/index.md` e il `log/` di oggi."
        ),
        "cmd.gardener.nothing_to_promote": (
            "Il giardiniere ha letto {lines} righe del diario di {name} in {elapsed} e ha "
            "giudicato che nessuna meritasse una pagina. Il diario è segnato come letto."
        ),
        "cmd.gardener.partial_write": (
            "Il giardiniere ha scritto {writes} pagine in {name} in {elapsed}, ma alcune "
            "scritture sono state rifiutate, quindi il diario è rimasto da leggere: la "
            "prossima passata rivedrà quelle righe."
        ),
        "cmd.gardener.commit_failed": (
            "Il giardiniere ha scritto {writes} pagine in {name} in {elapsed}, ma non è "
            "riuscito a segnare fin dove aveva letto: {detail}. Le pagine sono sul disco e il "
            "diario è rimasto da leggere, quindi la prossima passata rivedrà quelle righe."
        ),
        "cmd.gardener.aborted_user_active": (
            "Hai riaperto {name} mentre il giardiniere ci lavorava, quindi si è fermato "
            "dopo {elapsed} per non scriverti sopra ({writes} pagine erano già arrivate). Il "
            "diario è rimasto da leggere, quindi la prossima passata rivedrà quelle righe."
        ),
        "cmd.gardener.already_running": (
            "Una passata su {name} è già in corso, quindi questa non è partita: il "
            "giardiniere fa una passata alla volta per quaderno, altrimenti due passate si "
            "sovrascriverebbero le pagine. Riprova quando ha finito."
        ),
        "cmd.gardener.no_write": (
            "Il giardiniere ha finito in {elapsed} senza scrivere (tentativi bloccati o "
            "rifiutati); il diario è rimasto da leggere, quindi la prossima passata riprova."
        ),
        "cmd.gardener.incomplete": (
            "Il giardiniere non ha finito dopo {elapsed}; non è cambiato niente."
        ),
        "cmd.gardener.failed": "Il giardiniere non è riuscito dopo {elapsed}: {detail}",
        "cmd.gardener.map_lede": (
            "Niente di nuovo nel diario di {name}, quindi il giardiniere è passato solo per "
            "la mappa"
        ),
        "cmd.gardener.map_moved": (
            "`wiki/index.md` è passato da {before} a {after} caratteri in {elapsed}, con un "
            "tetto di {ceiling}"
        ),
        "cmd.gardener.map_fits": (
            "{lede}: {moved}. Ora ci sta, quindi ogni turno la vede di nuovo intera."
        ),
        "cmd.gardener.map_still_over": (
            "{lede}: {moved}. È ancora sopra, e non si riprova finché la mappa non cresce "
            "oltre questa misura."
        ),
        "cmd.gardener.map_unchanged": (
            "{lede}, e dopo {elapsed} è ancora di {after} caratteri con un tetto di "
            "{ceiling}: non è stato spostato niente. Non si riprova finché la mappa non "
            "cresce oltre questa misura."
        ),
        "cmd.history.you": "\U0001f464 Tu",
        "cmd.history.bot": "\U0001f916 Bot",
        "cmd.history.usage": (
            "Uso: /history [numero], per esempio /history 5 (predefinito: 10, massimo: 50)"
        ),
        "cmd.history.empty": "Non c'è ancora una cronologia.",
        "cmd.history.header": "Ultimi {count} messaggi:",
        "cmd.goal.usage": "Uso: /goal <descrizione del lavoro lungo>",
        "cmd.goal.busy": (
            "Sto già lavorando a qualcosa in questa chat. Usa prima `/stop`, poi rimanda "
            "`/goal <descrizione del lavoro lungo>`."
        ),
        "cmd.skill.none": "Nessuna skill disponibile.",
        "cmd.skill.header": "Skill disponibili ({count}):",
        "cmd.help.header": "✿ comandi di jenny:",
        "cmd.unavailable": "`{command}` non è disponibile: non riesco a caricare il suo prompt.",
        "cmd.refusal.project": (
            "`{command}` lavora su un quaderno, e questa conversazione non è un quaderno.\n\n"
            "Apri il quaderno, con il chip sopra la casella di testo, e manda `{command}` "
            "da lì."
        ),
        "cmd.refusal.personal": (
            "`{command}` non agisce su questo quaderno, quindi qui non fa niente.\n\n"
            "Mandalo dalla chat personale: il chip sopra la casella di testo ti riporta lì."
        ),
        "cmd.help.new": "Ferma il lavoro in corso e inizia una conversazione nuova.",
        "cmd.help.stop": (
            "Ferma la risposta in corso in questa chat. Gli aiutanti che lavorano in "
            "background continuano: fermali dalla loro bolla, o manda /stop all per fermare "
            "tutto."
        ),
        "cmd.help.status": "Mostra lo stato di runtime, provider e canali.",
        "cmd.help.model": "Mostra o cambia il preset del modello attivo.",
        "cmd.help.history": "Stampa gli ultimi N messaggi salvati della conversazione.",
        "cmd.help.goal": "Chiede all'agente di trattare la richiesta come un obiettivo lungo.",
        "cmd.help.dream": (
            "Fa partire subito il riordino della memoria. I limiti e la cadenza della "
            "revisione stanno in Impostazioni → Officina → Memoria."
        ),
        "cmd.help.gardener": (
            "Dentro un quaderno: trasforma le righe nuove del diario in pagine e aggiorna la "
            "mappa, oppure, senza niente di nuovo, riporta sotto il tetto una mappa troppo "
            "grande. La passata periodica si regola in Impostazioni → Officina → Memoria."
        ),
        "cmd.help.skill": "Elenca le skill attive e le loro descrizioni.",
        "cmd.help.tidy": (
            "Dentro un quaderno: riordina subito la wiki, dividendo quel che ha superato il "
            "budget per turno, togliendo prosa da una mappa troppo grande e riallineando "
            "l'elenco delle pagine. Lavora in questa conversazione, con le pagine e le tue "
            "risposte a portata di mano, cosa che la passata periodica del giardiniere non "
            "può fare."
        ),
        "cmd.help.init": "Dentro un quaderno: legge la wiki e scrive il suo AGENTS.md.",
        "cmd.help.help": "Elenca i comandi slash disponibili.",
        "cmd.scope_note.model": (
            "Cambia il preset per tutta l'installazione, non per un quaderno."
        ),
        "cmd.scope_note.dream": (
            "Riordina la memoria personale. Una conversazione di quaderno la alimenta solo "
            "con quel che dice di te, e solo in USER.md: il resto resta nelle pagine del "
            "quaderno."
        ),
        "cmd.scope_note.gardener": (
            "La passata periodica si regola in Impostazioni → Officina → Memoria."
        ),
        "cmd.scope_note.skill": (
            "Elenca quel che ha l'installazione, non quel che contiene questo quaderno."
        ),
    },
}

# I tipi d'errore del provider che hanno una frase propria (``error.<tipo>``).
ERROR_KINDS: frozenset[str] = frozenset(
    key.split(".", 1)[1]
    for key in _TEXT[_FALLBACK_LANGUAGE]
    if key.startswith("error.") and key != "error.internal"
)


def current_language() -> str:
    """La lingua dell'agente adesso, da ``config.json``; ``en`` se non si legge."""
    return _language_from_config()


def _language_from_config() -> str:
    """La lettura vera di :func:`current_language` (i test fissano quella).

    ``load_config`` e non una copia in memoria: la lingua la scrive la WebUI
    (onboarding) passando da ``store.mutate``, e nessun oggetto vivo la segue.
    È la stessa lettura che altri moduli fanno più volte per turno; qui serve
    solo quando c'è una frase da dire.
    """
    try:
        from jenny.config.loader import load_config

        return (load_config().agents.defaults.language or "").strip().lower()
    except Exception:  # noqa: BLE001 — una frase non deve far fallire chi la dice
        logger.opt(exception=True).debug("user_text: language not readable, using English")
        return _FALLBACK_LANGUAGE


DEFAULT_BOT_NAME = "Jenny"


def current_bot_name() -> str:
    """Il nome che l'utente ha dato all'agente (``agents.defaults.bot_name``), adesso.

    Letto da ``config.json`` a ogni chiamata come :func:`current_language`: la
    WebUI lo cambia da Impostazioni e vale dal turno dopo, senza riavvio.
    ``Jenny`` se manca, è vuoto o non si legge.
    """
    try:
        from jenny.config.loader import load_config

        name = (load_config().agents.defaults.bot_name or "").strip()
    except Exception:  # noqa: BLE001 — il nome è una cortesia, non un requisito
        logger.opt(exception=True).debug("user_text: bot name not readable")
        return DEFAULT_BOT_NAME
    return name or DEFAULT_BOT_NAME


def say(key: str, language: str | None = None, **fields: object) -> str:
    """La frase *key* nella lingua *language* (``None`` = quella configurata).

    Lingua sconosciuta → inglese. Chiave sconosciuta → ``KeyError``, anche se la
    lingua ce l'avrebbe: le due tabelle devono avere le stesse chiavi (lo prova
    un test), e l'inglese è il riferimento.
    """
    english = _TEXT[_FALLBACK_LANGUAGE][key]
    lang = (language if language is not None else current_language()).strip().lower()
    template = _TEXT.get(lang, {}).get(key, english)
    return template.format(**fields) if fields else template


def languages() -> tuple[str, ...]:
    """Le lingue che la tabella conosce (per i test)."""
    return tuple(_TEXT)


def keys(language: str = _FALLBACK_LANGUAGE) -> frozenset[str]:
    """Le chiavi di *language* (per i test)."""
    return frozenset(_TEXT[language])
