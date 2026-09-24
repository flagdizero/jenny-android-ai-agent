# L'umore dagli emoji — il piano

> Sostituisce il **sidecar** di [`mascot-mood-plan.md`](./mascot-mood-plan.md) —
> la richiesta al modello dopo ogni turno — e la scelta del modello che lo
> serviva (`mascotMoodModelPreset`). Del vecchio piano restano validi il frame
> `mascot_mood`, le tre facce (`happy`/`sad`/`angry`, v.
> [`mascot-faces-plan.md`](./mascot-faces-plan.md)), le regole con cui il client
> le mostra e la faccia triste gratuita sull'errore.

Decisione dell'utente, 24/09/2026: **niente più chiamate extra per l'umore**, si
risparmia; l'umore si legge dagli emoji della risposta, con un dizionario
emoji → faccia.

---

## Perché (il rilievo del 24/09/2026)

Sul telefono le espressioni **non sono mai comparse**, e nessuno se n'era
accorto. Il log:

> mascot mood: 'deepseek-v4.1-flash' spent the whole budget without answering
> (thinking on?); every verdict is neutral

La richiesta chiede `reasoning_effort="none"` e 3 token. DeepSeek il
ragionamento lo spegne solo con `{"thinking": {"type": "disabled"}}`, e Jenny lo
manda solo ai nomi esatti `deepseek-v4-flash`/`deepseek-v4-pro`
(`providers/openai_compat_helpers.py`, commit `e1c2e0e`). `v4.1` non è
nell'elenco: il modello pensa, i 3 token finiscono, il verdetto è sempre
neutro, e un neutro non manda nessun frame. Nel log nessun umore riuscito.

È un difetto della **forma**, non del numero: qualunque sidecar che dipende dal
sapere come si spegne il ragionamento di ogni modello si rompe alla prossima
versione, in silenzio. Le alternative discusse — riprovare con più budget
(costa i token del ragionamento a ogni turno), far scrivere l'umore al modello
principale (cambia il suo prompt, che il vecchio piano esclude apposta, e un
segno in coda può finire in chat o su Telegram) — sono state scartate a favore
di questa.

**Il segnale c'è già.** Jenny scrive le proprie emozioni: nelle risposte vere
chiude spesso con un emoji (😌 😏 ☀️ negli ultimi scambi a schermo). Leggerli
costa zero, è istantaneo, non dipende da provider, modello o lingua.

---

## Le decisioni

**D1 — Sul server, al posto della chiamata.** La funzione nuova sta in
`jenny/session/mascot_mood.py` e prende il posto di `classify_mood` in
`WebuiTurnCoordinator._schedule_mood_from_event`. Stesso momento (dopo
`turn_end`), stesso frame, stessi campi. Quindi **i due client non cambiano**:
casa e officina usano già lo stesso `shared/jenny-mascot.js`, che accetta
`happy`/`sad`/`angry` e scarta il resto. Un posto solo per il dizionario, e il
giorno in cui la mascotte flottante (Kotlin) vorrà le facce, il verdetto è già
calcolato per lei.

