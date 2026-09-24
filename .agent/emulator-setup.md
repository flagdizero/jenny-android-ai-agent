# L'emulatore quadrato — creazione e uso

Un AVD che imita la geometria del Titan 2 (1440×1440 fisici, 480 dpi → 480×480 px
CSS) per misurare inset e gesture quando il telefono non è collegato.

**Non è il Titan 2.** Stesso schermo, altro Android (API 37 / Android 17), altra
shell di sistema, altre impostazioni di navigazione. Vale per capire le grandezze
in gioco e per far girare la UI; non chiude una domanda sul telefono vero.

---

## Prerequisiti d'ambiente

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
```

Serve ogni volta: **la variabile non è nell'ambiente e non esiste
`android/local.properties`** — senza il prefisso, `gradlew` muore con
"SDK location not found".

`emulator` non è nel PATH: si invoca come `$ANDROID_HOME/emulator/emulator`.

**`avdmanager` non è installato in questo SDK** (niente `cmdline-tools/`, niente
`tools/bin/`). Per questo l'AVD qui sotto si crea a mano scrivendo i due file
che `avdmanager` avrebbe scritto: l'emulatore crea da sé le immagini disco al
primo avvio, partendo dalla system image.

Immagine di sistema disponibile: **una sola**,
`system-images/android-37.0/google_apis_playstore_ps16k/arm64-v8a/`.

---

## Creare l'AVD (una volta sola)

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
AVD=$HOME/.android/avd/jenny_square.avd
mkdir -p "$AVD"

cat > "$HOME/.android/avd/jenny_square.ini" <<EOF
avd.ini.encoding=UTF-8
path=$AVD
path.rel=avd/jenny_square.avd
target=android-37.0
EOF

cat > "$AVD/config.ini" <<'EOF'
AvdId=jenny_square
PlayStore.enabled=false
abi.type=arm64-v8a
avd.ini.displayname=jenny_square
avd.ini.encoding=UTF-8
disk.dataPartition.size=6G
fastboot.forceColdBoot=no
fastboot.forceFastBoot=yes
hw.accelerometer=yes
hw.audioInput=yes
hw.battery=yes
hw.camera.back=none
hw.camera.front=none
hw.cpu.arch=arm64
hw.cpu.ncore=4
hw.dPad=no
hw.gps=yes
hw.gpu.enabled=yes
hw.gpu.mode=auto
hw.gyroscope=yes
hw.initialOrientation=portrait
hw.keyboard=yes
hw.lcd.density=480
hw.lcd.height=1440
hw.lcd.width=1440
hw.mainKeys=no
hw.ramSize=3072
hw.sdCard=no
hw.sensors.light=yes
hw.sensors.orientation=yes
hw.sensors.proximity=yes
hw.trackBall=no
image.sysdir.1=system-images/android-37.0/google_apis_playstore_ps16k/arm64-v8a/
runtime.network.latency=none
runtime.network.speed=full
showDeviceFrame=no
skin.dynamic=yes
tag.display=Google APIs PlayStore
tag.id=google_apis_playstore
target=android-37.0
vm.heapSize=256
EOF
```

Le tre righe che contano sono `hw.lcd.width` / `hw.lcd.height` / `hw.lcd.density`.
Nessun `hw.device.name`: un profilo di dispositivo riscriverebbe la geometria.

Verifica: `$ANDROID_HOME/emulator/emulator -list-avds` deve elencare
`jenny_square`.

---

## Avviarlo

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
nohup $ANDROID_HOME/emulator/emulator -avd jenny_square \
  -no-window -no-audio -no-boot-anim -no-snapshot \
  -port 5556 -gpu swiftshader_indirect > /tmp/emu.log 2>&1 &
