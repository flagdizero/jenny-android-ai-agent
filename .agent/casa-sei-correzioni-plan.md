# Sei correzioni alla casa — piano

**Stato: decisioni prese, item 1-2-5 fatti.** Analisi del 20/09/2026 sul
Titan 2 (WebView 143.0.7499.192, schermo 1436×1440, densità 400 dpi).

## Le decisioni

| item | scelta |
|---|---|
| 1 — icona | **ingranaggio**, destinazione «Tu e Jenny» invariata *(fatto)* |
| 2 — sfocatura | **sì**, da confermare sul telefono *(fatto, non verificato)* |
| 3+4 — schermata App e tocco lungo | schermata via, menu nel cassetto |
| 3 — app nascoste | **ricompaiono tutte**, va bene |
| 4 — Jenny App | **stesso menu** di oggi |
| 5 — campo di testo | raggio fisso, graffetta **al centro** *(fatto)* |
| 6 — larghezza | **scansare la mascotte solo dove c'è** |

## Passo 0 — FATTO: il viewport è 574,4 px CSS

Misurato dall'immagine, non dedotto: il tasto manda (`.casa-send`, **40×40 px
CSS** dichiarati) occupa **esattamente 100×100 px fisici** → **DPR 2,500**; e il
suo margine destro misura 30 px fisici = 12,0 CSS, cioè il `padding` del
composer. Due conferme indipendenti sullo stesso scatto.

**574,4 px CSS.** La misura in memoria (590, DPR 2,44) era del 17/08 e nel
frattempo la densità è 400 e c'è un *Override size* di 1436×1440: corretta.

Conseguenze, con i numeri veri: filo largo **538,4**; tetto di Jenny 88% =
**473,8**; **82,6 px CSS sprecati a destra, il 14% dello schermo**. La mascotte
(`--jenny-size: 120`, `OUT_RATIO 0,25`) ha la scatola che comincia a **484,4** e
intrude nei **105 px CSS in fondo** al filo.

## Nota di metodo: le decisioni vecchie si rimisurano

Questo documento cita parecchi commenti e banchi già nel codice. **Nessuno di
essi vale come legge**: sono fotografie di quando furono scritti, e almeno due
sono già scaduti (v. item 2 e item 6). Dove una decisione vecchia si oppone a
una di queste sei richieste, il piano dice **come rimisurarla**, non «non si
può».

---

## Passo 0 — la misura che manca e serve a due item

La larghezza vera del viewport della WebView in px CSS. Serve agli item 5 e 6,
e **non la so con certezza**:

- calcolata dalla densità: 1436 px / (400/160) = **574,4 px CSS**;
- una misura in memoria del 2026-08 dice **590×566**, con DPR 2,44.

Le due non coincidono, e nel frattempo `wm size` riporta un **Override size**
(1436×1440 contro 1440×1440 fisici), che può averla cambiata. Senza il numero
giusto, ogni percentuale degli item 5 e 6 è stimata.

Si legge in un colpo solo con la pagina aperta:
`innerWidth`, `devicePixelRatio`, e `getBoundingClientRect()` di `.casa-thread`
e `.casa-jenny`. Sul telefono serve una console sulla WebView; in alternativa
il banco locale portato a quella larghezza dà gli stessi rapporti.

---

## 1. Il tasto con l'omino deve essere un ingranaggio

**Dov'è.** `index.html:91`, `<button class="casa-door" id="casa-door">` con
`<i class="ti ti-user">`, in alto a destra. Tocco → «Tu e Jenny»; **tocco lungo
→ officina**.

**Il lavoro è una riga**: `ti-user` → `ti-settings`.

**Ma c'è una domanda da decidere prima**, e non è cosmetica. Il commento accanto
dice che fino al 19/09 quel bottone *era* una chiave inglese e portava dritto in
officina, e che è stato cambiato perché la tavola mette l'officina in fondo a
«Tu e Jenny». Un ingranaggio è l'icona universale di «impostazioni»: chi lo vede
si aspetta le impostazioni, e trova una pagina che parla di lei e di te.

Tre uscite, in ordine di onestà:

- **a** — ingranaggio, e «Tu e Jenny» diventa davvero la pagina delle
  impostazioni della casa (lo è già per metà: modello, backup, aggiornamenti).
  Nessuna bugia, nessun lavoro oltre l'icona.
- **b** — ingranaggio che porta **dritto in officina**, e «Tu e Jenny» si
  raggiunge da dentro. È il ripristino di com'era prima del 19/09.
- **c** — un'icona che non promette impostazioni (`ti-adjustments`,
  `ti-dots`). Evita il problema invece di risolverlo.

Io farei **a**: è quello che già è, detto con l'icona giusta.

---

## 2. Il cassetto deve sfocare quello che sta sotto

**Dov'è.** `.launcher-scrim` in `mobile-style.css`: oggi è un velo nero piatto,
`rgba(0,0,0,0.55)`, con la sola opacità animata.

