/** La casa — la mappa di un quaderno.
 *
 *  «Wiki, grafo e progetti sono una cosa sola» (la tavola `Concetto`), e nella
 *  colonna dell'operatore, alla riga del grafo, c'e' un trattino: il grafo non
 *  resta di la'. Questa e' la sua stanza nuova.
 *
 *  **Disegnato, non portato.** `mobile-graph.js` sono 943 righe, e quasi tutte
 *  sono cose che in casa non esistono: la vista home dove i nodi sono le wiki,
 *  la legenda, il pannello di un nodo, il pescaggio da `window.mobileApp`, la
 *  cronologia di navigazione della SPA. Quel che si riusa e' cio' che vale la
 *  pena riusare — la fisica a forze di D3, e `shared/wiki-search.js`, che e'
 *  gia' condiviso.
 *
 *  **E D3 arriva al primo tocco sulla linguetta, mai prima.** Pesa 279.706
 *  byte contro un guscio che si porta dietro `marked` e `DOMPurify` e basta, e
 *  il commento in cima a `index.html` dice perche' quel guscio e' magro: «la
 *  differenza non e' estetica — e' il tempo che passa fra il tocco sull'icona e
 *  la prima riga leggibile». Chi non apre la mappa non la paga.
 */

import { ensureVendor } from './shared/utils.js';
import { i18n } from './shared/i18n.js';

const D3_SRC = '/html-mobile/assets/vendor/d3@7/d3.min.js';

/* L'inquadratura a riposo. Il margine tiene dentro le etichette, che stanno
   sotto il pallino e sporgono ai lati; la scala massima impedisce a un
   quaderno di tre pagine di diventare tre lune. */
const FIT_PADDING = 40;
const FIT_MAX_SCALE = 1.6;

/* Il raggio dice quanti collegamenti ha una pagina. E' l'unico numero che la
   casa mostra, e lo mostra senza scriverlo: un pallino piu' grosso e' un posto
   dove il quaderno si annoda. */
export function radiusOf(degree) {
  return Math.max(5, Math.min(15, 5 + (degree || 0) * 1.6));
}

/** I nodi e gli archi nella forma che vuole la simulazione.
 *
 *  `index` resta quello del server anche qui: e' la terza resa della stessa
 *  risposta, e la maschera della ricerca si legge con quel numero e non con la
 *  posizione in questo array (v. il cappello di `casa-pages.js`).
 */
export function toSimulation(data) {
  const nodes = (data?.nodes || []).map((node, index) => ({
    index,
    id: node.id,
    path: node.path,
    label: node.title || node.label || node.id,
    group: node.group || 'other',
    degree: node.degree || 0,
  }));
  const known = new Set(nodes.map((n) => n.id));
  const links = (data?.edges || [])
    .filter((e) => known.has(e.source) && known.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }));
  return { nodes, links };
}

export class CasaMap {
  constructor({ onOpenPage } = {}) {
    this.el = document.getElementById('casa-map');
    this.svgEl = document.getElementById('casa-map-svg');
    this.noteEl = document.getElementById('casa-map-note');
    this._onOpenPage = onOpenPage;
    this._sim = null;
    /* Contatore di generazione: chi esce dalla pagina mentre i 280 kB stanno
       arrivando deve poter dire alla continuazione che non e' piu' il suo
       turno. Il token da solo non basta — e' cieco all'uscita dalla stanza,
       ed e' la stessa ragione per cui `mobile-graph.js` ne ha due. */
    this._gen = 0;
    this._drawn = null;
  }

  /** Disegna la mappa di *data*. La stessa risposta dell'elenco. */
  async draw(data) {
    const gen = ++this._gen;
    /* Gia' disegnata per questa risposta: non si rifa' la simulazione a ogni
       ritorno sulla linguetta, o i nodi ripartono da capo ogni volta. */
    if (this._drawn === data) return;
    const { nodes, links } = toSimulation(data);

    if (!nodes.length) return this._say('casa.pages.none');
    if (!links.length) return this._say('casa.map.noLinks');

    this._say(null);
    try {
      await ensureVendor(D3_SRC);
    } catch (err) {
      console.warn('casa.map: D3 non caricato', err);
      if (gen === this._gen) this._say('casa.map.failed');
      return;
    }
    if (gen !== this._gen) return;
    this._drawn = data;
    this._render(nodes, links);
  }

