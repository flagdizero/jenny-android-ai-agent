# Le skill tornano in vista — in officina, cassetto Mani

> Stato: **fatto** (24/09/2026), installato sul Titan 2. Restano due verifiche
> sul telefono, dette al passo 6. Mockup visto
> e approvato in chat il 24/09 (tre schermate: Mani, pannello, «Le tue» vuota).
> Si spunta qui, passo per passo, man mano che il lavoro atterra.

## Perché

Dal 21/09/2026 (`98a0230`) le skill non si vedono da nessuna parte: la schermata
Apps che le ospitava è stata cancellata, e il cassetto le aveva già lasciate il
31/08 (`6363b44`) perché non si lanciano. Quel commit lasciava aperta la domanda
*«dove devono stare»*; la cancellazione l'ha chiusa per sottrazione, non per
risposta.

La risposta era già scritta:

- `officina-tavole-plan.md` — Mani è *«la porta per app e skill»*;
- `officina-allineamento-tavola.md` §I — una riga di riepilogo con freccina,
  *«Skill · 3 tue, 4 integrate»*;
- `docs/using/webui-tour.md:13` — **Hands**: *«web, location, SSH, Telegram,
  skills, mini-apps»*. La documentazione pubblica le mette già lì.

Mani risponde a *cosa sa fare Jenny*; una skill è una procedura che sa eseguire.

## Cosa resta fuori, deciso

Il 21/09 hai scelto di togliere **modifica, cancellazione, creazione** dalla UI.
Resta così: per scriverne o cambiarne una si passa dalla chat. Qui entra solo
**leggere** e **accendere/spegnere le tue**. Niente modalità sviluppatore
(tolta in `78ff330`), niente elenco delle `internal`, niente ricerca.

## Tre fatti che il piano rispetta

1. **Le integrate si riscrivono a ogni avvio.** Vivono in `workspace/skills/`
   come le tue, ma `sync_workspace_templates` (`utils/helpers.py:788`) le
   ri-estrae dall'APK senza `skip_existing`. Un `disabled: true` scritto nel
   loro frontmatter vale fino al riavvio, poi sparisce in silenzio — e oggi la
   rotta lo accetta. Lo dice già `docs/using/skills.md` («Honesty note»), e la
   vecchia UI lo lasciava fare lo stesso. Quindi: **interruttore solo sulle tue,
   e il backend rifiuta** il resto.
2. **Il payload non sa chi è integrata.** `source` vale `"workspace"` per tutte.
   `locked || internal` coincide oggi coi bundled per caso, non per contratto.
   L'elenco vero c'è ed è statico: `_SKILLS_MANIFEST` in
   `utils/android_assets.py:174`, lo stesso che l'estrazione usa sul telefono.
3. **Spegnere una skill vale dal turno dopo.** Il riassunto delle skill si
   ricostruisce dal disco a ogni turno (`agent/context.py:826` →
   `SkillsLoader.build_skills_summary`, che filtra i `disabled`). Costo: quel
   turno cambia il prompt di sistema e rompe la cache una volta.

## Le regole di divisione

Una funzione pura sola le applica (`shared/skills-view.js`, passo 2). Per ogni
voce del payload:

| condizione | dove va | controllo |
|---|---|---|
| `internal` | **servizio** — non elencata, solo contata | — |
| `bundled` e non `internal` | **Integrate** | lucchetto |
| non `bundled`, non `internal`, `locked` | **Le tue** | lucchetto (il frontmatter chiede di non toccarla) |
| non `bundled`, non `internal`, non `locked` | **Le tue** | interruttore, acceso = `!disabled` |

- **Riassunto** di una riga: `user_summary[locale] → user_summary.it →
  user_summary.en → description`, e **niente** se la `description` è il nome
  (è il ripiego di `_description()`: ripeterlo sotto il nome non dice nulla).
  Stessa scelta del vecchio `_skillUserSummary`, meno il ripiego sul nome.
- **Non disponibile** (`available: false`): al posto del riassunto, pallino
  ambra **più** `unavailable_reason` in testo — il colore da solo non basta
  (stessa regola di `test_lo_stato_si_distingue_anche_senza_colore`).
  Spenta e non disponibile possono coesistere: si vedono tutte e due.
