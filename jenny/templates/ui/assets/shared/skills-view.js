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
export function controllabile(skill) {
  return !skill.bundled && !skill.internal && !skill.locked;
}

/** L'elenco del payload, diviso nei due blocchi del pannello più un conto.
 *
 *  Le `internal` non si elencano (la modalità sviluppatore che le mostrava non
 *  c'è più) ma si **contano**: un totale che non torna con quel che si vede
 *  sembra un difetto. L'ordine è quello del backend, che già ordina per nome. */
export function dividiSkill(skills) {
  const tue = [];
  const integrate = [];
  let servizio = 0;
  for (const s of skills || []) {
    if (s.internal) servizio += 1;
    else if (s.bundled) integrate.push(s);
    else tue.push(s);
  }
  return { tue, integrate, servizio };
}

/** La riga sotto il nome. Il riassunto per l'utente nella sua lingua, poi
 *  l'altra, poi la descrizione per il modello — e **niente** se la descrizione
 *  è il nome stesso: è il ripiego di `_description()` lato server, e ripeterlo
 *  sotto il nome non dice nulla. */
export function riassuntoSkill(skill, locale) {
  const s = skill.user_summary;
  const perUtente = s && (s[locale] || s.it || s.en);
  if (perUtente) return perUtente;
  const d = (skill.description || '').trim();
  return d && d !== skill.name ? d : '';
}

/** La risposta breve della riga in cassetto. `t` è `i18n.t`, passato da chi
 *  chiama perché il modulo resti eseguibile fuori dal browser. */
export function riepilogoSkill({ tue, integrate }, t) {
  return tue.length
    ? t('skills.riepilogo', { integrate: integrate.length, tue: tue.length })
    : t('skills.riepilogoNessunaTua', { integrate: integrate.length });
}
