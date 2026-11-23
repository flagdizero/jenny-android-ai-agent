/** Il link d'abbinamento fra due Jenny (`jenny://pair?host=…&ip=…&port=…&code=…`).
 *
 * Lo porta il QR che un'altra Jenny mostra in officina: la fotocamera di
 * sistema lo legge e Android lo apre in Jenny (`MainActivity.capturePairLink`),
 * che lo tiene finché la pagina non lo chiede con `JennyNative.takePairLink()`.
 * La casa lo chiede al boot e quando Android la avvisa (`window.jennyPeer`), e
 * apre la stanza «Altre Jenny» col modulo già compilato; l'officina, se è lei
 * davanti, apre il suo. Abbinare resta un tocco dell'utente: il link da solo
 * non fa niente.
 *
 * Fra le due pagine il link viaggia in `sessionStorage`, non nell'URL: il
 * codice è un segreto di dieci minuti e non deve finire in una riga di log.
 */

const STORAGE_KEY = 'jenny-pair-link';

/** I campi del link, o `null` se non è un link d'abbinamento. */
export function parsePairLink(raw) {
  if (typeof raw !== 'string' || !raw.startsWith('jenny://pair')) return null;
  const query = raw.slice('jenny://pair'.length).replace(/^\?/, '');
  const params = new URLSearchParams(query);
  const code = (params.get('code') || '').trim();
  const host = (params.get('host') || '').trim();
  const ip = (params.get('ip') || '').trim();
  const port = Number.parseInt(params.get('port') || '', 10);
  if (!code || (!host && !ip)) return null;
  return {
    host: host.slice(0, 253),
    ip: ip.slice(0, 45),
    port: Number.isInteger(port) && port > 0 && port < 65536 ? port : null,
    code: code.slice(0, 20),
  };
}

/** Il link in attesa nel guscio Android, una volta sola. */
export async function takeNativePairLink() {
  const take = globalThis.window?.JennyNative?.takePairLink;
  if (typeof take !== 'function') return null;
  try {
    return parsePairLink(await take());
  } catch {
    return null;
  }
}

export function stashPairLink(link) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(link));
  } catch {
    /* Senza storage il link si perde: l'utente lo riscrive a mano. */
  }
}

export function takeStashedPairLink() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** Chiede il link al boot e ogni volta che Android avvisa; `onLink(link)` se c'è. */
export function watchPairLinks(onLink) {
  const pull = async () => {
    const link = await takeNativePairLink();
    if (link) onLink(link);
  };
  globalThis.window.jennyPeer = { onPairLink: pull };
  pull();
}
