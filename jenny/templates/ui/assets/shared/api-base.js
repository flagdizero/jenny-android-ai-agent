/** L'indirizzo di un provider come lo scrive la gente, e come va salvato.
 *
 *  La stessa regola di `normalize_api_base` in `jenny/webui/settings_api.py`,
 *  detta qui prima del giro per poterla dire nella lingua di chi legge: il
 *  server la riapplica comunque, e il suo rifiuto arriva in inglese. Vuoto e'
 *  l'indirizzo predefinito del formato; un `${...}` passa com'e'; lo schema si
 *  porta in minuscolo («Http://», dell'autocorrezione, diventa «http://»);
 *  valgono solo http e https con un host.
 *
 *  @returns `{ value }` con l'indirizzo da salvare (stringa vuota = il
 *           predefinito), oppure `{ error: true }`. */
export function normalizeApiBase(text) {
  const raw = String(text ?? '').trim();
  if (!raw || raw.startsWith('${')) return { value: raw };
  const at = raw.indexOf('://');
  if (at <= 0) return { error: true };
  const value = raw.slice(0, at).toLowerCase() + raw.slice(at);
  let url;
  try {
    url = new URL(value);
  } catch {
    return { error: true };
  }
  if ((url.protocol !== 'http:' && url.protocol !== 'https:') || !url.hostname) {
    return { error: true };
  }
  return { value };
}

/** L'indirizzo predefinito di ogni formato: il segnaposto del campo vuoto, e
 *  quello che vale davvero quando il campo resta vuoto (l'elenco dei modelli
 *  dell'onboarding, il salvataggio). Era scritto due volte, nel primo avvio e
 *  nel dialogo «Aggiungi marca» dell'officina. */
export const DEFAULT_API_BASE = Object.freeze({
  openai_compat: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
});

/** Gli attributi che tengono la tastiera lontana da un campo tecnico. */
export const NO_AUTOCORRECT = 'autocomplete="off" autocorrect="off" autocapitalize="none" spellcheck="false"';