- **Ordine**: nome, alfabetico, dentro ciascun blocco (il backend già ordina).
- Una skill tolta via `agents.defaults.disabled_skills` nel config **non
  arriva** nel payload (`list_skills` la filtra): non si vede. Accettato — quel
  campo nessuna schermata lo scrive, e resta fuori da questo giro.

## Parole (it / en)

Namespace nuovo `skills.*`. Le chiavi `apps.*` delle skill sono **morte dal
21/09** (zero riferimenti in `assets/` e negli HTML) e si cancellano al passo 5.

| chiave | it | en |
|---|---|---|
| `officina.gruppi.skill` | Skill | Skills |
| `skills.riepilogoNome` | Cosa sa fare | What she knows |
| `skills.riepilogo` | {integrate} integrate, {tue} tue | {integrate} built in, {tue} yours |
| `skills.riepilogoNessunaTua` | {integrate} integrate, nessuna tua | {integrate} built in, none yours |
| `skills.riepilogoErrore` | non si è potuto leggere | couldn't be read |
| `skills.comeInsegnare` | Per insegnargliene una, chiediglielo in chat. | To teach her one, ask in chat. |
| `skills.pannello` | Skill | Skills |
| `skills.tue` | Le tue | Yours |
| `skills.integrate` | Integrate | Built in |
| `skills.servizio` | Più {n} di servizio, che Jenny usa da sé. | Plus {n} internal ones Jenny uses on her own. |
| `skills.vuotaTitolo` | Non gliene hai ancora insegnata nessuna | You haven't taught her one yet |
| `skills.vuotaTesto` | Spiegale una cosa che le chiedi spesso: la trasforma in una skill. | Explain something you ask her often: she'll turn it into a skill. |
| `skills.chiedi` | Chiedi a Jenny | Ask Jenny |
| `skills.chiediPrompt` | Voglio insegnarti una nuova skill. Usa la skill "skill-creator" e guidami passo passo. | I want to teach you a new skill. Use the "skill-creator" skill and guide me step by step. |
| `skills.attiva` | Attiva la skill {name} | Turn on the {name} skill |
| `skills.integrataBloccata` | Viene con l'app: non si spegne da qui. | Comes with the app: it can't be turned off here. |
| `skills.erroreLettura` | L'elenco delle skill non si è potuto leggere. | The skill list couldn't be read. |
| `skills.riprova` | Riprova | Retry |

`test_i18n_parity.py` tiene allineate le due lingue da sé.

---

## Passo 1 — backend: chi è integrata, e il rifiuto

- [x] `utils/android_assets.py` — `bundled_skill_names() -> frozenset[str]`:
      le cartelle di primo livello delle voci `*/SKILL.md` di `_SKILLS_MANIFEST`.
      Accanto al manifest perché **è** il manifest; niente `importlib.resources`,
      che su Chaquopy è il walk silenzioso che `.agent/gotchas.md` vieta.
- [x] `webui/skills_api.py`
  - [x] `"bundled": name in bundled_skill_names()` in `_skill_payload` **e** in
        `_skill_payload_for` (sono due costruttori dello stesso dizionario:
        dimenticarne uno è il difetto più probabile di questo passo).
  - [x] `update_workspace_skill` e `delete_workspace_skill`: su una bundled,
        `PermissionError("bundled skill: re-extracted at startup")` **prima** di
        toccare il file. La rotta lo mappa già a 403. Anche la cancellazione:
        una integrata cancellata torna al riavvio, stesso difetto.
- [x] `tests/webui/test_skills_api.py`
  - [x] `bundled` vero per una skill scritta in `tmp_path/skills/cron`, falso per
        `tmp_path/skills/mia-skill`;
  - [x] update con `disabled=True` su `cron` → `PermissionError`, e il file
        **non è cambiato** (byte uguali prima e dopo);
  - [x] delete su `cron` → `PermissionError`, la cartella c'è ancora.
- [x] `tests/webui/test_skills_routes.py` — il 403 sul toggle di una bundled, dal
      dispatch.
- [x] Un test che incrocia `bundled_skill_names()` con le cartelle vere di
      `jenny/skills/`: `tests/utils/test_asset_manifests.py`, accanto ai due
      che tengono il manifest allineato al disco nei due versi.

Commit: *Skills say whether they ship with the app, and the ones that do can't be switched off*.

