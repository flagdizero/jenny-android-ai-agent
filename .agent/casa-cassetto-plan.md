# Il cassetto delle app in casa

> **Di cosa parla questo file.** Del **cassetto in casa**: il pannello con le
> app del telefono, le Jenny App e le skill, che oggi in casa non esiste (c'è
> solo in officina). La domanda del documento non è *se* farlo — è **con cosa
> costruirlo sotto**: Kotlin nativo, o il cassetto JavaScript che l'officina ha
> già. Per chi lo usa le due strade danno **la stessa cosa**.
>
> Il file si chiamava `casa-cassetto-nativo-plan.md` finché la risposta non è
> arrivata. Rinominato il 20/09/2026, perché un piano che si chiama «nativo» e
> conclude di non farlo nativo è una trappola per chi lo riapre fra un mese.

**Stato: il passo 0 è stato fatto, e ha risposto di no al Kotlin.**

> **Esito del 20/09/2026 — misurato sul Titan 2.** Il gateway è su in **~2,0 s**
> a caldo (tre giri: 1,93 / 1,94 / 2,14 s) e la casa è **completamente
> disegnata e usabile a 2,5 s**. A freddo, da `BOOT_COMPLETED` al gateway in
> ascolto passano **7,7 s**, ma **5,2 s sono la coda dei broadcast di Android**,
> prima che il processo di Jenny esista: la parte di Jenny è 2,5 s, la stessa
> di sopra.
>
> Il tetto di 90 s in `BOOT_POLL_TIMEOUT_MS` non viene mai nemmeno sfiorato.
>
> **Quindi l'argomento che giustificava il Kotlin non regge**: non c'è nessuna
> finestra lunga in cui il telefono non apre niente. La raccomandazione si
> ribalta — v. «La risposta», qui sotto.

---

## La risposta

**Portare in casa il cassetto che l'officina ha già**, non scriverne uno nativo.

Tre ragioni, in ordine:

1. **Il motivo per il nativo era il boot, e il boot non è un problema** (2,5 s,
   misurati). Restano la fluidità delle icone e il gesto: due cose vere, ma che
   non valgono giorni di lavoro contro mezza giornata.
2. **Hai chiesto «tutto, come in officina»** — app, Jenny App e skill insieme.
   Il cassetto JS **è già esattamente quello**. Il nativo dovrebbe
   ricostruirlo, e per la parte non-Android dipenderebbe comunque da Python.
3. **Il Kotlin non ha un solo test** (v. problema 3), mentre il cassetto JS ha
   due banchi e un modulo di ranking puro con undici test propri. Spostarlo di
   là vorrebbe dire perdere la rete per guadagnare millisecondi.

Resta valido e **va fatto comunque** il passo 1: il conteggio d'uso sta in un
posto che Android svuota. È un difetto di adesso, indipendente dal cassetto.

I passi 2-7 di questo documento restano scritti perché il ragionamento serva se
un giorno il quadro cambia (per esempio se il gateway si appesantisse), ma
**non sono il lavoro consigliato oggi**.

---

## Il piano originale, per memoria

Quel che segue è la proposta nativa com'era prima della misura.

La casa non ha un cassetto delle app. L'officina sì — `mobile-launcher.js`,
1.105 righe, più `shared/launcher-rank.js` — e ci si arriva dalla porta
«Cassetto delle app» dentro Mani. In casa non c'è niente: il composer ha la
graffetta e il tasto manda, e basta (`index.html:126`), che è voluto.

## Le due decisioni prese

| | scelta |
|---|---|
| **Cosa c'è dentro** | **tutto**: app Android, Jenny App e skill, come in officina |
| **Come si apre** | **passata verso l'alto dal bordo basso** |

Le due vanno lette insieme, perché la prima **sposta il motivo** per cui si
scrive in Kotlin, e la seconda è quella che costa di più in misure.

---

## Quel che «tutto» cambia, detto chiaro

L'argomento più forte per il nativo era questo: la WebView non parte finché il
gateway Python non risponde (`MainActivity.kt:690`, `waitForGatewayThenLoad`), e
in quella finestra il telefono non apre niente. Un cassetto che legge
`PackageManager` direttamente sarebbe pronto subito.

