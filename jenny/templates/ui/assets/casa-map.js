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

/* Quante pagine portano il nome scritto, e quanto lungo.
 *
 *  Misurato sul Titan con un quaderno vero da 31 pagine: scrivendoli tutti, e
 *  interi, le etichette si sovrappongono fino a diventare una macchia — e i
 *  titoli di una wiki sono lunghi («Coltivazione-Monstera-Roma — Sostegno,
 *  fertilizzazione, crescita»). Su uno schermo da 566 px non e' una
 *  regolazione fine: e' che oltre una decina di nomi non ce ne sta un
 *  undicesimo.
 *
 *  Quali: **i nodi piu' collegati**, che e' l'unica domanda a cui una mappa
 *  risponde meglio di un elenco — dove si annoda il quaderno. Gli altri
 *  restano pallini, e per sapere come si chiamano c'e' la linguetta accanto,
 *  che li elenca tutti e li cerca. */
const MAX_LABELS = 10;
const LABEL_CHARS = 22;
/* L'altezza di una riga di etichetta, in unita' del disegno. Si dichiara
   invece di misurarla: `getBBox()` su un testo appena inserito costringe il
   browser a un layout per ogni nome, e per un numero che il CSS fissa a 9 px
   (piu' discendenti e un filo d'aria). La larghezza invece si misura davvero —
   dipende dalle lettere, e indovinarla vorrebbe dire sbagliarla su ogni
   titolo. */
const LABEL_HEIGHT = 11;
/* Aria fra due riquadri. Due nomi che si toccano si leggono male quanto due
   che si accavallano. */
const LABEL_GAP = 3;
/* Sotto questa soglia si scrivono tutti: dieci su undici sarebbe una scelta
   che non si capisce, e undici nomi ci stanno. */
const LABEL_ALL_UNDER = 13;

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

/** Il nome accorciato: un titolo di wiki e' spesso una frase. */
export function shortLabel(text) {
  const t = String(text || '');
  return t.length <= LABEL_CHARS ? t : `${t.slice(0, LABEL_CHARS - 1).trimEnd()}…`;
}

/** Gli id delle pagine che portano il nome scritto: le piu' collegate.
 *
 *  A parita' di collegamenti decide il nome, perche' l'insieme deve essere lo
 *  stesso a ogni apertura: una mappa che cambia le etichette fra due sguardi
 *  sembra rotta anche quando disegna gli stessi nodi.
 */
export function labelledNodes(nodes) {
  const all = nodes || [];
  if (all.length < LABEL_ALL_UNDER) return new Set(all.map((n) => n.id));
  const ordinati = [...all].sort(
    (a, b) => (b.degree || 0) - (a.degree || 0) || String(a.label).localeCompare(String(b.label)),
  );
  return new Set(ordinati.slice(0, MAX_LABELS).map((n) => n.id));
}

/* Due riquadri si toccano? `LABEL_GAP` allarga quello che si sta provando, e
   basta: allargarli entrambi conterebbe l'aria due volte. */
function overlap(a, b) {
  return (
    a.x < b.x + b.w + LABEL_GAP &&
    b.x < a.x + a.w + LABEL_GAP &&
    a.y < b.y + b.h + LABEL_GAP &&
    b.y < a.y + a.h + LABEL_GAP
  );
}

/** I due posti in cui un nome puo' stare, in ordine di preferenza: sotto il
 *  pallino, e — se sotto e' occupato — sopra.
 *
 *  Sopra lo scarto e' **piu' largo di quanto sembri necessario**, e non e' per
 *  simmetria: `y` di un testo e' la linea di base, quindi andando in alto il
 *  riquadro scende sotto il punto d'ancoraggio di un paio di pixel. Con lo
 *  stesso numero di sotto, il fondo del nome finiva dentro l'aria del pallino
 *  del vicino, e il posto di riserva non era piu' un posto.
 */
export function labelOffsets(radius) {
  return [radius + 10, -(radius + 8)];
}

/** Il riquadro che un nome occupa, dato lo scarto verticale.
 *
 *  `y` di un testo SVG e' la **linea di base**, non il bordo alto: il riquadro
 *  comincia sopra di essa, e il testo ci scende sotto per i discendenti. Oggi
 *  i riquadri si confrontano solo fra loro, quindi una traslazione comune non
 *  cambierebbe nessuna decisione — ma un riquadro che dice il falso su dove
 *  sta il testo e' una trappola pronta per il primo che gli confronti accanto
 *  qualcos'altro. Esportata apposta: e' una promessa sulla geometria, e le
 *  promesse si misurano.
 */
export function labelBox(item, offset) {
  return {
    x: item.x - item.w / 2,
    y: item.y + offset - LABEL_HEIGHT * 0.8,
    w: item.w,
    h: LABEL_HEIGHT,
  };
}