## Passo 2 — la divisione, in un modulo puro

- [x] `jenny/templates/ui/assets/shared/skills-view.js`, senza DOM e senza rete:
  - `dividiSkill(skills)` → `{ tue: [...], integrate: [...], servizio: n }`,
    con le regole della tabella sopra;
  - `riassuntoSkill(skill, locale)` → stringa o `''`;
  - `riepilogoSkill(divise, t)` → la frase della riga in cassetto (le due
    forme: con e senza tue);
  - `controllabile(skill)` → `!bundled && !internal && !locked`.
  Stesso idioma di `telegramSummary` in `shared/telegram-pairing.js`: la riga in
  cassetto e il pannello leggono la stessa funzione, e il banco la esegue.
- [x] **`_UI_MANIFEST`** in `utils/android_assets.py`: aggiungere
      `"assets/shared/skills-view.js"`. Senza, sul telefono il file non esiste e
      l'import di `mobile-settings.js` fallisce — l'intero cassetto, non solo le
      skill.
- [x] `tests/webui/test_skills_view_client.py` sotto node (idioma di
      `test_launcher_rank_client.py`): una voce per ciascuna riga della tabella;
      `riassuntoSkill` che non ripete il nome; `user_summary` nella lingua
      giusta e i due ripieghi; `riepilogoSkill` con 0 tue.

Commit: *Put the rule that splits skills into yours and built-in in one place*.

## Passo 3 — la riga in cassetto

- [x] `shared/api-client.js`
  - `listSkills()` → `GET /api/webui/skills`;
  - `setSkillDisabled(name, disabled)` →
    `GET /api/webui/skills/<name>/update?disabled=1|0` (è la forma della rotta
    esistente: `parse_flag`, v. `test_update_accepts_every_truthy_form_for_disabled`).
- [x] `mobile-settings.js`
  - [x] `CASSETTI.mani.sezioni`: `'skill'` **prima di** `'scheduling'`;
  - [x] in `render()` → `sezioni`: `skill: () => this._gruppo('skill',
        i18n.t('officina.gruppi.skill'), this._renderSkill())`;
  - [x] `_renderSkill()`: `this._riepilogo('skill', …riepilogoNome,
        settings.loading)` + la riga fioca `skills.comeInsegnare`;
  - [x] `_caricaRiepilogoSkill()`, forma di `_caricaRiepilogoStoria`: guardia
        `_gen`, `catch` che scrive `skills.riepilogoErrore` (mai un
        «Caricamento…» eterno), chiamato in `_wireSections` accanto a
        `_caricaRiepilogoTelegram`. Il pannello **non** riusa questa fetch:
        rilegge sempre all'apertura, perché una skill scritta da Jenny un
        minuto fa deve esserci.
- [x] `tests/webui/test_officina_cassetti_contract.py` passa da sé (la sezione
      ha un cassetto e uno solo). Aggiungere in `test_officina_righe_contract.py`:
  - [x] `_renderSkill` usa `_riepilogo(` e non contiene `toggle-switch`
        (in cassetto si legge);
  - [x] `_caricaRiepilogoSkill` ha `catch` e `riepilogoErrore`.

> **Fatto insieme al passo 4, in un commit solo.** Una riga con la freccina che
> non apre niente è una porta finta, e la regola del giro è che l'app non resta
> mai rotta a metà fra un commit e l'altro.

## Passo 4 — il pannello

- [x] `officina.html`: `drawer-skill` + `drawer-skill-body`, copiati dalla forma
      di `drawer-storia` (maniglia, titolo `skills.pannello`, chiudi), accanto a
      lui, con il commento che dice perché sta lì.
