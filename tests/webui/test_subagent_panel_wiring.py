"""Il pannello subagent è cablato alla policy, e non ha modi di aggirarla.

``test_subagent_panel_policy.py`` esegue le regole; qui si controlla che
``mobile-chat.js`` le *usi* — un pannello che ricalcolasse a mano quali card
mostrare passerebbe quei test e regredirebbe comunque. Sono asserzioni sul
sorgente, nello stile di ``test_mascot_size_contract.py``: la WebUI non ha un
runner JS con DOM, e queste poche righe coprono proprio i punti che sul device
sono già andati storti una volta.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support import css_levels

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
CHAT_JS = UI_DIR / "assets" / "mobile-chat.js"
DIALOG_JS = UI_DIR / "assets" / "shared" / "dialog.js"
OFFICINA_HTML = UI_DIR / "officina.html"
CSS = UI_DIR / "assets" / "mobile-style.css"


def _chat() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def test_panel_imports_the_policy_instead_of_reimplementing_it() -> None:
    source = _chat()
    assert "from './shared/subagent-policy.js'" in source
    assert "saVisibleCards(" in source, "il filtro delle card passa dalla policy"
    assert "saActions(" in source, "i bottoni passano dalla matrice"


def test_turn_end_drops_the_terminated_cards() -> None:
    """L'aggancio della regola 'una card terminale lingera per un turno solo'."""
    source = _chat()
    handler = re.search(r"_handleTurnEnd\(latencyMs\)\s*\{(.*?)\n  \}", source, re.S)
    assert handler, "_handleTurnEnd non trovato"
    assert "_dropTerminatedSubagents()" in handler.group(1)
    drop = re.search(r"_dropTerminatedSubagents\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert drop, "_dropTerminatedSubagents non trovato"
    body = drop.group(1)
    # Azzerare *e* ri-renderizzare: senza il secondo, le card restano a schermo
    # fino al prossimo snapshot, che a turno finito potrebbe non arrivare mai.
    assert "_subagentLiveIds = new Set()" in body
    assert "_renderSubagents(" in body


def test_the_expiry_has_a_wakeup_of_its_own() -> None:
    """La scadenza di una card terminale va *applicata*, non solo calcolata.

    A zero running il poll è spento per scelta, quindi l'unico evento che resta
    è un timer: senza di lui una card scaduta se ne sta a schermo fino al
    prossimo frame, che su un install guidato dai cron può essere fra ore — che
    è esattamente il difetto che la scadenza doveva chiudere.
    """
    source = _chat()
    render = re.search(r"_renderSubagents\(snapshot\)\s*\{(.*?)\n  \}", source, re.S)
    assert render, "_renderSubagents non trovato"
    assert "_scheduleSubagentExpiry(view.nextExpiryMs)" in render.group(1)
    sched = re.search(r"_scheduleSubagentExpiry\(nextExpiryMs\)\s*\{(.*?)\n  \}", source, re.S)
    assert sched, "_scheduleSubagentExpiry non trovato"
    body = sched.group(1)
    # Un timer solo, riarmato: due sveglie sovrapposte renderizzano due volte.
    assert "clearTimeout(this._subagentExpiryTimer)" in body
    assert "setTimeout(" in body
    # Ri-render dello snapshot che si ha già: la card deve cadere anche offline.
    assert "_renderSubagents(this._subagentSnapshot)" in body
    assert "api." not in body and "fetch(" not in body


def test_the_panel_is_hidden_when_there_are_no_cards() -> None:
    """Zero card = elemento `hidden`, non pannello collassato."""
    source = _chat()
    render = re.search(r"_renderSubagents\(snapshot\)\s*\{(.*?)\n  \}", source, re.S)
    assert render, "_renderSubagents non trovato"
    body = render.group(1)
    assert "const total = running.length + lingering.length;" in body
    assert "this.subagentsEl.hidden = total === 0;" in body
    # `recent` grezzo non decide più nulla della visibilità: solo i lingering.
    assert "recent.length === 0" not in body


def test_stop_on_a_card_does_not_also_open_the_detail() -> None:
    """Regola 3: un tap su Stop è un'azione, non una richiesta di dettaglio."""
    setup = re.search(r"_setupSubagentPanel\(\)\s*\{(.*?)\n  \}", _chat(), re.S)
    assert setup, "_setupSubagentPanel non trovato"
    body = setup.group(1)
    btn_branch = body.split("closest('.sa-btn')", 1)[1]
    stop_prop = btn_branch.index("e.stopPropagation()")
    open_detail = btn_branch.index("_openSubagentDetail")
    assert stop_prop < open_detail, (
        "stopPropagation deve stare nel ramo del bottone, prima del ramo che apre la modale"
    )
    assert "closest('.sa-row')" in btn_branch


def test_the_detail_modal_reuses_the_shared_dialog() -> None:
    assert "from './shared/dialog.js'" in _chat()
    assert "detailDialog(" in _chat()
    dialog = DIALOG_JS.read_text(encoding="utf-8")
    assert "export function detailDialog(" in dialog
    # Tutte le vie d'uscita che un utente di telefono si aspetta: X, backdrop,
    # Esc e gesto Indietro (gli ultimi due via l'evento `cancel` di <dialog>).
    assert "showModal()" in dialog
    assert "'cancel'" in dialog
    assert "oc-detail-close" in dialog
    assert "e.target === dialog" in dialog, "manca la chiusura al tap sul backdrop"
    # Il markup non sta più in officina.html: se lo porta il modulo, che lo
    # monta all'import — i gusci che usano questi dialoghi sono due.
    for node_id in ("oc-detail-dialog", "oc-detail-title", "oc-detail-body",
                    "oc-detail-actions", "oc-detail-close"):
        assert f'id="{node_id}"' in dialog, node_id
    assert 'id="oc-detail-dialog"' not in OFFICINA_HTML.read_text(encoding="utf-8"), (
        "due copie dello stesso markup: la seconda è quella che resterà indietro"
    )


def test_the_detail_modal_is_a_sheet_like_every_other_detail_surface() -> None:
    """Piena larghezza e ancorata in basso, come gli altri dettagli della UI.

    Era nata come variante del confirm — 92vw sospesi in mezzo allo schermo — ed
    era la sola superficie di dettaglio della UI a non essere un `.oc-sheet`. Il
    margine per lato lo pagavano le righe di attività, che sono monospazio.
    """
    html = DIALOG_JS.read_text(encoding="utf-8")
    dialog = re.search(r'<dialog[^>]*id="oc-detail-dialog"[^>]*>', html)
    assert dialog, "il <dialog> del dettaglio non è stato trovato"
    classes = dialog.group(0)
    assert "oc-sheet" in classes, "il dettaglio non usa il guscio sheet condiviso"
    assert "oc-dialog" not in classes, "il dettaglio è tornato una card centrata"
    assert "oc-sheet-inner" in html.split('id="oc-detail-dialog"')[1][:400]

    css = CSS.read_text(encoding="utf-8")
    rule = re.search(r"\n\.oc-detail \{(.*?)\}", css, re.S)
    assert rule, "la regola .oc-detail non è stata trovata"
    body = rule.group(1)
    # Nessuna larghezza propria: quella la dà `.oc-sheet` (100%).
    assert "width" not in body, "il dettaglio si ridà una larghezza tutta sua"
    # Il tetto in altezza invece deve restare qui: `.oc-sheet` non ne ha, e senza
    # tetto un flusso che cresce spinge la X fuori dallo schermo.
    assert re.search(r"max-height:\s*\d+vh", body), "manca il tetto in vh"


def _detail_source() -> str:
    """Le funzioni che compongono il corpo della modale, concatenate.

    Il corpo è diviso per *vita* del contenuto (riepilogo che invecchia, esito che
    compare a fine lavoro, pieghe che l'utente apre), non per campo: un campo si
    cerca in tutte.
    """
    source = _chat()
    parts = []
    for name in ("_saSumHtml", "_saFocusView", "_saFoldsHtml", "_saDiagRowsHtml",
                 "_saApplyCapNote", "_saSnapshotEntries"):
        found = re.search(name + r"\([^)]*\)\s*\{(.*?)\n  \}", source, re.S)
        assert found, f"{name} non trovato"
        parts.append(found.group(1))
    return "\n".join(parts)


def test_the_modal_shows_what_the_card_cannot() -> None:
    source = _chat()
    body = _detail_source()
    for field in ("agent_type", "lineage_id", "elapsed_s", "idle_s", "phase",
                  "iteration", "entry.task", "tool_events", "result_summary",
                  "entry.error"):
        assert field in body, f"la modale non mostra {field}"
    # La spiegazione del perché can_restart è falso vive qui, non sulla card.
    assert "can_restart === false" in body
    assert "subagents.autoCapReached" in body
    card = re.search(r"_subagentRecentRow\(entry\)\s*\{(.*?)\n  \}", source, re.S)
    assert card and "autoCapReached" not in card.group(1), (
        "la nota sul tetto dei rilanci è stata spostata nella modale"
    )


def test_the_body_is_a_frame_with_one_zone_per_question() -> None:
    """Riepilogo, esito, attività, pieghe — in quest'ordine, e uno solo scorre.

    La prima versione impilava otto righe chiave/valore, lo stream, l'incarico, la
    coda tool e l'esito in un corpo che scorreva, con la lista e i `<pre>` che
    scorrevano dentro di esso: tre scroll annidati e nulla che dominasse. L'ordine
    non è cosmetico — la parte viva è la ragione per cui la modale si apre, e su
    uno schermo quadrato un `<pre>` di tre righe basta a spingerla sotto la piega.
    """
    detail = re.search(r"_subagentDetailHtml\(entry, state\)\s*\{(.*?)\n  \}", _chat(), re.S)
    assert detail, "_subagentDetailHtml non trovato"
    body = detail.group(1)
    order = ["sa-sum", "sa-focus", "_saStreamShellHtml", "_saFoldsHtml"]
    positions = [body.index(token) for token in order]
    assert positions == sorted(positions), f"le zone del telaio sono fuori ordine: {order}"

    css = CSS.read_text(encoding="utf-8")
    frame = re.search(r"\.oc-detail-body \{(.*?)\n\}", css, re.S)
    assert frame, "la regola .oc-detail-body non è stata trovata"
    assert "flex-direction: column" in frame.group(1), "il corpo non è un telaio"
    # L'unica zona elastica è lo stream: le altre tre non si allungano, quindi la
    # lista è in vista per costruzione e non serve nessuna ginnastica di scroll.
    for zone, rule in (("sa-sum", "flex-shrink: 0"), ("sa-focus", "flex-shrink: 0"),
                       ("sa-more", "flex-shrink: 0"), ("sa-stream", "flex: 1 1 auto")):
        block = re.search(r"\n\." + zone + r" \{(.*?)\n\}", css, re.S)
        assert block and rule in block.group(1), f".{zone} deve dichiarare {rule}"
    assert "_saRevealStream" not in _chat(), (
        "la ginnastica di scroll esisteva perché il corpo scorreva: nel telaio non serve"
    )


def test_the_tool_history_is_told_in_one_place_only() -> None:
    """Lo snapshot è il *ripiego* della lista, non un secondo blocco.

    "Tool recenti" come blocco a sé raccontava la stessa storia dello stream: una
    ferma e una viva, senza dire quale fosse quale.
    """
    source = _chat()
    assert "sa-detail-events" not in source, "il blocco duplicato dei tool è tornato"
    entries = re.search(r"_saStreamEntries\(view\)\s*\{(.*?)\n  \}", source, re.S)
    assert entries, "_saStreamEntries non trovato"
    body = entries.group(1)
    assert "_saSnapshotEntries()" in body
    # Ripiego solo a lista vuota: al primo evento vero lo snapshot sparisce da sé.
    assert "if (!view.rows.length)" in body
    css = CSS.read_text(encoding="utf-8")
    assert ".sa-detail-event" not in css, "il CSS del blocco duplicato è rimasto"


def test_the_long_text_lives_in_a_fold_that_survives_the_refresh() -> None:
    """Incarico e diagnostica sono `<details>`: chiusi per default, e il refresh
    dello snapshot (ogni 5s) non li richiude in faccia a chi sta leggendo."""
    source = _chat()
    fold = re.search(r"_saFold\(labelKey, innerHtml\)\s*\{(.*?)\n  \}", source, re.S)
    assert fold and "<details" in fold.group(1), "le pieghe non sono `<details>` nativi"
    assert "open" not in re.sub(r"[a-zA-Z-]*open[a-zA-Z-]*=", "", fold.group(1)), (
        "una piega aperta per default costa la metà utile della modale"
    )
    refresh = re.search(r"_refreshSubagentDetailStatic\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert refresh, "_refreshSubagentDetailStatic non trovato"
    body = refresh.group(1)
    assert "sa-more" not in body, "il refresh riscrive il contenitore delle pieghe"
    # Riscritto è solo ciò che invecchia, e l'esito solo se è cambiato davvero.
    for piece in ("sa-sum", "_saApplyFocus", "sa-diag", "_saApplyCapNote"):
        assert piece in body, piece
    focus = re.search(r"_saApplyFocus\(entry, state\)\s*\{(.*?)\n  \}", source, re.S)
    assert focus and "dataset.sig === sig" in focus.group(1), (
        "riscrivere l'esito a ogni snapshot riporta in cima chi lo sta leggendo"
    )


def test_the_footer_buttons_follow_the_state() -> None:
    """Un subagent può concludersi con la modale aperta: "Ferma" su un lavoro
    finito è una bugia, e su un job fallito nasconde l'unico bottone utile."""
    source = _chat()
    sync = re.search(r"_saSyncActions\(state\)\s*\{(.*?)\n  \}", source, re.S)
    assert sync, "_saSyncActions non trovato"
    body = sync.group(1)
    assert "_saDialogActions(state)" in body, "i bottoni passano dalla matrice"
    # Il contratto di detailDialog è il data-action-id, e il click è delegato:
    # rimpiazzare i bottoni non stacca nessun handler.
    assert "data-action-id" in body
    refresh = re.search(r"_refreshSubagentDetailStatic\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert refresh and "_saSyncActions(state)" in refresh.group(1)
    dialog = DIALOG_JS.read_text(encoding="utf-8")
    assert "actionsEl.addEventListener('click'" in dialog, "il click non è più delegato"


def test_the_stream_state_machine_lives_in_the_policy_module() -> None:
    """Append-o-rimpiazza, cursore, buco e accoppiamento start/end non si
    reimplementano nel renderer: stanno dove un test li esegue."""
    source = _chat()
    for symbol in ("saActivityInit", "saActivityFrame", "saActivityIngest",
                   "saActivityRows", "saDigestView"):
        assert f"{symbol}," in source or f"{symbol}(" in source, symbol
    # Nessuna decisione su `initial`/`gap` presa a mano fuori dalla policy.
    assert "msg.initial" not in source, "la regola append/replace è della policy"
    assert ".gap" not in source, "il buco lo dichiara la policy, non il renderer"


def test_opening_the_modal_watches_and_every_exit_unwatches() -> None:
    """Il gateway spinge SOLO a chi guarda: una chiusura che non fa unwatch lo
    lascia a spingere frame a una modale che non c'è più."""
    source = _chat()
    open_fn = re.search(r"_openSubagentDetail\(taskId\)\s*\{(.*?)\n  \}", source, re.S)
    assert open_fn, "_openSubagentDetail non trovato"
    body = open_fn.group(1)
    assert "_attachSubagentStream(taskId)" in body
    # La Promise di detailDialog risolve per OGNI via d'uscita (X, backdrop, Esc,
    # gesto Indietro di Android): un solo aggancio copre tutte.
    assert "_detachSubagentStream()" in body
    attach = re.search(r"_attachSubagentStream\(taskId\)\s*\{(.*?)\n  \}", source, re.S)
    assert attach, "_attachSubagentStream non trovato"
    attach_body = attach.group(1)
    # App in background: nessuno sta guardando, quindi si smette di guardare.
    assert "visibilitychange" in attach_body
    assert "_unwatchSubagent()" in attach_body
    # Reconnect: il gateway ha dimenticato il watch della connessione caduta.
    assert "'chat:open'" in attach_body
    detach = re.search(r"_detachSubagentStream\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert detach, "_detachSubagentStream non trovato"
    detach_body = detach.group(1)
    assert "_unwatchSubagent()" in detach_body
    for removed in ("removeEventListener", "_saStream = null"):
        assert removed in detach_body, removed


def test_the_watch_is_resumed_from_the_cursor_we_already_have() -> None:
    """Ri-watchare da zero dopo un reconnect duplicherebbe (o, col rimpiazzo,
    cancellerebbe) tutto lo stream già letto."""
    watch = re.search(r"_watchSubagent\(\)\s*\{(.*?)\n  \}", _chat(), re.S)
    assert watch, "_watchSubagent non trovato"
    assert "sendSubagentWatch(stream.taskId, stream.cursor)" in watch.group(1)
    ws = (
        Path(__file__).resolve().parents[2]
        / "jenny" / "templates" / "ui" / "assets" / "shared" / "ws-manager.js"
    ).read_text(encoding="utf-8")
    assert "'subagent_watch'" in ws and "'subagent_unwatch'" in ws
    assert "task_id: String(taskId)" in ws


def test_a_gap_triggers_an_http_resync_from_the_pre_gap_cursor() -> None:
    source = _chat()
    handler = re.search(r"_handleSubagentActivity\(msg\)\s*\{(.*?)\n  \}", source, re.S)
    assert handler, "_handleSubagentActivity non trovato"
    assert "resyncFrom" in handler.group(1)
    assert "_resyncSubagentStream(applied.resyncFrom)" in handler.group(1)
    resync = re.search(r"_resyncSubagentStream\(since\)\s*\{(.*?)\n  \}", source, re.S)
    assert resync, "_resyncSubagentStream non trovato"
    assert "getSubagentActivity(stream.taskId, since)" in resync.group(1)


def test_watch_limit_freezes_the_view_instead_of_pretending() -> None:
    """`watch_limit` = sfratto dal gateway: da lì non arriva più niente, e una
    vista ferma che si dichiara viva è peggio di una vista ferma."""
    source = _chat()
    handler = re.search(r"_handleSubagentUnwatched\(msg\)\s*\{(.*?)\n  \}", source, re.S)
    assert handler, "_handleSubagentUnwatched non trovato"
    body = handler.group(1)
    assert "'watch_limit'" in body
    assert "_saStatus = 'frozen'" in body
    assert "sa-stream-resume" in source, "manca il modo di far ripartire la vista"


def test_the_digest_is_fetched_only_when_the_block_is_expanded() -> None:
    """La maggior parte di questi blocchi non viene mai aperta: caricarli tutti
    sarebbe una lettura da disco per riga di trace."""
    source = _chat()
    append = re.search(r"_appendSubagentDigest\(entry\)\s*\{(.*?)\n  \}", source, re.S)
    assert append, "_appendSubagentDigest non trovato"
    assert "getSubagentDigest" not in append.group(1), "il digest non si carica in anticipo"
    toggle = re.search(r"_toggleSubagentDigest\(block\)\s*\{(.*?)\n  \}", source, re.S)
    assert toggle, "_toggleSubagentDigest non trovato"
    body = toggle.group(1)
    assert "getSubagentDigest(block.dataset.taskId)" in body
    # Solo all'apertura, e una volta sola.
    assert "if (!opening || block.dataset.loaded) return;" in body
    # `source: "none"` = niente blocco, non un blocco vuoto.
    assert "block.remove()" in body
    assert "subagents.digest.live" in body, "un'anteprima va dichiarata tale"


def test_the_digest_block_only_follows_a_witnessed_termination() -> None:
    """Stessa regola delle card: nulla di un turno passato compare da sé."""
    source = _chat()
    note = re.search(r"_noteFinishedSubagents\(lingering\)\s*\{(.*?)\n  \}", source, re.S)
    assert note, "_noteFinishedSubagents non trovato"
    body = note.group(1)
    assert "_saDigestSeen" in body, "un blocco per task, non uno per poll"
    render = re.search(r"_renderSubagents\(snapshot\)\s*\{(.*?)\n  \}", source, re.S)
    assert render and "_noteFinishedSubagents(lingering)" in render.group(1), (
        "il blocco nasce dai soli terminati che lingerano (transizione osservata qui)"
    )


def test_the_live_stream_adds_no_polling() -> None:
    """Il costo dello stream è zero a modale chiusa: è tutto push. L'unico timer
    resta quello del pannello coarse, e nemmeno quello gira a vista nascosta."""
    source = _chat()
    timers = re.findall(r"setInterval\(", source)
    sync = re.search(r"_syncSubagentPolling\(hasRunning\)\s*\{(.*?)\n  \}", source, re.S)
    assert sync, "_syncSubagentPolling non trovato"
    body = sync.group(1)
    assert "5000" in body, "il poll del pannello coarse è ancora il solo timer subagent"
    assert "visibilityState" in body, "a vista nascosta non c'è niente da invecchiare"
    # Nessun secondo intervallo introdotto per lo stream.
    stream = re.search(r"_attachSubagentStream\(taskId\)\s*\{(.*?)\n  \}", source, re.S)
    assert stream and "setInterval" not in stream.group(1)
    assert len(timers) <= 3, f"timer inattesi in mobile-chat.js: {len(timers)}"


def test_the_summary_line_is_rendered_and_never_re_derived() -> None:
    """`summary` è già curato e capato a 160 caratteri dal server: ricostruirlo
    da name/status significherebbe mostrare qualcosa che nessuno ha autorizzato —
    e quel testo passa da contenuto non fidato."""
    row = re.search(r"_saRowEntry\(row, isHead = false\)\s*\{(.*?)\n  \}", _chat(), re.S)
    assert row, "_saRowEntry non trovato"
    body = row.group(1)
    assert "escapeHtml(row.summary)" in body
    assert "escapeHtml(row.outcome)" in body
    assert "escapeHtml(row.name)" in body
    css = CSS.read_text(encoding="utf-8")
    # La frase va a capo e non viene troncata: è l'informazione, non la cornice.
    main = re.search(r"\.sa-ev-main\s*\{(.*?)\}", css, re.S)
    assert main and "text-overflow: ellipsis" not in main.group(1)


def test_in_flight_and_finished_do_not_look_alike() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert ".sa-ev.is-pending" in css
    assert "animation: spin" in css
    assert ".sa-ev-hole" in css, "un buco nello stream deve vedersi"
    assert ".sa-stream-live.is-live .sa-stream-dot" in css, "manca l'indicatore di diretta"
    # La lista è l'unico contenitore di scroll del corpo, e il suo pavimento sta
    # sul contenitore: un `min-height` sul figlio che scorre non lo fa scorrere,
    # lo fa sfondare il riquadro del padre e finire sopra le pieghe.
    stream_list = re.search(r"\.sa-stream-list\s*\{(.*?)\}", css, re.S)
    assert stream_list and "min-height: 0" in stream_list.group(1)
    assert "overflow-y: auto" in stream_list.group(1)
    stream = re.search(r"\n\.sa-stream \{(.*?)\n\}", css, re.S)
    assert stream and re.search(r"min-height:\s*\d+vh", stream.group(1)), (
        "il pavimento della parte viva va sul contenitore, e in vh"
    )


def test_the_stream_does_not_yank_the_view_from_a_reader() -> None:
    """Auto-scroll solo se l'utente è in fondo: con un frame ogni 0.4s, seguire
    sempre renderebbe la lista illeggibile proprio quando serve."""
    source = _chat()
    render = re.search(r"_renderSubagentStream\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert render, "_renderSubagentStream non trovato"
    body = render.group(1)
    # Si misura PRIMA di mutare, e la misura vince sull'evento scroll (una WebView
    # non lo emette per uno scroll programmatico).
    assert body.index("_saAtBottom()") < body.index("_syncStreamRows")
    assert "if (this._saStick) this._saScrollToLatest();" in body
    assert "sa-stream-jump" in source, "manca il modo di tornare in fondo"


def test_stalled_stays_unmissable() -> None:
    """Quello che il test sul device ha confermato non deve regredire."""
    source = _chat()
    assert "has-stalled" in source
    assert "sa-idle-warn" in source
    assert "subagents.stalledHint" in source
    # Auto-apertura una volta per stallo nuovo.
    assert "_lastStalledIds" in source
    css = CSS.read_text(encoding="utf-8")
    assert ".subagents.has-stalled .subagents-head { color: var(--warning); }" in css
    assert ".sa-row.state-stalled" in css
    assert ".state-stalled .sa-state { color: var(--warning); }" in css
    assert ".sa-idle-warn { color: var(--warning); }" in css


def test_elapsed_and_idle_are_never_the_ellipsised_value() -> None:
    """`idle` distingue 'piantato da 4 minuti' da 'al lavoro da 4 minuti'."""
    css = CSS.read_text(encoding="utf-8")
    assert ".sa-clock, .sa-idle { flex-shrink: 0; white-space: nowrap; }" in css
    # L'ellipsis vive solo sulla coda troncabile della riga meta.
    rest = re.search(r"\.sa-rest\s*\{(.*?)\}", css, re.S)
    assert rest and "text-overflow: ellipsis" in rest.group(1)


def test_the_collapse_rule_survives() -> None:
    """Senza questa riga il pannello si chiude solo nella freccia."""
    assert ".subagents-body[hidden] { display: none; }" in CSS.read_text(encoding="utf-8")


def test_the_digest_takes_the_whole_row_when_open() -> None:
    """Aperto, "cosa ha fatto davvero" prende la riga come gli altri fold.

    La meta-row è un flex-wrap di chip: un fold che vi entra come un box proprio
    tiene testata *e* corpo dentro una colonna larga quanto la sua etichetta. Gli
    altri fold di quella riga si sciolgono con `display: contents` — la testata
    diventa un chip, il corpo un elemento a `width: 100%` che va a capo da sé. Il
    digest era l'unico a non farlo, e aperto restava un francobollo.
    """
    css = CSS.read_text(encoding="utf-8")
    for selector in (r"\.chat-thinking", r"\.sa-digest"):
        rule = re.search(rf"\n{selector}\s*\{{(.*?)\}}", css, re.S)
        assert rule, selector
        assert "display: contents" in rule.group(1), (
            f"{selector} non si scioglie nella meta-row: aperto resta larga quanto l'etichetta"
        )
    body = re.search(r"\n\.sa-digest-body\s*\{(.*?)\}", css, re.S)
    assert body and "width: 100%" in body.group(1), "il corpo del digest non prende la riga"
    # La testata resta un chip: se si allargasse anche lei, il fold chiuso
    # occuperebbe una riga intera per tre parole. Le sue dichiarazioni stanno
    # in due posti — il gruppo delle testate del turno e la regola sua — quindi
    # si leggono tutte le regole che la nominano.
    head = "\n".join(
        corpo for selettori, corpo, _ in css_levels.rules(css)
        if ".sa-digest-head" in {x.strip() for x in selettori.split(",")}
    )
    assert "display: inline-flex" in head
    assert "align-self: flex-start" in head, (
        "fuori dalla meta-row (chat, flex a colonna) lo stretch allargherebbe la chip"
    )


def test_the_digest_has_exactly_one_home() -> None:
    """Il digest è metadata di un turno: vive in una ``.chat-turn-meta``, sempre.

    Il vecchio ripiego lo appendeva in coda a ``.chat-area`` quando non c'era una
    bolla corrente — cioè in tutti i turni che non streammano, che è esattamente
    il caso dell'annuncio di un subagent nato da lavoro interno. Lì il corpo
    diventa un flex item della colonna che scorre e si schiaccia (misurato: 12px
    di padding contro 36px di contenuto).
    """
    body = re.search(r"_appendSubagentDigest\(entry\)\s*\{(.*?)\n  \}", _chat(), re.S)
    assert body, "_appendSubagentDigest non trovato"
    source = body.group(1)
    assert "chatArea.appendChild(block)" not in source, (
        "il digest non deve poter finire come figlio diretto della colonna della chat"
    )
    assert "_ensureAiMessage()" in source, (
        "senza bolla corrente va creata, non aggirata"
    )
    assert "_ensureMetaRow(" in source


def test_nothing_stacked_in_the_scrolling_column_can_shrink() -> None:
    """La difesa strutturale contro l'intera categoria di bug.

    Una colonna flex che scorre ha spazio libero NEGATIVO appena il contenuto
    supera lo schermo, e un flex item che è a sua volta uno scroll container ha
    min-height automatica ZERO: assorbe tutta la compressione e si riduce al
    proprio padding. Serve la regola su ENTRAMBI i lati, perché
    ``display: contents`` solleva i figli nel layout ma il selettore ``>``
    continua a vedere il DOM: da solo colpirebbe il wrapper, che non genera box.
    """
    css = CSS.read_text(encoding="utf-8")
    column = re.search(r"\n\.chat-area > \*\s*\{(.*?)\}", css, re.S)
    assert column, ".chat-area > * non trovato"
    assert "flex-shrink: 0" in column.group(1)
    # E la stessa proprietà nella regola di ogni corpo che scorre, perché lì la
    # regola sulla colonna non arriva.
    for selector in (r"\.sa-digest-body", r"\.chat-thinking-body"):
        rule = re.search(rf"\n{selector}\s*\{{(.*?)\}}", css, re.S)
        assert rule, selector
        body = rule.group(1)
        assert "overflow-y: auto" in body, (
            f"{selector} non è più uno scroll container: la guardia mentirebbe"
        )
        assert "flex-shrink: 0" in body, selector


def test_carousel_only_from_two_cards_up() -> None:
    render = re.search(r"_renderSubagents\(snapshot\)\s*\{(.*?)\n  \}", _chat(), re.S)
    assert render
    body = render.group(1)
    assert "'is-carousel', total > 1" in body
    assert "'is-single', total === 1" in body


def test_every_subagent_string_exists_in_both_locales() -> None:
    """Una chiave mancante non fallisce da nessuna parte: `i18n.t()` ritorna la
    chiave grezza, e l'utente si ritrova `subagents.activity.live` a schermo.

    La parità fra i due file la difende ``test_i18n_parity``; qui si controlla
    che le chiavi *usate dal codice* esistano davvero.
    """
    source = _chat()
    # Ogni literal `'subagents.…'`, non solo quelli dentro `i18n.t(`: le pieghe e
    # le righe di diagnostica ricevono la chiave come argomento e la traducono
    # dentro l'helper, e una chiave inventata lì si vedrebbe a schermo com'è.
    used = set(re.findall(r"'(subagents\.[a-zA-Z.]+)'", source))
    # Le chiavi composte a runtime (state/phase/activity.<status>) hanno già il
    # loro fallback sul valore grezzo o un enum chiuso: si elencano qui.
    used |= {
        f"subagents.activity.{status}" for status in ("live", "paused", "frozen", "offline")
    }
    assert "subagents.activity.waiting" in used, "il test non sta leggendo le chiavi giuste"
    for locale in ("en", "it"):
        data = json.loads(
            (
                Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
                / "assets" / "i18n" / f"{locale}.json"
            ).read_text(encoding="utf-8")
        )
        for key in sorted(used):
            node = data
            for part in key.split("."):
                assert isinstance(node, dict) and part in node, f"{locale}.json manca {key}"
                node = node[part]
            assert isinstance(node, str), f"{locale}.json: {key} non è una stringa"


def test_relaunch_from_the_ui_stays_manual() -> None:
    """Un umano che premo Rilancia non lo rifiuta il tetto automatico."""
    routes = (
        Path(__file__).resolve().parents[2] / "jenny" / "webui" / "subagent_routes.py"
    ).read_text(encoding="utf-8")
    assert "manual=True" in routes
