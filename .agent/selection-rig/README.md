# Banco di prova per la selezione del testo su Android

Tre pagine con lo stesso testo (80 paragrafi numerati), header e footer da
80px, e un overlay giallo che stampa ancora e fuoco a ogni `selectionchange`
(`A=p3:39 F=p6:44 len=317`). Servono a misurare come Chromium ri-deriva la
base della selezione al tocco di un manico, senza passare da Jenny.

| file | scroller | chrome |
|---|---|---|
| `a-inner-scroller.html` | `main { overflow: auto }` (Jenny oggi) | in flusso |
| `b-root-scroller.html` | il documento | `position: fixed` |
| `c-root-scroller-transparent-chrome.html` | il documento | `fixed` + `pointer-events: none` |

## Come si usa

```bash
export ANDROID_SERIAL="${ANDROID_SERIAL:?il seriale del telefono di prova, da adb devices -l}"
python3 -m http.server 8099 --bind 127.0.0.1 --directory .agent/selection-rig &
adb reverse tcp:8099 tcp:8099
adb shell am start -a android.intent.action.VIEW -d http://localhost:8099/a-inner-scroller.html com.android.chrome
```

Gesto (coordinate fisiche del Titan 2, 1436×1440, dpr 2,5; la toolbar di
Chrome occupa i primi ~205px e **si nasconde quando si scorre verso il basso**,
spostando tutto di ~140px: rileggere uno screenshot prima di ogni tocco):

```bash
adb shell input swipe 890 826 890 826 800        # pressione lunga su una parola
./gesture.sh drag 968 865 968 1170 10           # allungo col manico finale (sta ~40px sotto la riga)
./gesture.sh drag 20 900 20 600 8               # scroll col dito nel margine sinistro
./gesture.sh drag 968 678 1030 678 6            # tocco il manico e lo sposto
./shot.sh nome                                  # screenshot senza il warning "Multiple displays"
```

La lettura è l'overlay: se `A` cambia quando si sposta il manico finale (o `F`
quando si sposta quello iniziale), la base è stata ri-derivata male.

Misure del 13/09/2026 (Chrome 150.0.7871.63): A → `A=p1:0`, tutto il
documento; B → tenuta con la base fuori viewport, **collassata** con la base
sotto l'header; C → tenuta in entrambi i casi. Il ragionamento sta in
[`../chat-selection-root-plan.md`](../chat-selection-root-plan.md).