**Il lavoro è due righe** sullo scrim:
`backdrop-filter: blur(12px)` più il prefisso `-webkit-`.

**L'obiezione precedente, e perché è probabilmente scaduta.** Nello stesso
foglio, su `.swipe-scrim`, c'è scritto:

> «no filter/backdrop-filter, so WebView never renders transparent regions
> (icons) black»

Tre ragioni per non prenderla per buona:

1. **Parla di un altro elemento.** `.swipe-scrim` copre il contenuto durante il
   gesto di cambio vista e **anima l'opacità a ogni frame**. Lo scrim del
   cassetto sfuma una volta e poi sta fermo: è il caso facile, non quello duro.
2. **Parla di un altro WebView.** Oggi sul telefono gira **143.0.7499.192**
   (misurato). `backdrop-filter` lì è maturo; il difetto delle regioni
   trasparenti rese nere è roba di versioni molto più vecchie.
3. Il velo nero e la sfocatura non sono alternativi: si tengono tutti e due,
   con il nero abbassato (0,55 → ~0,35), altrimenti la sfocatura non si vede.

**Come si chiude la questione**: si mette, si guarda **sul telefono** (non sul
banco: è un difetto di compositing del dispositivo), e si controllano le due
cose che il commento vecchio denunciava — le icone delle app nella lista e la
mascotte, che è il nodo trasparente più grosso della casa. Se tornano nere, la
riga si toglie e il commento si aggiorna con la data nuova invece di restare un
divieto senza scadenza.

**Costo del rischio**: una build. Non tocca nessuna logica.

---

## 3. e 4. — sono **un lavoro solo**, e vanno fatti insieme

Le due richieste si incastrano, e farle separate è più lavoro e più rischio.

### Cosa c'è oggi

- **La schermata App** (`view-apps` in officina, `mobile-apps.js`, **1.730
  righe**) con tre stanze: skill, Jenny App, app Android.
- Dentro quella schermata, un **tocco lungo su una app apre già il foglio che
  chiedi**: `showAndroidAppSheet` (riga ~1053 il gancio, ~1390 il menu) e le sue
  voci sono esattamente **Apri · Info app · Disinstalla**, più **Nascondi /
  Mostra**.
- Il ponte nativo ha già i metodi: `launchApp`, `openAppInfo`, `uninstallApp`
  (`InstalledAppsBridge.kt`).
- `shared/longpress.js` esiste e lo usano già cinque file. **Il cassetto no**
  (zero occorrenze in `mobile-launcher.js`).

Quindi l'item 4 **non è codice nuovo**: è un foglio che esiste, agganciato a
righe che non lo chiamano.

### Il nodo da sciogliere per primo

**`AppsController` è la sorgente dati del cassetto in tutti e due i gusci** —
gliel'ho dato io ieri. Buttare «la schermata» senza separare prima i dati
spegne il cassetto in casa e in officina.

L'ordine obbligato è:

1. **Estrarre la metà dati** di `AppsController` in `shared/apps-source.js`: le
   quattro fetch, le tre liste, `addChangeListener`, `ensureLoaded`,
   `listsFailed`, `retryFailedLists`, `launcherEntries`. È lavoro già mezzo
   fatto — quella metà **non tocca il DOM** e `render()` esce prima di toccarlo
   (commentato sul posto).
2. **Portare il foglio per-app nel cassetto**: `setupLongPress` sulle righe,
   e il foglio riusato. Qui si decide anche il **tocco lungo su una Jenny App**,
   che oggi ha un suo foglio (`showJennyAppSheet`).
3. **Togliere «Nascondi»** — v. sotto.
4. **Cancellare la schermata**: `view-apps` dal markup, `mobile-apps.js`, la
   voce `apps` da `controllerFactories` e da `CASSETTI.mani.porte`, la riga
   «Gestisci app e skill» dal foglio (che a quel punto non ha più dove portare —
   ed è già nascosta in casa, v. `_manageAvailable`), e i banchi che la
   guardano.

### «Nascondi» va via davvero: cosa si porta dietro

Superficie misurata:

| dove | cosa |
|---|---|
| `mobile-apps.js` | ~40 righe: `hiddenPackages`, `loadHiddenApps`, `_showHidden`, le azioni `hide`/`unhide` |
| `ws_http.py:714-724` | due rotte: `_handle_webui_hidden_apps` e `..._update` |
| `mobile-launcher.js` | la guardia «le app nascoste restano nascoste» dentro `launcherEntries` |
| `tests/webui/test_apps_rooms_contract.py` | 8 righe |
| i18n | `apps.hide`, `apps.show` |

**Una domanda da decidere**: le app già nascoste. Sul telefono c'è un elenco
salvato; buttando la funzione, quelle app **ricompaiono** nel cassetto. È il
comportamento giusto (non esiste più un posto dove rivelarle), ma è un cambio
visibile e va detto, non scoperto. Da leggere prima quante sono.

### Costo