/** Dove va ogni nome, e quali non ci stanno.
 *
 *  **Chi arriva prima sceglie**, e arriva prima la pagina piu' collegata: e' la
 *  stessa gerarchia con cui si decide chi un nome ce l'ha
 *  (v. `labelledNodes`), e senza un ordine fisso la mappa cambierebbe
 *  etichette fra due aperture disegnando gli stessi nodi. A parita' decide il
 *  nome.
 *
 *  Un nome che non trova posto **sparisce**, e non si accavalla: due parole
 *  sovrapposte non sono due informazioni, sono zero. La pagina resta il suo
 *  pallino, si tocca lo stesso, e il suo nome sta nell'elenco accanto.
 *
 *  **Si scansano fra loro e basta: i pallini non sono ostacoli.** Ci ho provato,
 *  e il conto dice di no. Un nome sta dieci pixel sotto il bordo del suo
 *  cerchio, cioe' dentro l'aria che separa due riquadri — quindi litigava
 *  perfino col proprio pallino, e tutte le etichette sparivano. Escluso il
 *  proprio, restava che un nome largo fino a 90 px su nodi distanti 40 tocca
 *  sempre il cerchio del vicino: misurato, tre pagine in fila ne conservavano
 *  **una su tre**. Un nome che sfiora un pallino si legge; un nome che non c'e'
 *  no, e la mappa esiste per leggere i nomi.
 *
 *  @param items  `[{id, x, y, w, r, priority}]` in coordinate del disegno.
 *  @returns `Map<id, offset>`, solo per i nomi che ci stanno.
 */
export function placeLabels(items) {
  const presi = [];
  const scelti = new Map();
  const ordinati = [...(items || [])].sort(
    (a, b) =>
      (b.priority || 0) - (a.priority || 0) ||
      String(a.id).localeCompare(String(b.id)),
  );
  for (const item of ordinati) {
    for (const offset of labelOffsets(item.r || 0)) {
      const box = labelBox(item, offset);
      if (presi.some((p) => overlap(p, box))) continue;
      presi.push(box);
      scelti.set(item.id, offset);
      break;
    }
  }
  return scelti;
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

    /* Il nome sotto il pallino, e solo per i nodi che ne portano uno. Niente
       `pointer-events`: il bersaglio e' il pallino, e un'etichetta che
       intercetta il tocco fa mancare la pagina accanto. Lo scarto verticale
       vero lo sceglie `placeLabels` a fisica ferma; questo e' il posto di
       preferenza, buono finche' i nodi si muovono. */
    const conNome = labelledNodes(nodes);
    const name = root.append('g').attr('class', 'casa-map-labels')
      .selectAll('text').data(nodes.filter((d) => conNome.has(d.id))).join('text')
      .text((d) => shortLabel(d.label))
      .attr('dy', (d) => labelOffsets(radiusOf(d.degree))[0]);

    this._sim = d3.forceSimulation(nodes)
      /* Quanto ci mette a fermarsi. Il default di D3 sono ~300 tick, cioe'
         cinque secondi a 60 fps — e su un quaderno da 31 pagine il Titan ne fa
         una trentina al secondo, quindi **dieci**. Dieci secondi in cui i nomi
         stanno dove capita, perche' si collocano solo a fisica ferma: misurato
         con due scatti, a 5 s accavallati e a 16 s a posto. Con 0,045 i tick
         sono ~150 e la nuvola e' assestata lo stesso: a quel punto le forze
         stanno gia' spostando i nodi di frazioni di pixel. */
      .alphaDecay(0.045)
      /* Misurati su un quaderno vero da 31 pagine: con 58 e -120 i nodi si
         impilavano al centro in una matassa, e nemmeno lo zoom la apriva. */
      .force('link', d3.forceLink(links).id((d) => d.id).distance(72).strength(0.35))
      .force('charge', d3.forceManyBody().strength(-230).distanceMax(400))
      .force('center', d3.forceCenter(w / 2, h / 2).strength(0.05))
      /* Le pagine senza collegamenti sono tante — in un quaderno appena
         cominciato sono quasi tutte — e la repulsione le spinge via senza che
         niente le richiami: `forceCenter` sposta il baricentro, non i singoli.
         Il risultato, visto al banco, e' un nodo solo all'angolo opposto e
         mezza stanza vuota in mezzo. Una molla debole verso il centro li tiene
         nella stessa pagina senza appiattire il disegno. */
      .force('x', d3.forceX(w / 2).strength(0.06))
      .force('y', d3.forceY(h / 2).strength(0.06))
      .force('collision', d3.forceCollide().radius((d) => radiusOf(d.degree) + 14))
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
      this._placeLabels(name);
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

  /* Misura i nomi, li colloca, e toglie quelli che non ci stanno.
   *
   *  Si fa **una volta sola**, a fisica ferma: rifarlo a ogni tick vorrebbe
   *  dire far lampeggiare i nomi mentre la nuvola si assesta, e costerebbe una
   *  misura di testo per etichetta per frame. Lo zoom non lo rifa' perche' non
   *  cambia niente: ingrandire e' una trasformazione del gruppo, e due
   *  riquadri che non si toccavano non cominciano a toccarsi.
   */
  _placeLabels(selection) {
    const misure = [];
    selection.each(function (d) {
      misure.push({
        id: d.id,
        x: d.x,
        y: d.y,
        /* La larghezza vera del testo reso. `getComputedTextLength` e non
           `getBBox`: vuole la stessa misura ma senza il riquadro, e su una
           WebView e' la strada piu' corta. */
        w: this.getComputedTextLength ? this.getComputedTextLength() : 0,
        r: radiusOf(d.degree),
        priority: d.degree || 0,
      });
    });
    const scelti = placeLabels(misure);
    selection
      .attr('dy', (d) => scelti.get(d.id) ?? 0)
      .attr('display', (d) => (scelti.has(d.id) ? null : 'none'));
  }
}
