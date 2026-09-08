# Le facce di Jenny — lista di esecuzione

Stato di [`mascot-faces-plan.md`](./mascot-faces-plan.md). Il ragionamento sta
là, qui c'è solo cosa è fatto. Si spunta quando è **girato** (test verdi, o
visto sul telefono per il passo 7), non quando è scritto.

Ramo `feat/mascot-faces`, aperto l'08/09/2026 da `main` (004a56b). **Passi 0 e
1 girati l'08/09/2026**: 9.211 test verdi, lint e pyright puliti. Il B/N è
uscito senza perdere pose (verificato prima con gli md5: tutte e 15 erano
davvero colorate) e la guida `COLORARE_LE_POSE.md` è diventata
`SOSTITUIRE_UNA_POSA.md`, perché di colorare non c'è più niente. **Passo 2
girato l'08/09/2026**: 9.220 test verdi. I 9 livelli pesano 102 kB (64 di corpi,
38 di facce), e la registrazione ha un test che la misura — due coppie
ricompongono la posa cotta con scarto massimo 3 e 2 su 255. **Passi 3, 4, 5 e 6
girati l'08/09/2026 in un commit solo**: separarli avrebbe lasciato un contratto
rosso in mezzo (il client nomina gli umori che il backend manda, e cambiano
insieme), e le doc raccontano proprio il comportamento che cambia lì. 9.226 test
verdi; le aree toccate verdi anche su 3.11 (venv ricostruito, si era rotto di
nuovo).

Deviazioni dal piano, decise strada facendo:
- **niente classe `layered`**: la faccia si spegne da sé con `_setFace(null)` e
  una classe sull'img, quindi la decisione sta in un posto solo (JS) invece di
  due;
- **`_layered()` è solo `out`**, non `out && !flying`: in volo il layer del
  volo copre tutto e tenere l'arte a livelli sotto evita un lampo di posa cotta
  all'atterraggio;
- **il pensa e i 4 frame del parlato non si esportano più** (5 webp, 120 kB): il
  livello li ha resi orfani. I sorgenti restano: sono il riferimento del test
  di registrazione;
- **`_talkTick` spegne la faccia anche nel ramo docked**: trascinandola al bordo
  *mentre* parla, `_syncArt` esce subito e la faccia resterebbe accesa sopra
  un'arte che ce l'ha già dentro. Trovato da un test, non a occhio.

Verifica per ogni passo (da `AGENTS.md`, con la correzione locale
`python3 -m pytest`, non `pytest`):

```bash
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && python3 -m pytest -q
```

Commit sempre con `-s` (DCO). Il telefono è su Python 3.11: le aree toccate
vanno provate anche nel venv 3.11 (`cat /tmp/py311/pyvenv.cfg` prima di
fidarsi — quel venv si è già rotto due volte).

---

## Passo 0 — il ramo

- [x] **0.1** albero pulito, `main` aggiornata
- [x] **0.2** `git switch -c feat/mascot-faces main`

## Passo 1 — il bianco/nero si ritira *(un commit)*

- [x] **1.1** asset: 15 webp B/N cancellati, 15 `-color` rinominati al nome
      piano; `_UI_MANIFEST` da 30 a 15 voci
- [x] **1.2** sorgenti: 15 `<stem>.PNG` line-art e `icon_color.png` cancellati,
      15 `<stem>_color.PNG` rinominati a `<stem>.PNG`
- [x] **1.3** `gen_pose_webp.py`: un export per posa
- [x] **1.4** `shared/mascot.js`: via `poseUrl`, `mascotColor`,
      `setMascotColor`, `COLOR_KEY`, `color` dal detail; chiave morta ripulita
- [x] **1.5** `mobile-jenny.js`: `poseUrl(x)` → `x`, e via il ciclo di
      `_applyMascotPrefs` sulle `img` del volo