**Ma skill e Jenny App esistono solo dentro Python.** Chiedendo un cassetto
completo, la parte non-Android non può comunque esserci prima che il gateway
risponda. Quindi l'argomento del boot non sparisce ma **si dimezza**: il
cassetto nativo è pronto subito per le app del telefono, e completo poco dopo.

Vale ancora la pena? Sì, e per una ragione che regge da sola: **oggi in quella
finestra non c'è niente**, solo una rotella. «Le app del telefono subito, il
resto fra un attimo» è meglio di «niente per N secondi», qualunque sia N.

Ma va costruito nella versione onesta, non in quella pigra:

- le app Android compaiono **subito**, da `PackageManager`;
- la fascia di skill e Jenny App **si dichiara in attesa** con una riga sua,
  invece di essere assente e poi comparire di soppiatto.

La differenza fra le due versioni è tutta qui: un cassetto che **cambia
contenuto in silenzio** sotto le mani è un difetto; uno che dice «sto ancora
svegliando Jenny» è un'informazione. È la stessa regola di `checkLines` in
`shared/update-flow.js` — non far passare «non lo so ancora» per «non c'è».

---

## Passo 0 — La misura che dimensiona tutto ✅ FATTO

### I numeri, 20/09/2026, Titan 2 (1440×1440, 400 dpi, `navigation_mode=2`)

