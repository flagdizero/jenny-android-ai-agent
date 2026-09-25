# Jenny Apps — Design

Design agreed during onboarding (July 2026), implemented in the same cycle. Runtime lives in
`jenny/apps/` (manifest/storage/http/executor), gateway routes in
`jenny/webui/apps_api.py` + `ws_http.py`, agent tools in
`jenny/agent/tools/app_actions.py`, UI kit in `jenny/templates/ui/assets/apps/`.

**Transport constraint (hard wall):** the gateway HTTP layer (`websockets` http11) rejects
any non-GET method and any request body at the parser level. Actions therefore execute via
`GET /api/apps/<slug>/actions/<name>?params=<url-encoded JSON>&token=...` (~6 KB params
budget, 413 above). Never "fix" this to POST. The SDK hides the transport. Bonus: no CORS
preflight exists or is needed — the sandboxed (opaque-origin) iframe only ever issues simple
GETs, and action responses carry `Access-Control-Allow-Origin: *` on every status.

## Concept

A Jenny App is a folder in `workspace/apps/<slug>/`:

```
workspace/apps/piante/
├── app.json        # manifest: name, icon, external server, typed actions
├── AGENT.md        # context for Jenny (what the app is, preferences, thresholds) — NOT the contract
├── app/
│   └── index.html  # the UI (HTML/JS only — no per-app Python)
└── data/           # app state, shared with the agent
```

Only `app/` is web-served (`/apps/<slug>/<rel>` maps to `apps/<slug>/app/<rel>`): manifest,
AGENT.md and `data/` are never reachable over HTTP — data flows only through actions.

Apps live in `workspace/apps/`, **not** `workspace/ui/` — the latter is re-extracted from the
package on every startup (`jenny/utils/helpers.py::sync_workspace_templates`) and
would be overwritten. Apps are user content; `ui/` is bundled content.

Boundary with skills: if it needs a screen, it's an app; if it only lives in chat, it's a
skill. An app can carry its own agent-facing side (actions + AGENT.md), so "integration with
an external server" is naturally an app. This fork has no MCP client; typed actions are the
in-workspace equivalent.

## Actions (the contract)

Actions are declared in `app.json` with a JSON Schema for their parameters. They are
declarative — the gateway never executes per-app code (no plugin system).

**`actions` is optional** (since Sept 2026). An app whose only job is to draw a screen has
nothing agent-facing to declare, and the previous non-empty requirement made that app
impossible: the observed result was an *invented* action added purely to satisfy the schema,
which then shipped broken. `AppToolsSyncer` registers zero tools for such an app, which is
the correct outcome. The boundary paragraph above always said an app "can" carry an
agent-facing side — the code disagreed with it.

**Never `server.auth`.** There is no credential store; `_parse_manifest` rejects a manifest
declaring it (app loads broken, remedy named) and `execute_http_action` refuses with 501 as
a second line. See `.agent/security.md`.

Two kinds:

- `storage`: typed mutations/queries on collections under `data/` (append, set, update,
  delete, query), validated by the gateway before writing.
- `http`: mapped onto calls to the app's external server (e.g. a LAN plant server), with
  parameters validated and injected into URL/body, executed through a gateway proxy that goes
  through the existing SSRF protections (`jenny/security/`).

A `python` kind is explicitly deferred/excluded. If an app needs real server-side logic, it
belongs in the app's external server behind an `http` action.

Example manifest:

```json
{
  "name": "Piante",
  "server": { "baseUrl": "http://192.168.1.50:8080" },
  "actions": [
    { "name": "lista_piante",   "kind": "http", "method": "GET", "path": "/plants",
      "description": "Elenco piante con stato" },
    { "name": "umidita_pianta", "kind": "http", "method": "GET",
      "path": "/plants/{id}/humidity", "params": { "id": { "type": "string" } } },
    { "name": "annota_cura",    "kind": "storage", "op": "append", "collection": "cure",
      "params": { "pianta": { "type": "string" }, "nota": { "type": "string" } } }
  ]
}
```

## Vista esterna (`view: {"kind": "external"}`)

Un'app puo' dichiarare che il suo schermo **e' la UI del suo server**, invece di
`app/index.html`:

```json
{ "name": "Telecomando", "description": "...",
  "server": { "baseUrl": "http://hps:8091" },
  "view": { "kind": "external" } }
```

`actions` e `app/index.html` diventano entrambi superflui (il validatore avvisa se
`index.html` c'e' comunque: non verrebbe mai mostrato).

**Perche' esiste.** Un iframe verso un `http://` non-loopback non parte affatto: la
network security config dell'APK permette il cleartext solo verso il gateway, e la
WebView risponde `ERR_CLEARTEXT_NOT_PERMITTED` prima di aprire un socket. Prima di
Sett 2026 "mostrami il mio server" non era esprimibile, e un'app che ci provava era
un riquadro bianco senza messaggi.

**Come funziona.** `GET /api/webui/apps/<slug>/view` avvia `apps/proxy.py::AppViewProxy`
— un listener su `127.0.0.1:<porta effimera>` che inoltra a `server.baseUrl` a livello
di byte — e torna `{"url": ...}`. La SPA incornicia quell'URL. `.../view/close` chiude
il listener; un idle timeout lo chiude comunque se quel segnale non arriva.

