# La selezione alla radice — lista di esecuzione

Stato di [`chat-selection-root-plan.md`](./chat-selection-root-plan.md). Si
spunta quando è **girato** (test verdi, o visto sul telefono per i passi 2, 3,
4, 5 e 7), non quando è scritto.

Ramo: `claude/fra-fai-pull-q8fxdq`. Verifica per ogni passo:

```bash
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && python3 -m pytest -q
```

Telefono (albero pulito, release firmata, come nel primo piano):

```bash
export ANDROID_SERIAL="${ANDROID_SERIAL:?il seriale del telefono di prova, da adb devices -l}" ANDROID_HOME=$HOME/Library/Android/sdk
(cd android && ./gradlew app:assembleRelease) && adb install -r android/app/build/outputs/apk/release/app-release.apk
```

Commit sempre con `-s`.

---

## Passo 1 — rig e documenti

- [x] 1.1 `.agent/selection-rig/` con le tre pagine, `gesture.sh`, `shot.sh`, README.
- [x] 1.2 Matrice A/B/C misurata su Chrome 150 del Titan 2 e scritta nel piano.
- [x] 1.3 Nota in coda a `chat-selection-plan.md` che rimanda qui.

## Passo 2 — `_scroller`

- [x] 2.1 Getter `_scroller` → `document.scrollingElement`.
- [x] 2.2 Nessun `this.chatArea.scrollTop|scrollHeight|clientHeight` fuori dal
      guard di `_rememberScrollAnchor`.
- [x] 2.3 Listener `scroll` su `window`; `touchstart/end/cancel` su `#view-chat`.
- [x] 2.4 `visualViewport.resize` → `scrollToBottom(true)` se `_autoScroll`.
- [x] 2.5 `test_history_reach_client.py` finge `_scroller`, verde.

## Passo 3 — guscio `mode-chat`

- [x] 3.1 `.chat-bottom` in `index.html` attorno a `#subagents`, `#attach-preview`,
      `#input-bar`, col FAB dentro; `#view-chat` senza `height` inline.
- [x] 3.2 Blocco CSS `:root.mode-chat` (html/body/.app/.body/.main/#view-chat/.chat-area).
- [x] 3.3 `.chat-bottom` e `.dock` sticky; FAB assoluto in `.chat-bottom`.
- [x] 3.4 `.jenny-duo`, `.drawer`, `.drawer-backdrop`, `.swipe-scrim` fixed in `mode-chat`.
- [x] 3.5 `setupViewportHeight` → `--vv-height`; `scrollTo(0,0)` fuori da `mode-chat`.
- [x] 3.6 Telefono: scroll, autoscroll in streaming, FAB (spostato a sinistra
      della mascotte, che lo copriva), "carica altro" in cima, cambio vista e
      ritorno al punto di lettura, swipe chat → Wiki. **Tastiera soft non
      misurabile**: il Titan 2 ha la tastiera fisica e mostra solo la striscia IME.

## Passo 4 — chrome trasparente durante la selezione

- [x] 4.1 `exposeSelectionState()` in `selection.js`, classe `has-selection` su `<html>`.
- [x] 4.2 Regola `:root.has-selection :is(.chat-bottom, .dock, .jenny-duo) { pointer-events: none }`.
- [x] 4.3 `forwardTapsThroughChrome(selectors)`: `focus()` sul composer, `click()` sui bottoni.
- [x] 4.4 Telefono: i due gesti dell'utente; base sotto composer e sotto dock;
      tap sul composer e sul dock durante una selezione.

## Passo 5 — selezionabilità

- [x] 5.1 `.chat-area { user-select: none }`; `text` su `.chat-content`, `.chat-tool-name`.
- [x] 5.2 Telefono: "Seleziona tutto" evidenzia le bolle e nient'altro della
      chat (meta, riga Copia, chip, dock). Residuo cosmetico: il *placeholder*
      del composer si evidenzia durante il select-all del documento; il campo
      resta `user-select: text` di proposito, perché lì si scrive.

## Passo 6 — rimozioni

- [x] 6.1 `pinSelectionAnchor`, `anchorWasClamped`, `EDGE_EPS`, `pointRect`,
      `looksLikeFreshWord` via da `selection.js`; chiamata via da `mobile-app.js`.
- [x] 6.2 `chat-select-sheet` via da `index.html`; `_showSelectSheet` e la voce
      del foglio `⋯` via da `mobile-chat.js`; `.oc-select*` via dal CSS.
- [x] 6.3 Chiavi `chat.selectText`, `chat.selectAll` via da `it.json` e `en.json`.
- [x] 6.4 `test_selection_anchor_client.py` via; `test_chat_selection_contract.py`
      senza le asserzioni sul foglio, su `onclose`, su `EDGE_EPS`/`getClientRects`.

## Passo 7 — test

- [x] 7.1 `test_chat_root_scroller_contract.py` (elenco nel piano).
- [x] 7.2 `test_chat_scroller_client.py` in node.
- [x] 7.3 `test_selection_chrome_client.py` in node.
- [x] 7.4 Suite piena verde, lint e pyright puliti.

## Passo 8 — chiusura

- [x] 8.1 Protocollo telefono su build release pulita (13/09/2026, sera): punti
      1-10, 12-14 verificati; 11 (tastiera soft) non misurabile sul Titan 2.
- [x] 8.2 `gotchas.md`: le due regole nuove.
- [x] 8.3 Piano aggiornato con le misure; memoria aggiornata.
- [ ] 8.4 PR (solo se richiesta).