```

Porta fissa 5556 → **serial `emulator-5556`**. Pinnarlo sempre:

```bash
export ANDROID_SERIAL=emulator-5556
adb devices -l          # deve mostrare un solo dispositivo
adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed | tr -d '\r')" = "1" ]; do sleep 3; done
```

Primo avvio a freddo: ~1 minuto (`Boot completed in 13256 ms` nel log
dell'emulatore, poi il resto del sistema).

`-no-window` è headless. Per vedere lo schermo senza aprire la finestra:

```bash
adb exec-out screencap -p > /tmp/shot.png
```

Spegnerlo: `adb emu kill`.

### Verifica della geometria (prima di misurare qualunque cosa)

```bash
adb shell wm size      # atteso: Physical size: 1440x1440
adb shell wm density   # atteso: Physical density: 480
```

Se non tornano questi due numeri, l'AVD non ha rispettato il `config.ini` e ogni
misura successiva è sbagliata.

`wm size 1440x2200` / `wm size reset` servono per il test di controllo sulle
media query CSS legate all'altezza del viewport.

---

## Navigazione: gesture ↔ tre pulsanti

**Sull'immagine `android-37.0` la modalità di partenza è già gesture**
(`navigation_mode = 2`, overlay `…navbar.gestural` attivo).

```bash
# → gesture
adb shell cmd overlay enable  com.android.internal.systemui.navbar.gestural
adb shell cmd overlay disable com.android.internal.systemui.navbar.threebutton

# → tre pulsanti
adb shell cmd overlay enable  com.android.internal.systemui.navbar.threebutton

# verifica: 0 = tre pulsanti, 2 = gesture
adb shell settings get secure navigation_mode
adb shell cmd overlay list | grep navbar
```

Attenzione: `enable` non disabilita l'altro overlay. Con `gestural` e
`threebutton` entrambi `[x]`, `navigation_mode` resta `2` — per tornare davvero
ai tre pulsanti bisogna disabilitare `gestural`, o riabilitarlo per tornare a
gesture. Meglio spegnere esplicitamente quello che non si vuole.

---

## Build e installazione

```bash
cd <worktree>/android          # MAI dall'albero principale: srcDir("../../")
export ANDROID_HOME=$HOME/Library/Android/sdk
export ANDROID_SERIAL=emulator-5556
./gradlew app:installDebug
```

Solo **debug**: `assembleRelease` richiede `android/keystore.properties`, che è
gitignored e nei worktree non c'è. L'emulatore è vergine, il debug si installa
senza conflitti di firma.

Avvio e log:

```bash
adb shell monkey -p com.flagdizero.jenny -c android.intent.category.LAUNCHER 1
adb logcat -d -s Jenny:V GatewayStarter:V python.stderr:V AndroidRuntime:E
```

L'immagine è `arm64-v8a` e l'APK debug contiene quell'ABI: Chaquopy parte
(gateway su `127.0.0.1:18790`, WebUI caricata). Il gateway si avvia **senza
provider configurato** e resta in attesa dell'onboarding: la SPA mostra il
wizard di primo avvio, non la chat.

---

## Superare l'onboarding senza chiavi vere

Serve a ogni verifica di UI: finché il wizard è a schermo non si arriva né alla
chat né al composer. **Nessuna chiave API vera**: il gateway non deve rispondere,
deve solo smettere di dirsi al primo avvio.

`_is_first_run` (`webui/settings_api.py`) risponde a una domanda sola:
`len(config.providers.providers) == 0`. Basta quindi **un provider fittizio in
`config.json`**. La build è debug, quindi `run-as` legge e scrive i file privati
dell'app — con i **percorsi assoluti**: `run-as … sh -c 'cat > files/…'` fallisce
con "No such file or directory" perché la `sh` interna non eredita la cwd.

```bash
export ANDROID_SERIAL=emulator-5556
D=/data/data/com.flagdizero.jenny/files/workspace
adb shell am force-stop com.flagdizero.jenny
adb shell "run-as com.flagdizero.jenny cat $D/config.json" > /tmp/cfg.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/cfg.json"))
d["providers"] = {"providers": [{"name": "fake-local", "format": "openai_compat",
                                 "api_key": "EMPTY",
                                 "api_base": "http://127.0.0.1:1/v1"}],
                  "default": "fake-local"}
