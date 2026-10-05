/** Le tele dei quaderni: quale colore ha un quaderno, ovunque compaia.
 *
 *  Un quaderno ha una tela sola, e la porta in quattro posti: la copertina
 *  nella pagina Quaderni (`home-who.js`), il pallino della fila
 *  (`home-strip.js`), la pastiglia e il percorso (`home-app.js`), la testa
 *  della sua scheda (`home-notebook.js`). Per questo il conto sta qui, in un
 *  modulo foglia che non importa niente: chi ha bisogno del colore non deve
 *  portarsi dietro la pagina intera.
 *
 *  I colori non sono qui: sono sei per tema, `--spine-1` … `--spine-6` con il
 *  loro inchiostro, in mobile-style.css. Qui c'e' solo **quale**.
 */

/** Quante tele ha ogni tema. */
export const SPINES = 6;

/** Il conto stabile su un nome: lo stesso che faceva il pallino fino al
 *  05/10/2026, cosi' cambiare la forma non ha rimescolato le identita'. */
export function hashOf(name) {
  let hash = 0;
  for (const ch of String(name || '')) hash = (hash * 31 + ch.codePointAt(0)) % 360;
  return hash;
}

/** Quale delle sei tele e' del quaderno *name*: da 1 a `SPINES`.
 *
 *  **Non significa niente oltre l'identita'**: serve a riconoscere un
 *  quaderno fra nomi che si somigliano, non a dire uno stato. Per questo le
 *  tele sono token loro e non `--error` o `--accent` — un quaderno sano tinto
 *  dell'errore si leggerebbe come un allarme.
 */
export function spineOf(name) {
  return (hashOf(name) % SPINES) + 1;
}

/** Il colore di un quaderno per un pallino: la sua tela, nei colori del tema
 *  a schermo. E' un `var()`, quindi cambia da se' quando cambia il tema. */
export function dotColor(name) {
  return `var(--spine-${spineOf(name)})`;
}

/** Tela, inchiostro e storta di un elemento: proprieta' CSS, cosi' la
 *  finitura del tema (`--spine-edge`, il bagliore, il lucido) le legge. La
 *  storta (-1, 0, 1) e' del nome e non della tela, o due quaderni dello
 *  stesso colore penderebbero sempre insieme; la quantita' la decide il tema
 *  (`--spine-jitter`, zero quasi ovunque). */
export function paintSpine(el, name) {
  const n = spineOf(name);
  el.style.setProperty('--cover', `var(--spine-${n})`);
  el.style.setProperty('--cover-ink', `var(--spine-${n}-ink)`);
  el.style.setProperty('--tilt', String((Math.floor(hashOf(name) / SPINES) % 3) - 1));
}

/** L'iniziale della copertina: il primo carattere vero, anche fuori dal
 *  piano base. */
export function initialOf(name) {
  return ([...String(name || '')][0] || '').toUpperCase();
}
