# Guida rapida: sostituire una posa e portarla sul telefono

Guida operativa passo-passo per il flusso "ritocco (o sostituisco) un sorgente
→ rigenero i webp → li vedo sul dispositivo". Per il riferimento tecnico
completo (cosa fa ogni script, regole di scala, mapping runtime) vedi
[`README.md`](./README.md).

## 1. Tabella dei nomi file

Ogni posa ha **un solo sorgente** in questa cartella, a colori.
Modifica/sostituisci il file **con lo stesso nome esatto** — lo script non ha
bisogno di altro.

| Posa (runtime)                      | Sorgente               |
|-------------------------------------|------------------------|
| idle                                | `idle.PNG`             |
| think                               | `think.PNG`            |
| side                                | `jenny-side.PNG`       |
| side-talk                           | `jenny-side-talk.PNG`  |
| talk1a (bocca aperta, mano alzata)  | `talk_1a.PNG`          |
| talk1b (bocca aperta, braccia giù)  | `talk_1b.PNG`          |
| talk2a (bocca chiusa, mano alzata)  | `talk_2a.PNG`          |
| talk2b (bocca chiusa, braccia giù)  | `talk_2b.PNG`          |
| hang (appesa)                       | `jenny-hang.PNG`       |
| fall (caduta)                       | `jenny-fall.PNG`       |
| ground (atterrata)                  | `jenny-ground.PNG`     |
| walk1                               | `jenny-walk1.PNG`      |
| walk2                               | `jenny-walk2.PNG`      |
| hello1                              | `hello1.PNG`           |
| hello2                              | `hello2.PNG`           |

Le pose qui sopra sono quelle "cotte", con la faccia disegnata dentro. L'arte
**a due livelli** (`body_*.PNG` e `face_*.PNG`, corpi senza faccia + facce da
sola) segue le stesse regole di export e si rigenera con lo stesso comando; la
mappa dei nomi e quali sono in riserva stanno in
[`README.md`](./README.md#due-livelli-corpo--faccia).

Fino all'08/09/2026 ogni posa aveva due sorgenti, line-art bianco/nero e
gemello colore, e a runtime l'utente sceglieva fra le due varianti. La
preferenza è stata ritirata: resta il colore (v. `.agent/mascot-faces-plan.md`,
F9). L'**icona app** (`icon.png`) è un sorgente a sé, sempre line-art, e non
si tocca da questa guida.

## 2. Regole da rispettare quando esporti dal tool di disegno

- **Canvas esattamente 3000×3000**: lo script si ferma con un `assert` se non
  lo è.
- **Sfondo trasparente** (RGBA), non bianco pieno.
- **Stessa scala e stesso allineamento delle altre pose**: lo script non
  scala, non ritaglia e non ricentra nulla — se una posa esce più
  grande/piccola delle altre a runtime, il problema è nel PNG, non nello
  script.

## 3. Rigenerare i webp

Dalla cartella `android/image_source/`:

```bash
python3 gen_pose_webp.py
```

Rigenera **tutti e 24** i webp in `jenny/templates/ui/assets/` (15 pose cotte +
9 livelli), non solo quello che hai toccato — è normale e voluto, è
idempotente.

Se vedi `AssertionError: ... atteso canvas 3000x3000, trovato (...)` il
sorgente che hai salvato non è quadrato 3000×3000: ricontrolla l'export.

## 4. Caricare sul telefono

Verifica prima che il device sia collegato:

```bash
adb devices
```

Poi, dalla cartella `android/` (non da `image_source/`):

```bash
./gradlew app:installDebug
```

**Un riavvio dell'app non basta**: Chaquopy ri-estrae il bundle
`jenny/templates/ui` dentro l'APK solo a ogni installazione, quindi serve
davvero la build/install per vedere le immagini nuove sul dispositivo.

## Riepilogo one-liner

```bash
# dopo aver sostituito uno o più sorgenti in questa cartella:
cd android/image_source && python3 gen_pose_webp.py && cd .. && ./gradlew app:installDebug
```