  /** Ferma la fisica. Chiamarla uscendo: una simulazione che nessuno guarda
   *  continua a girare a rAF su nodi che non si vedono. */
  stop() {
    this._gen += 1;
    if (this._sim) {
      this._sim.stop();
      this._sim = null;
    }
  }

  _say(key) {
    if (this.noteEl) {
      this.noteEl.textContent = key ? i18n.t(key) : '';
      this.noteEl.hidden = !key;
    }
    if (key && this.svgEl) this.svgEl.innerHTML = '';
  }

  _render(nodes, links) {
    /* Una simulazione precedente va fermata prima di sostituirne l'SVG: i
       suoi tick scrivono su nodi che stanno per sparire. */
    this.stop();
    const box = this.el.getBoundingClientRect();
    const w = Math.max(240, Math.round(box.width) || 320);
    const h = Math.max(240, Math.round(box.height) || 320);
    this.svgEl.setAttribute('viewBox', `0 0 ${w} ${h}`);
    this.svgEl.innerHTML = '';

    const svg = d3.select(this.svgEl);
    const root = svg.append('g');

    const line = root.append('g').attr('class', 'casa-map-edges')
      .selectAll('line').data(links).join('line');

    const dot = root.append('g').attr('class', 'casa-map-nodes')
      .selectAll('circle').data(nodes).join('circle')
      .attr('r', (d) => radiusOf(d.degree))
      .attr('class', (d) => `casa-map-node casa-group-${d.group}`)
      .on('click', (_e, d) => this._onOpenPage?.(d.path, d.label));

    /* Il nome sotto il pallino. Niente `pointer-events`: il bersaglio e' il
       pallino, e un'etichetta che intercetta il tocco fa mancare la pagina
       accanto. */
    const name = root.append('g').attr('class', 'casa-map-labels')
      .selectAll('text').data(nodes).join('text')
      .text((d) => d.label)
      .attr('dy', (d) => radiusOf(d.degree) + 10);

    this._sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d) => d.id).distance(58).strength(0.4))
      .force('charge', d3.forceManyBody().strength(-120).distanceMax(320))
      .force('center', d3.forceCenter(w / 2, h / 2).strength(0.05))
      /* Le pagine senza collegamenti sono tante — in un quaderno appena
         cominciato sono quasi tutte — e la repulsione le spinge via senza che
         niente le richiami: `forceCenter` sposta il baricentro, non i singoli.
         Il risultato, visto al banco, e' un nodo solo all'angolo opposto e
         mezza stanza vuota in mezzo. Una molla debole verso il centro li tiene
         nella stessa pagina senza appiattire il disegno. */
      .force('x', d3.forceX(w / 2).strength(0.06))
      .force('y', d3.forceY(h / 2).strength(0.06))
      .force('collision', d3.forceCollide().radius((d) => radiusOf(d.degree) + 12))
      .on('tick', () => {
        line
          .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
          .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
        dot.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
        name.attr('x', (d) => d.x).attr('y', (d) => d.y);
      });

    /* Si trascina e si avvicina: su 590x566 un quaderno da sessanta pagine non
       ci sta, e rimpicciolire i pallini non lo renderebbe leggibile. */
    const zoom = d3.zoom()
      .scaleExtent([0.4, 4])
      .on('zoom', (e) => root.attr('transform', e.transform));
    svg.call(zoom);

    /* A fisica ferma, la nuvola sta dove l'hanno lasciata le forze — che non e'
       il centro. Visto al banco: sei nodi raccolti in basso a destra e mezza
       stanza vuota. Si inquadra a riposo e non a ogni tick: inseguire una
       simulazione che si assesta vuol dire farle ballare sotto lo sguardo. */
    this._sim.on('end', () => {
      const xs = nodes.map((n) => n.x);
      const ys = nodes.map((n) => n.y);
      const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
      const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
      const k = Math.min(
        FIT_MAX_SCALE,
        (w - FIT_PADDING * 2) / Math.max(1, x1 - x0),
        (h - FIT_PADDING * 2) / Math.max(1, y1 - y0),
      );
      const t = d3.zoomIdentity
        .translate(w / 2 - k * (x0 + x1) / 2, h / 2 - k * (y0 + y1) / 2)
        .scale(k);
      svg.call(zoom.transform, t);
    });
  }
}
