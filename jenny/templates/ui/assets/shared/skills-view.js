/** Le skill come le vede Mani: chi è tua, chi viene con l'app, chi lavora per lei.
 *
 *  Puro di proposito — niente DOM, niente rete, niente `i18n` importato — per
 *  la stessa ragione di `launcher-rank.js`: la regola che divide l'elenco si
 *  prova sotto node, e la riga in cassetto e il pannello leggono **questa**
 *  funzione invece di tenerne ciascuno una copia (v.
 *  `.agent/officina-skill-plan.md`, «Le regole di divisione»).
 *
 *  La domanda che conta è una sola: **un interruttore qui sopravvive al
 *  riavvio?** Le integrate no — l'avvio le ri-estrae dall'APK sopra quel che
 *  c'è — e il backend ormai rifiuta di toccarle. Per questo `bundled` decide
 *  il blocco, e non `source`, che vale `"workspace"` per tutte.
 */

/** Un interruttore ha senso solo dove la scelta resta scritta. `locked` su una
 *  skill tua è il suo frontmatter che chiede di non toccarla: la si ascolta. */
export function controllable(skill) {
  return !skill.bundled && !skill.internal && !skill.locked;
}

/** La chiave i18n del perche' una skill non ha l'interruttore.
 *
 *  Sono due motivi diversi e il lucchetto li diceva uguali: una skill tua con
 *  `locked` nel frontmatter si leggeva «Viene con l'app», che per una skill
 *  scritta dall'utente e' falso — e lo manda a cercare il motivo nel posto
 *  sbagliato. */
export function blockReason(skill) {
  return skill.bundled ? 'skills.integrataBloccata' : 'skills.tuaBloccata';
}

/** L'elenco del payload, diviso nei due blocchi del pannello più un conto.
 *
 *  Le `internal` non si elencano (la modalità sviluppatore che le mostrava non
 *  c'è più) ma si **contano**: un totale che non torna con quel che si vede
 *  sembra un difetto. L'ordine è quello del backend, che già ordina per nome. */
export function splitSkill(skills) {
  const yours = [];
  const integrate = [];
  let service = 0;
  for (const s of skills || []) {
    if (s.internal) service += 1;
    else if (s.bundled) integrate.push(s);
    else yours.push(s);
  }
  return { yours, integrate, service };
}

/** La riga sotto il nome. Il riassunto per l'utente nella sua lingua, poi
 *  l'altra, poi la descrizione per il modello — e **niente** se la descrizione
 *  è il nome stesso: è il ripiego di `_description()` lato server, e ripeterlo
 *  sotto il nome non dice nulla. */
export function skillBlurb(skill, locale) {
  const s = skill.user_summary;
  const perUser = s && (s[locale] || s.it || s.en);
  if (perUser) return perUser;
  const d = (skill.description || '').trim();
  return d && d !== skill.name ? d : '';
}

/** La risposta breve della riga in cassetto. `t` è `i18n.t`, passato da chi
 *  chiama perché il modulo resti eseguibile fuori dal browser. */
export function skillsSummary({ yours, integrate }, t) {
  return yours.length
    ? t('skills.summary', { integrate: integrate.length, yours: yours.length })
    : t('skills.summaryNoneYours', { integrate: integrate.length });
}