| | |
|---|---|
| app lanciabili | **70** (su 271 pacchetti installati) |
| gateway in ascolto, a caldo | **1,93 / 1,94 / 2,14 s** (tre giri, `force-stop` → socket) |
| casa disegnata e usabile | **2,5 s** (a 1,5 s c'è ancora la rotella) |
| `BOOT_COMPLETED` → gateway, a freddo | **7,72 s** |
| …di cui coda broadcast di Android | **5,19 s** (il processo di Jenny non esiste ancora) |
| …di cui Jenny | **2,54 s** |

Cronologia del boot a freddo, dal buffer di log:

```
20:34:12.324  Android posta BOOT_COMPLETED
20:34:17.511  Jenny lo riceve e avvia il servizio      (+5,19 s)
20:34:18.596  parte run_gateway (Python)               (+6,27 s)
20:34:19.1→5  estrazione asset                          (~0,6 s)
20:34:20.047  WebSocket in ascolto su 127.0.0.1:18790  (+7,72 s)
```

**Come leggerli.** Contro la tabella qui sotto siamo nel ramo «2-3 s»: il
cassetto nativo non è una necessità funzionale. E il pezzo lungo del boot a
freddo non è codice nostro — è Android che ci mette 5,2 s a dire a Jenny che il
telefono è acceso. Nessun cassetto, nativo o no, tocca quel numero.

### Una cosa vista per strada: l'estrazione degli asset gira due volte

Nel log, `sync_workspace_templates` estrae gli stessi file prima in
`/data/user/0/com.flagdizero.jenny/…` e poi in `/data/data/com.flagdizero.jenny/…`.
**Sono la stessa cartella**: verificato creando un file da un percorso e
leggendolo dall'altro. Il secondo giro costa ~110 ms a ogni avvio e non produce
niente. Non è un lavoro del cassetto — va annotato e basta.

### Il ragionamento che ha portato alla misura

Il tempo vero di boot del gateway sul Titan 2 non era mai stato misurato. Il
codice conosce solo il tetto:

```kotlin
// MainActivity.kt:67-68
private const val BOOT_POLL_INTERVAL_MS = 250L
private const val BOOT_POLL_TIMEOUT_MS = 90_000L
```

| Se il gateway risponde in… | Allora |
|---|---|
| **> 8-10 s** | il nativo è una necessità: il telefono è inusabile come telefono a ogni riavvio |
| **2-3 s** | resta un guadagno di fluidità, non di funzione. Vale comunque la pena, ma il piano si può fare con più calma e il passo 1 da solo copre già il rischio peggiore |

A telefono sbloccato:

```bash
adb shell am force-stop com.flagdizero.jenny
adb logcat -c
adb shell am start -n com.flagdizero.jenny/.MainActivity
adb logcat -d | grep -E "MainActivity|GatewayService|Gateway socket"
```

Il delta fra `onCreate` e il primo `loadWebView()`. Tre volte: a freddo dopo un
riavvio del telefono, a caldo dopo un force-stop, e una a batteria bassa con
Doze attivo. Quello che conta è il primo.

Nella stessa sessione, l'altra cifra che manca:

```bash
adb shell pm list packages | wc -l     # quante app ci sono
```

e il peso della risposta di `/api/android/apps`, che oggi porta ogni icona come
PNG base64 dentro il JSON.

---

## Cosa c'è già, e non va riscritto

`InstalledAppsBridge.kt` (127 righe) espone già tutto:

| metodo | fa |
|---|---|
| `listInstalledApps()` | query `ACTION_MAIN`/`CATEGORY_LAUNCHER`, dedup per package, ordinate per etichetta |
| `launchApp(pkg)` | `getLaunchIntentForPackage` + `FLAG_ACTIVITY_NEW_TASK` |
| `uninstallApp(pkg)` | dialogo di sistema |
| `openAppInfo(pkg)` | schermata Info app |

Oggi però fa una cosa che serve **solo** alla WebView: codifica ogni icona in
PNG base64 (`drawableToBase64Png`, 96 px) e la mette nel JSON, che poi
attraversa Chaquopy → `json.loads` → HTTP → N data-URL da decodificare. Un
`RecyclerView` nativo usa il `Drawable` così com'è: zero codifica, zero copia.

Il layout regge l'innesto: `activity_main.xml` è già un `ConstraintLayout` con
WebView + loading + error. Il cassetto è un quarto fratello.

E il contratto col nativo esiste già ed è **lo stesso per i due gusci**:
entrambi espongono `window.mobileApp` (`casa-app.js:214`, `mobile-app.js:107`),
documentato in testa a `casa-app.js` come la superficie che Kotlin chiama —
`onNativeReady()`, `goHome()`, `onPackageChanged()`.

---

## La passata dal bordo basso: perché costa meno di quanto sembri

Il Titan 2 è 1440×1440 con `navigation_mode = 2` (gesture attiva, misurato in
`apps-drawer-handover.md`). La passata verso l'alto dal bordo basso **è** il
gesto di home del sistema: le due cose si contendono gli stessi pixel.

**Ma qui c'è un'attenuante che altrove non ci sarebbe, ed è decisiva.** Jenny
**è** il launcher. Quando la passata scappa al sistema, il sistema consegna
l'intent HOME… a Jenny stessa, che lo gira alla pagina:

```kotlin
// MainActivity.kt:635
if (intent?.hasCategory(Intent.CATEGORY_HOME) == true) {
    webView?.evaluateJavascript("if (window.mobileApp) window.mobileApp.goHome()") {}
}
```

e in casa `goHome()` chiude quel che sta sopra e torna nella conversazione
(`casa-app.js:666`).

Quindi **il modo peggiore in cui questo gesto può fallire è "sei tornato alla
conversazione invece di aprire il cassetto"** — fastidioso, non distruttivo. Su
un telefono normale lo stesso errore ti butterebbe fuori dall'app.

Due conseguenze pratiche:

1. Il margine si può tarare **provandolo**, senza paura di perdere niente.
2. Conviene farne una virtù: se il cassetto è aperto, **HOME lo chiude**. Il
   gesto scappato e il gesto riuscito finiscono nello stesso posto invece di
   contraddirsi.

Il lavoro vero: `setSystemGestureExclusionRects` sulla striscia bassa mentre il
cassetto è chiuso, e la ritaratura del margine sul Titan 2 — gli 8 px CSS del
cassetto JS sono un numero **dell'emulatore**, e `getBottomGestureInset()` sul
Titan 2 non è mai stato letto (handover §7.2, §7.3).

---

## I problemi che restano

### 1. Il conteggio d'uso sta nel posto sbagliato, e quel posto perde i dati

`UsageRanking` (`shared/launcher-rank.js:113`) scrive la frecency nel
`localStorage` della WebView, chiave `launcher-usage`.

Kotlin non può leggerlo. E lo dice un commento di `MainActivity.kt` scritto per
un altro motivo:

> «il localStorage della WebView non sopravvive al kill (persistenza asincrona
> di Chromium), le SharedPreferences sì»

Quindi **il dato è già fragile oggi**, su un'app che il sistema uccide di
routine. Il cassetto nativo non crea il problema: lo rende impossibile da
ignorare. Va spostato in `SharedPreferences`, con migrazione una-tantum dal
vecchio valore.

**È il passo che vale di più per quanto costa, e si può fare da solo.**

### 2. Due cassetti che fanno la stessa cosa

Se la casa prende il nativo e l'officina tiene il suo JS, esistono due
implementazioni di ricerca, ordinamento e avvio. È il difetto che questo codice
evita apposta altrove, e lo dice a voce in `shared/update-flow.js`:

> «Estratta e non ricopiata: una seconda copia di una macchina a stati non
> sbaglia subito, sbaglia dopo, quando una delle due impara qualcosa che
> l'altra non sa.»

Avendo scelto «tutto», i due cassetti avrebbero ora **lo stesso contenuto**, il
che rende la doppia copia più difficile da giustificare, non meno. Uscita
preferita: il nativo serve tutti e due i gusci, e l'officina lo apre via
`JennyNative` invece di disegnarne uno suo (passo 6).

### 3. Nessun banco può vedere il Kotlin

```
find android -path "*/test/*" -o -path "*/androidTest/*"   → vuoto
grep testImplementation android/app/build.gradle.kts       → niente
```

Zero test Kotlin su 10.796 righe già esistenti. Il cassetto JS invece ha due
banchi, e `launcher-rank.js` è un modulo **puro** con undici test propri che
girano sotto node senza telefono.

Riscrivere il ranking in Kotlin butta via quella rete. Risposta: **non
riscriverlo.** Il nativo consuma il contratto, non ricopia il codice (passo 3).
Se un giorno deve comunque esistere in Kotlin, prima entrano `kotlin.test` e
JUnit nel modulo.

---

## I passi

Ognuno è spedibile da solo e lascia l'app intera.

**Passo 0 — le misure.** Tempo di boot del gateway (tre volte) e peso
dell'elenco app. Si scrivono qui.

**Passo 1 — il conteggio d'uso esce dal `localStorage`.** Va in
`SharedPreferences` attraverso un metodo nuovo su `JennyNative`, con migrazione
dal vecchio valore. **Nessuna riga di UI cambia**, e la fragilità del problema 1
sparisce anche se il resto non si facesse mai.

**Passo 2 — `listInstalledApps()` impara a tacere sulle icone.** Un parametro
`withIcons: Boolean`: il nativo chiama senza, la WebUI con.

**Passo 3 — il ranking resta puro.** Si porta il contratto, non il codice; i
undici test restano il documento di riferimento.

**Passo 4 — il `View` e le due popolazioni.** `RecyclerView` come quarto figlio
di `activity_main.xml`. App Android subito; skill e Jenny App con la loro riga
«in attesa» finché il gateway non risponde, e mai comparse in silenzio.

**Passo 5 — la passata e il margine.** `setSystemGestureExclusionRects` a
cassetto chiuso, ritaratura sul Titan 2, e HOME che chiude il cassetto quando è
aperto.

**Passo 6 — la ricerca e la tastiera fisica.** Type-ahead e rotella. Il handover
(§2) avverte che **non si sa quali eventi produca la rotella del Titan 2**: va
misurato prima, non dopo.

**Passo 7 — l'officina smette di avere il suo.** Chiude il problema 2.

---

## Cosa NON è stabilito

- Il tempo di boot vero del gateway, e quante app ci sono sul telefono.
- Il valore vero di `getBottomGestureInset()` sul Titan 2.
- Che eventi manda la rotella — aperto dal 31/08/2026.
- Se il passo 7 si fa davvero, o se la doppia copia si accetta e si dichiara.

## Vedi anche

- [`apps-drawer-plan.md`](./apps-drawer-plan.md) — le decisioni del cassetto JS
- [`apps-drawer-handover.md`](./apps-drawer-handover.md) — le misure sul telefono
- [`casa-plan.md`](./casa-plan.md) — perché la casa è spoglia