**D2 — Si legge solo la risposta.** L'ultima riga della assistente, con la
stessa selezione di oggi (`mood_inputs`: niente comandi, niente righe
sintetiche, `strip_think`; se l'ultima riga è dell'utente il turno è finito in
errore e l'errore ha già la sua faccia). Cade la domanda dell'utente, che serviva
al modello e non al dizionario, e cade il minimo di 40 caratteri: «ok 😊» è un
umore.

**D3 — Non contano gli emoji che non sono suoi.** Prima di leggere si tolgono i
blocchi di codice (```` ``` ````), il codice in linea e le righe citate (`>`):
lì dentro un emoji è dell'utente o di un file, non di lei.

**D4 — Il dizionario.** Tre insiemi, uno per faccia. Il criterio è la faccia
che si disegna, non la sfumatura: con tre espressioni, «contenta»,
«sollevata», «complice» e «affettuosa» sono tutte `happy`.

- **happy** — sorrisi e risate (😀 😃 😄 😁 😆 😊 🙂 😉 😌 😏 😎 🤗 🥰 😍 😘 🤩
  🥳 😂 🤣 😋 😜 😝 😛), cuori (❤️ 🧡 💛 💚 💙 💜 🤍 🖤 💕 💖 💗 💓 💞 😻),
  festa e approvazione (🎉 🎊 ✨ 🌟 💪 👍 👏 🙌 🥂).
- **sad** — tristezza, delusione, preoccupazione, stanchezza (😢 😭 😞 😔 😟
  😕 🙁 ☹️ 😥 😰 😓 😩 😫 🥺 🥲 😿 💔 😪 😮‍💨).
- **angry** — fastidio e rabbia (😠 😡 🤬 😤 👿 💢 🙄 😒 😾).
- **fuori dal dizionario**, di proposito: gli ambigui (😅 🙃 😬 🤔 😐 😑 😶 👀
  🤷 😳 😱 🫠) e tutto ciò che non è un'emozione (☀️ 🌧️ 🍝 📅 🚗 bandiere,
  oggetti, frecce). Meglio nessuna faccia che quella sbagliata: un neutro non
  manda niente, come oggi.
- **Emoticon di testo**, pochi e con i bordi controllati: `:)` `:-)` `:D` `;)`
  → happy, `:(` `:-(` `:'(` → sad, `>:(` → angry. Mai dentro una parola o un
  URL (`http://`), e fuori dal codice per D3.

**D5 — Come si confrontano.** Si normalizza prima (via `U+FE0F`, via i toni di
pelle `U+1F3FB`–`U+1F3FF`; una sequenza ZWJ si cerca intera e poi per il suo
primo emoji). Poi **vince la faccia con più emoji; a parità vince l'ultima**
che compare — il tono di una risposta sta in coda (il vecchio sidecar per lo
stesso motivo guardava la coda). Nessun emoji del dizionario → neutro, nessun
frame.

**D6 — Cosa si toglie.** `classify_mood`, `build_mood_request`, `parse_mood`,
`_LETTER_TO_MOOD`, `resolve_mood_model`, `MOOD_MAX_TOKENS`,
`MOOD_REASONING_EFFORT`, `MOOD_TEMPERATURE`, i due avvisi `_WARNED_*`,
l'uso di `conversation_scope` (opencode) e la registrazione dei token.
`MOOD_USER_MAX_CHARS`/`MOOD_ASSISTANT_MAX_CHARS`/`MOOD_MIN_ASSISTANT_CHARS`
spariscono con D2. Il modulo resta dov'è, puro e senza rete.

**D7 — La configurazione.** `agents.defaults.mascotMood` resta: accende e
spegne le facce, adesso gratis. `mascotMoodModelPreset` esce dallo schema ed
entra in `loader.RETIRED_KEY_PATHS` (camelCase e snake_case).
~~Resta nel file, inerte, senza migrazione~~ — **sbagliato, corretto
all'implementazione**: una chiave che lo schema non conosce `store.mutate` la
conserva *per sempre*, e `load_config_with_raw` avvisa «not recognised» a ogni
caricamento. Le chiavi ritirate sono la terza specie apposta: nessun avviso, e
alla prossima scrittura cadono. Nessun salto di `configVersion`: cadere alla
prima scrittura ordinaria basta, e non c'è niente da riscrivere al boot.

**D8 — Il conteggio dei token.** La fonte `mascot` resta in `_SOURCE_KEYS`
(`agent/token_usage.py`) per leggere lo storico già registrato; da qui in poi
non ci scrive più nessuno. Il commento lo dice.

**D9 — Il log.** Una riga INFO per verdetto non neutro, con l'emoji che l'ha
deciso (`mascot mood: happy (from 😏, 2 votes)`): è l'unico modo di verificarlo
sul telefono, dove i DEBUG non si vedono. Nessun testo della risposta nel log.

**D10 — Fuori da questo lavoro.** La mascotte flottante (Kotlin,
`FloatingOverlayController.syncFace`) che oggi sa solo bordo/pensa/triste: può
prendere lo stesso verdetto in un giro successivo. E il riconoscimento della
famiglia DeepSeek (`v4.1` fuori dall'elenco): non serve più all'umore, ma resta
vero per chi imposta `reasoningEffort: none` sul turno principale — va trattato
a parte.

---

## I passi

1. **Commit dell'unificazione della mascotte** (fatta il 24/09, ancora nel
   working tree), da sola: questo lavoro ci si appoggia e va tenuto separato.
2. **Il dizionario e la funzione**, `mood_from_reply(text) -> str` in
   `mascot_mood.py`, pura: pulizia (D3), normalizzazione e voto (D5).
   Test in `tests/session/test_mascot_mood.py` riscritto: un caso per regola,
   **con esempi inventati** (il repo è pubblico), più un contratto che ogni
   voce del dizionario mappi su una faccia di `MOODS` e che nessun emoji stia in
   due insiemi.
3. **Il collegamento**: `_schedule_mood_from_event` chiama la funzione al posto
   del provider. Non serve più un task in background con la rete, ma si
   lascia lì: il frame deve comunque partire *dopo* `turn_end`, ed è quello
   che l'ordine garantisce. Test in `tests/session/test_webui_turns.py`: frame
   pubblicato per una risposta con 😊, niente per una senza emoji o con solo
   ☀️, niente con `mascotMood: false`, e **nessuna chiamata al provider** in
   nessun caso.
4. **La pulizia** (D6, D7, D8): schema, test di config, `token_usage`, il test
   opencode che copre lo scope della richiesta dell'umore.
5. **Documentazione pubblica** (`docs/` ha un secondo lettore, il sito):
   `using/themes-mascot.md` (il paragrafo che racconta la richiesta),
   `reference/configuration.md` (la riga di `mascotMood`, via quella del
   preset), `reference/websocket.md` (da dove viene il frame). Nessun file si
   sposta o si rinomina, quindi nessun URL cambia.
6. **Il vecchio piano**: un cappello in `mascot-mood-plan.md` che rimanda qui.
7. **Verifica.** Suite completa su 3.14 e sul venv 3.11; ruff; pyright sul
   perimetro bloccante. Poi build e installazione, e sul telefono: la riga
   INFO nel log dopo una risposta con un emoji. Per **vedere** la faccia serve
   la mascotte in-app accesa (oggi è spenta: `jenny-mascotte-visible = 0`), e
   quella si riaccende da Tu e Jenny — scelta dell'utente.

**Misura facoltativa, da autorizzare:** contare gli emoji che Jenny usa davvero
nelle conversazioni sul telefono — **solo i conteggi**, calcolati sul
dispositivo, senza copiare il testo — per vedere quanti dei suoi emoji il
dizionario copre e quanti ne restano fuori. Il tentativo di copiare le sessioni
sul Mac è stato bloccato, giustamente, perché sono dati personali.

---

## Checklist

- [x] 1 · Commit dell'unificazione della mascotte — `b0567b8`
- [x] 2 · `mood_from_reply` + dizionario + test (`tests/session/test_mascot_mood.py`, 44)
- [x] 3 · Collegamento in `webui_turns.py` + test: il provider finto **solleva** se
      viene chiamato, in tutti i casi; nessun token registrato; riga INFO senza testo
- [x] 4 · Via sidecar, avvisi, token; preset in `RETIRED_KEY_PATHS` (D7 corretta),
      provato con una mutazione (senza la voce il test diventa rosso); `mascot`
      resta per lo storico; tolto il terzo percorso dal test dello scope opencode
- [x] 5 · `docs/` aggiornati (tre pagine, nessun file spostato)
- [x] 6 · Cappello su `mascot-mood-plan.md`
- [x] 7 · Suite 3.14 (10.599) + venv 3.11 sui file toccati (593), ruff, pyright;
      build e installazione (13:57, PID 20987): gateway su, nessun avviso sulla
      chiave ritirata, che nel `config.json` del telefono c'è ancora
      (`mascotMoodModelPreset: null`)
- [ ] 7b · Riga `mascot mood: … (from …)` nel log dopo una risposta vera con un
      emoji — serve un messaggio dell'utente; per *vedere* la faccia, la
      mascotte in-app va riaccesa da Tu e Jenny
- [ ] (facoltativa) Copertura del dizionario sugli emoji veri — solo conteggi, se autorizzata