Due vincoli spiegano la forma:

- **Il proxy non puo' essere una route del gateway.** Il gateway gira sul parser di
  `websockets`, che non legge il body: un POST non passerebbe, e una UI remota reale
  ne fa. Ed e' anche il motivo per cui non e' un prefisso di path — i path assoluti
  della pagina remota (`/style.css`) si risolverebbero sulla radice del gateway.
- **La vista esterna non e' annidata dentro l'app-frame.** I flag di sandbox si
  ereditano nei frame figli, quindi annidarla le darebbe un'origine opaca: cookie
  bloccati, `localStorage` che solleva, `fetch` con `Origin: null`. Ha il suo overlay
  con `allow-same-origin`, che li' e' sicuro perche' l'origine e' quella del proxy,
  non quella del gateway (porta diversa). Threat model completo in `.agent/security.md`.

Solo `http://`: un server `https` non ha bisogno del proxy (la policy cleartext non lo
tocca) e il proxy lo rifiuta dicendolo.

## One contract, three consumers

1. **Jenny**: each action is registered as a native LLM tool via the tool registry
   (`jenny/agent/tools/registry.py`), e.g. `piante_umidita_pianta(id)` — provider-side
   parameter validation, structured errors on failure. Tools are re-registered hot when an
   `app.json` changes; no gateway restart.
2. **The app's UI**: calls the actions endpoint through `jenny.action()` in `jenny-sdk.js`
   (GET transport — see above; solves CORS, centralizes auth in the manifest).
3. **Jenny's cron jobs**: proactivity ("alert me if the basil is dry") lives on the agent
   side, never in the app.

## Directionality

- **Jenny → app**: always possible, even when the app is "closed" (a closed app is just
  unrendered HTML; Jenny acts on its data through actions).
- **App → Jenny**: never autonomously. Only an explicit user hand-off: `jenny.discuss(text)`
  switches to chat with app context prefilled (existing pattern:
  `_startSkillCreation()` in `mobile-apps.js`). Jenny's replies land in chat and are never
  rendered inside the app. Apps stay deterministic UIs; intelligence lives in one place.

## UI and SDK

Apps open in a sandboxed full-screen iframe inside the SPA.

**Graphical standard — the Jenny Kit.** No external CSS framework. Apps link a stylesheet
served by the gateway (`templates/ui/assets/apps/jenny-kit.css`) that reuses the
SPA's theme tokens (`--bg`, `--text`, `--accent`, `data-theme` dark/light — see
`mobile-style.css`), styles semantic HTML classlessly, and defines a ~10-class component
vocabulary (topbar, card, list-row, badge, stat, fab, grid, empty). Icons: the already
bundled Tabler webfont. Charts: `jenny-charts.js` helpers (line/bars/gauge) wrapping the
already bundled d3. Rationale: apps are same-origin iframes, so shared gateway assets keep
them offline-safe and visually native to the SPA, and a small documented vocabulary is what
makes LLM-generated UIs come out consistently good; "self-contained" means *no external
hosts*, not "everything inline". The kit is documented for the generator in
`jenny/skills/app-creator/references/manifest.md`.

The JS SDK is minimal:

- `jenny.action(name, params)` — call one of the app's own actions with the app's token
  (`?token=` in the frame `src`: an HMAC of the gateway secret over the slug, valid only on
  that app's files and actions — never the gateway secret itself, see `.agent/security.md`).
- `jenny.discuss(text)` — hand off to chat with app context.
- `data-changed` WebSocket event — live refresh when Jenny writes while the app is open.

The iframe sandbox is a second line of defense: an app cannot navigate the SPA or touch the
chat DOM. That it only talks to its own endpoints is **not** the sandbox's doing — it is the
per-app token, which the gateway accepts on that app's routes and nowhere else (until Sept 2026
the frame carried the gateway secret, and the claim was false).

## Credentials

**Not implemented, and `server.auth` is rejected — not tolerated.** The design was: `app.json`
holds only a reference (`"auth": {"secretRef": "..."}`), the secret lives in a separate store
excluded from agent reads (hook point: `jenny/security/workspace_access.py`), and the proxy
injects it at call time. None of that store exists.

Until it does, a manifest declaring `server.auth` is **rejected at load** by `_parse_manifest`
(the app shows as broken, with the remedy in the message) and refused with 501 by
`execute_http_action` as a second line. Before Sept 2026 only the 501 existed *and* the
app-creator skill instructed writing `secretRef` anyway "so manifests keep working when the
store lands" — so an app could validate clean, open, and have every http action dead. It
happened. An app server must be reachable without credentials.

Tokens still never enter the LLM context or app HTML.

## App creation

Apps are created only via Jenny in chat: the "+" button in the Jenny Apps grid opens the chat
with the builtin `app-creator` skill (`jenny/skills/app-creator/`) that teaches the
manifest format and folder conventions and ships a manifest validator script.
There is no dedicated editor in the WebUI (manual edits happen through workspace files).

Validation at load: when the gateway scans `workspace/apps/`, a malformed `app.json` must
never crash anything — the app shows up in the grid as "broken" with a readable error, so the
user can ask Jenny to fix it. The app generator is an LLM; the system must assume manifests
occasionally come out wrong.
