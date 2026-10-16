/** L'ingresso dell'estensione Drive nelle shell (`shared/extensions.js`).
 *
 *  Ogni shell carica solo il pezzo che la riguarda: la casa la scheda della
 *  pagina Backup, l'onboarding la domanda «Ci conosciamo già?».
 */

export async function mount(ctx) {
  if (ctx.shell === 'home') {
    const { mountHome } = await import('./home.js');
    mountHome(ctx);
  } else if (ctx.shell === 'onboarding') {
    const { mountOnboarding } = await import('./onboarding.js');
    mountOnboarding(ctx);
  }
}