Il grosso è il passo 1. Il resto è togliere. Stima: il doppio del lavoro di
ieri, e per metà è **cancellazione** — che è la parte che riduce il codice.

---

## 5. Il campo di testo a più righe

**Il difetto è misurato e la causa è certa.**

`.casa-field` (casa-style.css) ha `border-radius: var(--radius-pill)` = **999px**.
A una riga (40px) è una capsula. A tre righe (~100px) il raggio si taglia a
metà altezza: gli estremi diventano due semicerchi da 50px, e la forma si
sfonda. È quello che si vede sul telefono adesso.

**La soluzione è già scritta in questo repo**, in officina, con tanto di
commento:

```css
/* .compose-pill — mobile-style.css
   Radius fisso = metà del min-height: a una riga è identico alla pillola,
   in multiriga i corner restano 25px (rettangolo arrotondato, stile WhatsApp). */
border-radius: 25px;
```

Quindi: **`--radius-pill` → un valore fisso pari a metà di `min-height`**. In
casa `min-height` è 40px → **20px**.

**Il tasto allega.** `.casa-field` ha `align-items: flex-end`, quindi la
graffetta (40px alta) si incolla al fondo mentre il campo cresce. L'officina usa
`align-items: center`. Le due danno risultati diversi su tre righe e **nessuna
delle due è ovviamente giusta**: `center` la lascia a mezz'aria, `flex-end` la
appoggia all'ultima riga. Da guardare a schermo prima di scegliere; se si vuole
copiare l'officina, è `center`.

Nota: `.casa-composer` ha anch'esso `align-items: flex-end`, ed è **giusto** —
è quello che tiene i due tondi (cassetto e manda) in fondo mentre la pillola
cresce. Quello non si tocca.

---

## 6. I messaggi di Jenny devono prendere più larghezza

**I numeri.**

| | px CSS |
|---|---|
| `.casa-thread` padding | 18 per lato |
| `.casa-msg-jenny` | `max-width: 88%` |
| viewport (da confermare, v. passo 0) | ~574 |
| → larghezza utile del filo | ~538 |
| → tetto del messaggio | ~473 |
| → margine destro sprecato | ~65, cioè **oltre un decimo dello schermo** |

**E la mascotte.** `.casa-jenny` è `position: absolute`, larga
`--jenny-size` (120 di serie), ancorata a destra con `right: -size*0.25` quando
è fuori (`OUT_RATIO = 0.25`, `shared/mascot.js`). Il suo riquadro comincia
quindi a ~**484 px CSS**, e il disegno vero un po' più a destra per via dei
margini trasparenti del webp.

**Ecco il punto vero, ed è la cosa che l'analisi ha cambiato rispetto alla tua
descrizione.** Quel `max-width: 88%` non è padding decorativo: è **lo scansare
la mascotte**. Solo che la mascotte sta **in fondo a destra** e il tetto è
applicato a **tutti** i messaggi, anche a quelli in cima allo schermo dove lei
non c'è. Si paga un decimo di larghezza su ogni riga per un ostacolo che tocca
solo le ultime due.

Tre uscite:

- **a — tetto più alto e basta.** `max-width: 88%` → ~93%, padding 18 → 12. Si
  recupera poco (~30px), l'ostacolo resta mal risolto, ma è una riga e non può
  rompere niente.
- **b — niente tetto, e la mascotte si sposta se dà fastidio** *(consigliata)*.
  Si toglie `max-width` a Jenny; il testo prende tutta la larghezza del filo.
  Sotto la mascotte ci passa — ma lei **si mette via con un tocco** (è la sua
  funzione, `dock`/`out`) e si trascina. Il testo che le passa dietro è
  l'eccezione, non la regola.
- **c — scansarla solo dove c'è.** Un `padding-right` sugli ultimi messaggi, o
  un `shape-outside`. È la soluzione «giusta» e la più fragile: dipende da
  quanti messaggi stanno in fondo, che cambia a ogni turno.

Il padding sinistro (18px) si può portare a 12 in tutti e tre i casi: sul lato
sinistro non c'è niente da scansare.

---

## Ordine consigliato

1. **Passo 0** — la larghezza vera. Cinque minuti, e sblocca 5 e 6.
2. **Item 5** (raggio fisso) e **item 1** (icona): due righe, zero rischio.
3. **Item 6**: decisa la lettera, una o due righe.
4. **Item 2** (sfocatura): una build, e la verifica va fatta **sul telefono**.
5. **Item 3+4** insieme, nell'ordine dei quattro passi sopra.

I primi quattro punti sono mezza giornata in tutto. Il quinto è il lavoro vero.

## Decisioni che aspettano te

- Item 1: **a**, **b** o **c**.
- Item 5: graffetta a metà (come l'officina) o in fondo (com'è adesso).
- Item 6: **a**, **b** o **c**.
- Item 3: le app che hai nascosto **ricompariranno**. Va bene?
- Item 4: il tocco lungo su una **Jenny App** — stesso foglio di oggi
  (`showJennyAppSheet`), o niente?