json.dump(d, open("/tmp/cfg.json", "w"), indent=2)
PY
adb shell "run-as com.flagdizero.jenny sh -c 'cat > $D/config.json'" < /tmp/cfg.json
adb shell "run-as com.flagdizero.jenny sh -c 'chmod 600 $D/config.json'"
```

**Non basta.** `mobile-last-mode` in `localStorage` resta a `onboarding` dal
tentativo precedente, e `init()` ci riatterra anche con `first_run: false`. Va
riportato a `chat` (v. la sezione CDP qui sotto) **una volta sola**: dopo, l'app
riparte in chat da sola.

> **Il reload va fatto con la sua `bs`.** L'URL iniziale porta il segreto di
> bootstrap in query (`?bs=…`), consumato da `shared/api-client.js` al caricamento
> del modulo. Un `location.href = "/html-mobile/?mode=chat"` lo butta via: la SPA
> resta senza token, va "offline", la cronologia non si carica e `/api/webui/*`
> risponde 401 — con l'aria di un bug del gateway. Dopo aver scritto
> `localStorage`, si riparte con **force-stop + monkey**, mai con un reload a mano.

All'avvio l'app chiede due permessi runtime (notifiche, posizione): il primo è un
dialog con due bottoni (`input tap 720 1008` = "Don't allow"), il secondo si
congeda con `KEYCODE_BACK`. Ricompaiono a ogni avvio se rifiutati.

## Guidare la WebView da CDP

La WebView di una build debug è ispezionabile, ed è il modo più preciso di
leggere lo stato della SPA (nessun OCR di screenshot, nessuna gesture da tarare).

```bash
adb shell cat /proc/net/unix | grep -o "webview_devtools_remote.*" | head -1
adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
curl -s http://127.0.0.1:9222/json/list      # il target con url html-mobile
```

Poi `Runtime.evaluate` sul `webSocketDebuggerUrl` (client `websockets` in
Python). **Il socket porta il PID**: dopo ogni force-stop cambia, e il `forward`
va rifatto.

Da lì, per esempio: `localStorage.setItem("mobile-last-mode","chat")`,
`window.mobileApp.currentMode`, `window.mobileApp._overlayLayers().filter(l=>l.present())`.

## Far scattare `onPackageChanged`

`MainActivity` ascolta `PACKAGE_ADDED` / `PACKAGE_REMOVED` /
`PACKAGE_FULLY_REMOVED` e li gira alla SPA. Per provocarli serve un pacchetto
che si possa davvero installare e disinstallare.

**Su un'app di sistema non si può**: `pm uninstall --user 0 <pkg>` risponde
`only root can delete system app for a particular user`, e `adb root` su
un'immagine `google_apis_playstore` dice `adbd cannot run as root in production
builds`. Non c'è modo di aggirarlo dall'esterno.

Serve quindi un APK proprio. Ne basta uno vuoto — nessuna classe, nessuna
risorsa, solo una activity di sistema esportata con l'intent-filter `LAUNCHER`,
altrimenti il pacchetto non compare nella lista (che viene da
`queryIntentActivities`). Progetto Gradle minimo **fuori dal worktree**
(`namespace` e `applicationId` a piacere, AGP 8.7.0, `compileSdk` 34,
`minSdk` 26), con questo manifest:

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:label="Sonda Cassetto">
        <activity android:name="android.app.Activity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

`./gradlew app:assembleDebug` (~7 s a freddo), poi `adb install -r …` e
`adb uninstall <pkg>`. La disinstallazione arriva come **due** notifiche
(`REMOVED` e `FULLY_REMOVED`): è previsto, il lato JS accorpa i refresh.

## Una mini-app di prova

`<workspace>/apps/<slug>/app.json` con `name`, `description` e **almeno una**
`actions` (l'array vuoto è un manifest rotto). Una `kind: "storage"` con
`op: "append"` e una `collection` basta. L'`index.html` accanto viene servito su
`/apps/<slug>/index.html`; se risponde "Not Found" l'overlay compare comunque, il
che è quanto serve per provare la catena di Indietro.

---

## Vedi anche

- [`apps-drawer-plan.md`](./apps-drawer-plan.md) — le misure raccolte qui, e cosa
  resta ignoto sul Titan 2