- [x] **1.6** `mobile-onboarding.js`: `poseUrl(x)` → `x`
- [x] **1.7** `mobile-settings.js` + i18n `it`/`en`: via la riga dei colori
- [x] **1.8** test: `localStorage` con `jenny-mascotte-color = '0'` non produce
      path B/N e la chiave si ripulisce; parità i18n verde
- [x] **1.9** `themes-mascot.md`, `settings.md`, `README.md`,
      `COLORARE_LE_POSE.md`: una variante per posa
- [x] **1.10** verifica verde; commit `-s`

## Passo 2 — i sorgenti nuovi e la pipeline *(un commit, nessun cambio a runtime)*

- [x] **2.1** 23 PNG da `JENNY_IMG_NEW/` a `android/image_source/` coi nomi
      della tabella (i due nomi sporchi corretti, `wave1`/`wave2` incrociati);
      `JENNY_IMG_NEW/` rimossa
- [x] **2.2** `gen_pose_webp.py`: tabella `LAYERS`, 9 export
- [x] **2.3** `_UI_MANIFEST`: +9
- [x] **2.4** `README.md` + `COLORARE_LE_POSE.md`: due livelli, tabella,
      riserva, ricetta della coppia neutra diagonale
- [x] **2.5** `tests/webui/test_mascot_layer_sources.py`: esistenza + manifest
      + **ricomposizione** dei tre pari cotti (`importorskip("PIL")`)
- [x] **2.6** verifica verde; commit `-s`

## Passo 3 — il livello faccia nel client *(un commit, umore ancora fermo)*

- [x] **3.1** CSS: `.jenny-art-stack`, `img.jenny-face`, specchio e volo
      spostati sul wrapper, `:not(.layered)` nasconde la faccia; bob e wobble
      non toccati
- [x] **3.2** `_buildDom`: wrapper + seconda `img`; il volo resta fratello
- [x] **3.3** `BODY`/`FACE`, `_setBody`/`_setFace`, classe `layered`
      (`out && !flying`)
- [x] **3.4** `_syncArt` e `_talkTick` sulla precedenza del piano; ramo cotto
      invariato
- [x] **3.5** preload dei 9
- [x] **3.6** test: precedenza della faccia; ramo cotto invariato
- [x] **3.7** verifica verde; commit `-s`

## Passo 4 — il vocabolario *(un commit, backend)*

- [x] **4.1** `MOODS` a quattro, `_LETTER_TO_MOOD` A–D, riga `C` del prompt
- [x] **4.2** `config/schema.py`: `mascot_mood` di default `True`
- [x] **4.3** test `tests/session/` e `tests/config/` aggiornati
- [x] **4.4** verifica verde, **anche su 3.11**; commit `-s`

## Passo 5 — l'umore sulle facce *(un commit, client)*

- [x] **5.1** via `MOOD_ART`, `MOOD_STANDBY`, `MOOD_WORRY_AFTER_MS`,
      `_armWorry`, `_disarmWorry`, guardia `worried`
- [x] **5.2** contratto `MOODS` ⊆ `FACE` in `test_mascot_mood_client.py`;
      harness senza standby né timer della preoccupazione
- [x] **5.3** l'errore fa ancora `sad`
- [x] **5.4** verifica verde; commit `-s`

## Passo 6 — documentazione *(stesso PR)*

- [x] **6.1** `docs/using/themes-mascot.md`: quattro espressioni, due livelli;
      via lo standby
- [x] **6.2** `docs/reference/configuration.md`: `mascotMood` torna `true`
- [x] **6.3** `docs/reference/websocket.md`: etichette del frame
- [x] **6.4** note di superamento su `mascot-mood-plan.md` (D7, D13, Standby) e
      sulla sua checklist (passo 7 → qui)
- [x] **6.5** nessun file di `docs/` spostato o rinominato

## Passo 7 — sul telefono *(girato l'08/09/2026; misure nel piano)*

- [x] **7.1** APK **release** dal ramo (il telefono ha la firma release:
      `installDebug` non passa), albero pulito, `CN=flagDiZero`, contenuto
      verificato dentro `assets/chaquopy/app.imy`