- [x] `mobile-settings.js`
  - [x] `_APRI_PANNELLO.skill = this._apriSkill`;
  - [x] `_apriSkill()`: prende `#drawer-skill-body` da **`document`** (il
        pannello vive fuori da `contentEl`: è la trappola già caduta una volta,
        `test_il_corpo_del_pannello_si_disegna_all_apertura`), ci scrive uno
        stato di caricamento, chiama `api.listSkills()`, passa per
        `dividiSkill`, disegna. Guardia `_gen`. Errore → `skills.erroreLettura`
        + bottone `skills.riprova` che richiama `_apriSkill`.
  - [x] riga di una skill:
        ```
        <div class="skill-riga">
          <button class="skill-riga-testo" aria-expanded="false">
            <span class="skill-riga-nome">nome</span>
            <span class="skill-riga-sotto">riassunto | ● motivo</span>
          </button>
          <label class="toggle-switch"> <input type=checkbox aria-label="skills.attiva"> …
            oppure  <i class="ti ti-lock" title="skills.integrataBloccata">
        </div>
        ```
        Il tocco sul testo scioglie il troncamento a due righe (`aria-expanded`
        si aggiorna). Niente foglio, niente dialogo: è l'unica cosa in più che la
        riga ha da dire.
  - [x] l'interruttore: ottimistico; `api.setSkillDisabled(name, !checked)`;
        in errore torna com'era e `showToast(i18n.t('settings.saveError'),
        'error')` — la stessa forma del rollback già a `mobile-settings.js:2500`.
        Durante la chiamata `disabled` sull'input, per non mandarne due.
  - [x] «Le tue» vuota: il blocco del mockup (glifo, titolo, testo, bottone).
        Il bottone chiude il pannello (`window.mobileApp.drawer.close('skill')`)
        e scrive `skills.chiediPrompt` nel composer. Per farlo serve una porta
        pubblica: in `mobile-app.js` il `_mandaInChat` che già esiste diventa
        raggiungibile come `mandaInChat(testo)` (il privato resta per
        `appsActions`, o si rinomina ovunque — uno dei due, non tutte e due).
        **Non** si manda: si scrive, e l'utente completa.
  - [x] in fondo: `skills.servizio` se `servizio > 0`, fioca.
- [x] `mobile-style.css`: `.skill-riga` (flex, `border-top` 0.5px tranne la
      prima, come `.cron-riga`), `.skill-riga-nome` in `var(--font-mono)` —
      `test_il_monospazio_viene_dal_token_e_non_da_un_nome_di_carattere` —,
      `.skill-riga-sotto` con `-webkit-line-clamp: 2` che cade quando
      `[aria-expanded="true"]`, il pallino `var(--warning)`. L'interruttore è
      il `.toggle-switch` che c'è già: niente di nuovo.
- [x] i18n `it.json`/`en.json`: tutte le chiavi della tabella.
- [x] `test_officina_righe_contract.py`:
  - [x] `drawer-skill` e `drawer-skill-body` esistono in `officina.html`;
  - [x] `_apriSkill` cerca il corpo con `document.getElementById`;
  - [x] **la decisione del 21/09 come banco**: il corpo di `_apriSkill` non
        contiene `deleteSkill`, `/delete`, `ti-trash`, `ti-edit` — se qualcuno
        rimette modifica o cancellazione, lo fa sapendolo;
  - [x] l'interruttore compare solo passando da `controllabile(`.

Commit: *The Skills panel: read them all, switch yours on and off*.

## Passo 5 — pulizia e documenti

- [x] i18n: cancellare le chiavi morte `apps.newSkill`, `deleteSkillConfirm`,
      `createSkillPrompt`, `deleteSkillFailed`, `roomSkills`, `searchSkills`,
      `yourSkills`, `builtInSkills`, `builtInLocked`, `toggleSkill` (riverificare
      zero riferimenti con `grep -rna` al momento, v. la memoria su
      `mobile-chat.js` che fa il binario).
- [x] `aria-label="Cerca app, skill…"` statico in `index.html:112` e
      `officina.html:361` → «Cerca un'app…», come il placeholder.
- [x] `docs/using/skills.md` — riscrivere «Managing skills from the Apps tab»
      in «Seeing and switching skills in Hands» e la tabella dei tre livelli (non c'è più
      Developer mode: `internal` sono contate, non mostrate). L'«Honesty note»
      diventa una frase sola al passato: ora l'interruttore sulle integrate non
      c'è, e il backend rifiuta. **Stesso percorso, stesso H1**: il sito
      (`jenny-site`) ne deriva URL e titolo.
- [x] `docs/using/app-launcher.md:12` — «they live in the Apps tab's own Skills
      room» → Mani. Il resto del file (skill nel cassetto, scheda Apps, Hide)
      l'ha riscritto un'altra sessione in `d9ebb0b`, lasciando apposta questa
      frase a questo passo.
- [x] `docs/README.md` — la riga 37 del launcher l'ha già corretta `d9ebb0b`;
      qui è cambiata la riga 31, quella di Skills, che prometteva di spiegare
      «perché le modifiche alle integrate non sopravvivono al riavvio»: ora
      non si possono più fare, e la pagina spiega il lucchetto.
- [x] `officina-tavole-plan.md` e `officina-allineamento-tavola.md`: la porta
      delle skill in Mani è fatta, con la data e un rimando a questo file.

Commit: *Drop the skill strings nobody reads, and tell the docs where skills went*.

## Passo 6 — prova (24/09/2026)

Suite:

- [x] `ruff check jenny/ tests/` — pulito.
- [x] `python3 -m pytest -q` su 3.14: **10617 passati**, dopo un rosso che la
      suite webui da sola non vedeva — `tests/channels/test_websocket_http_routes.py`
      confronta per uguaglianza l'intero payload di una skill, e il campo nuovo
      lo rompeva (`de4c0d3`). Venv 3.11: **10608 passati, 0 rossi**.
- [x] `npx pyright jenny/bus jenny/command jenny/runtime jenny/session` — 0 errori.

**Sul Mac, UI vera su un gateway vero** (`run_gateway()` in un workspace di
prova nello scratchpad, provider finto, le dieci integrate copiate e due tue
inventate, una con un requisito mancante):

- [x] Mani: «Skill · Cosa sa fare  5 integrate, 2 tue ›», fra Telegram e
      «Quando agisce da sola».
- [x] Pannello: le tue con l'interruttore, la non disponibile col pallino e
      «Missing: …» in testo; le integrate col lucchetto e il `user_summary`
      italiano; «Più 5 di servizio» in fondo; il tocco sul testo scioglie le
      due righe.
- [x] Interruttore spento → `disabled: true` nel frontmatter, sul disco.
- [x] Errore del server simulato (fetch sostituita solo per `/update?`) →
      l'interruttore torna com'era e compare «Errore nel salvataggio».
- [x] `…/skills/cron/update?disabled=1` → 403, md5 di `cron/SKILL.md` uguale
      prima e dopo.
- [x] «Le tue» vuota (skill spostate via, pannello riaperto: rilegge davvero)
      → il blocco; «Chiedi a Jenny» chiude il pannello, va in Console, la frase
      è nel composer col fuoco, e non è partita.
- [x] Tema chiaro (Pietra) e scuro (Chanel): pallino, lucchetto e interruttori
      si leggono.

**Sul telefono** — release firmata da un worktree pulito su `de4c0d3`
(`grep '[jenny]'` vuoto), `adb install -r` → Success; poi guidata dal Mac con
`adb forward` + `#bs=`, cioè il JS dell'APK sui dati veri:

- [x] «5 integrate, 4 tue» — i numeri veri; il pannello elenca le quattro tue
      con l'interruttore e le descrizioni.
- [ ] **Non fatto sul telefono, di proposito:** spegnere una skill vera.
      `update_skill` riscrive il frontmatter via YAML (le descrizioni lunghe
      vanno a capo, visto sul Mac): sarebbe una modifica a un file dell'utente
      per provare un codice già provato sul Mac. Se serve, con una skill usa e
      getta messa con `su` (e le categorie MLS, v. memoria).
- [ ] **Non verificato:** che `/skill` in chat non elenchi una spenta al turno
      dopo, e che resti spenta dopo force-stop + riavvio. Il primo lo
      garantisce `list_skills` (filtra i `disabled`, testato in
      `tests/agent/test_skills_loader.py`), il secondo il fatto che l'estrazione
      non tocca le cartelle fuori dal manifest — ma nessuno dei due è stato
      guardato sul telefono.

## Rischi da tenere d'occhio

- **Due costruttori del payload** (`_skill_payload`, `_skill_payload_for`):
  `bundled` in uno solo e la risposta del toggle perde il campo — il pannello
  la ignora oggi, ma è la forma del difetto che non si vede.
- **Il manifest UI**: un file JS nuovo non listato funziona sul Mac (il gateway
  di sviluppo serve dal disco) e non esiste sul telefono.
- **Un'altra sessione sullo stesso albero** (v. memoria): i passi toccano
  `mobile-settings.js` e i due i18n, che sono i file più contesi.