- [x] **7.2** screenshot dei quattro stati: riposo, pensa, parlato, felice —
      e la faccia felice regge anche a +1,5 s
- [x] **7.3** parlato: 23 scatti su 9,2 s, la bocca alterna e **il gesto pure**
      — dopo la correzione del passo 7bis; faccia incollata al corpo in tutti
      e 23 (una sola taglia provata, la Small)
- [x] **7.4** specchio: out a sinistra è specchiata (ciocca e logo ribaltati),
      cioè guarda dentro lo schermo
- [x] **7.5** docked: arte cotta con la sua faccia, mezza visibile, come prima
- [x] **7.6** volo: la pegman è un'immagine sola, nessuna faccia appiccicata
      sopra; all'atterraggio torna ai livelli senza lampi
- [x] **7.7** 19 asset su 19 nominati dal client rispondono **200** dal
      gateway del telefono; nessun 404 di asset in logcat
- [x] **7.8** Impostazioni → Personalizzazione: il blocco Mascotte ha due
      righe, la riga dei colori non c'è più, taglia e visibilità funzionano
- [ ] **7.9** turno in errore → faccia triste — **non riproducibile
      dall'esterno**: con le radio spente il fallimento del provider torna
      come *testo in chat* («Error calling LLM: …»), non come frame `error`,
      quindi il livello 0 non scatta. Il lato client è coperto dal test node
      (`_applyMood('sad')` → faccia triste). V. incognite nel piano.
- [ ] **7.10** turno da Telegram → non fatto: servirebbe mandare un messaggio
      dal Telegram dell'utente. Coperto dal test del coordinatore.
- [ ] **7.11** onboarding: non eseguito, richiederebbe azzerare `workspace/`
      (config, chiavi, cronologia). I 5 asset che usa rispondono 200.
- [x] **7.12** PR [#34](https://github.com/flagdizero/jenny-android-ai-agent/pull/34) verso `main` (il merge è dell'utente)

## Passo 7bis — il gesto che non cambiava mai *(trovato sul telefono)*

- [x] **7bis.1** `_setAgentState` tiene viva la bocca a ogni segnale di
      parlato, non solo al primo: prima, dopo un secondo l'animatore tornava
      al pensa in mezzo alla frase e ripartiva da `animIdx = 0`, quindi
      `BODY.hand` era un asset che nessuno poteva vedere. Difetto di prima dei
      livelli, che i livelli hanno reso visibile.
- [x] **7bis.2** test che fallisce sull'asserzione (non sulla sintassi) se la
      riga si toglie; secondo APK e seconda raffica a confermarlo

## Passo 8 — l'interruttore *(pianificato l'08/09/2026; decisioni G1–G8 nel piano)*

- [ ] **8.1** backend: `_apply_bool` per `mascot_mood`/`mascotMood` in
      `_apply_agent_defaults`, valore nel payload `agent`, **niente**
      `restart_required` (si rilegge a ogni turno)
- [ ] **8.2** test route: accetta `1`/`0`, round-trip nel payload, niente
      riavvio
- [ ] **8.3** client: `_toggleRow` + `_hint` nel blocco Mascotte, lettura da
      `this.data` con `?? true`, rollback sull'errore come il toggle posizione
- [ ] **8.4** i18n `it`/`en`: `settings.mascotMood`, `settings.mascotMoodHint`
      col numero misurato (~200 token per turno)
- [ ] **8.5** la prova che spenta non costa: zero richieste, zero frame, zero
      righe nel bucket `mascot`
- [ ] **8.6** docs: `settings.md` (riga nuova **e** la frase sui controlli
      solo-locali, che oggi mette la mascotte fra quelli che non toccano
      `config.json`), `themes-mascot.md`, `configuration.md`
- [ ] **8.7** verifica completa verde; commit `-s`
- [ ] **8.8** telefono: spenta → un turno → bucket fermo e nessun frame;
      riaccesa → un turno → il frame torna
