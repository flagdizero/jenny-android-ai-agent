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
import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';

const D3_SRC = '/html-mobile/assets/vendor/d3@7/d3.min.js';

/* L'inquadratura a riposo. Il margine tiene dentro le etichette, che stanno
   sotto il pallino e sporgono ai lati; la scala massima impedisce a un
   quaderno di tre pagine di diventare tre lune. */
const FIT_PADDING = 40;
const FIT_MAX_SCALE = 1.6;

/* Quanto puo' muoversi un dito e contare ancora come **tocco** sul pallino.
 *
 *  Il pallino ha due gesti sopra: il tocco apre la pagina, il trascinamento la
 *  sposta. Col pollice si pestano, e non e' simmetrico — un tocco che non apre
 *  e' un colpo a vuoto che si ripete, ma un trascinamento che apre anche la
 *  pagina ti porta dentro il lettore e ti fa perdere la disposizione. Quindi
 *  meglio stretta che larga.
 *
 *  Il numero non e' scelto a occhio, ed e' anche l'unica cosa qui che solo un
 *  pollice su un vetro puo' giudicare: il repo ha tre soglie, una per gesto, e
 *  questa e' quella che risponde alla stessa domanda — `shared/pinch-zoom.js`,
 *  «movimento massimo perche' un gesto conti come tap». La mascotte ne ha una
 *  piu' stretta (6) perche' sta sopra un filo che scorre, e un suo
 *  trascinamento involontario porta via la lettura; qui porta via solo un
 *  pallino, che si rimette.
 *
 *  Dichiarata qui e non importata da la': due gesti diversi che oggi
 *  condividono un numero sono due numeri, non uno — legarli vuol dire
 *  romperne uno aggiustando l'altro.
 */
const SOGLIA_TOCCO = 10;

/* Dove restano gli spilli fra un'apertura e l'altra.
 *
 *  **Non `localStorage`**, ed e' una misura del repo e non un gusto: il
 *  commento di `MainActivity.kt` che `shared/launcher-usage-store.js` cita dice
 *  che «il localStorage della WebView non sopravvive al kill (persistenza
 *  asincrona di Chromium)». Jenny e' il launcher del telefono e il sistema la
 *  uccide di routine: una disposizione tenuta li' si sbriciolerebbe da sola,
 *  poco alla volta, e una mappa che ogni tanto dimentica non sembra rotta —
 *  sembra che il salvataggio non funzioni, che e' peggio.
 *
 *  Il ponte nativo sarebbe durevole ma ha **un cassetto solo**, ed e' del
 *  cassetto delle app: prendergli la chiave non e' roba nostra.
 *
 *  Quindi nel workspace, che e' dove stanno i quaderni: sopravvive al kill,
 *  alla reinstallazione, e se lo porta dietro un backup. Un file solo per tutti
 *  i quaderni invece di uno dentro `wikis/<nome>/`, perche' quella cartella la
 *  decide la config (`wiki.wikis_dir`) e il client non la conosce — cercarla
 *  vorrebbe dire indovinarla. Sotto `.jenny/` perche' e' stato dell'interfaccia
 *  e non roba dell'utente: il gestore file lo nasconde da se' (i pattern
 *  `internal`), quindi non compare fra i suoi file.
 */
const FILE_SPILLI = '.jenny/map-layout.json';

/* Quante pagine portano il nome scritto, e quanto lungo.
 *
 *  **Il tetto e' una rete di sicurezza, non una regola di disegno** — e per due
 *  giorni e' stato il contrario. Nato a 10 il 19/09/2026 su una misura vera:
 *  un quaderno da 31 pagine, i titoli di una wiki sono frasi
 *  («Coltivazione-Monstera-Roma — Sostegno, fertilizzazione, crescita»), e su
 *  566 px scrivendoli tutti le etichette diventavano una macchia.
 *
 *  Quella misura resta giusta, il rimedio no. Il difetto l'ha visto l'utente il
 *  21/09: su una mappa da 21 pagine restavano undici pallini muti **con lo
 *  spazio attorno visibile**, perche' il tetto li escludeva prima che qualcuno
 *  misurasse. Un numero che non guarda il disegno non puo' avere ragione ai due
 *  estremi — su una nuvola larga e sparsa dieci sono pochi, su una strettissima
 *  sono troppi.
 *
 *  A decidere e' gia' `placeLabels`, che **ordina per collegamenti** (l'unica
 *  domanda a cui una mappa risponde meglio di un elenco: dove si annoda il
 *  quaderno) e butta chi non ci sta. Il tetto era in buona parte un doppione; la
 *  parte che non lo era — «ci sta» vuol dire «i riquadri non si toccano», non
 *  «si legge» — la paga meglio un numero alto. A quaranta, su un quaderno
 *  normale decide lo spazio; su uno enorme il tetto ferma il muro di testo prima
 *  che si formi. Costa trenta misure di testo in piu', una volta sola, a fisica
 *  ferma.
 *
 *  Gli altri restano pallini, e per sapere come si chiamano c'e' la linguetta
 *  accanto, che li elenca tutti e li cerca.
 */
const MAX_LABELS = 40;
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
  /* Nessuna scorciatoia per i quaderni piccoli. Ce n'era una — «sotto le
     tredici pagine si scrivono tutte» — e con un tetto a 10 cambiava la
     risposta; a 40 non ne cambia nessuna, perche' undici nodi passano interi
     dallo `slice` comunque. Un ramo che non puo' cambiare l'esito e' solo un
     posto in piu' dove sbagliare. */
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
    /* Vero appena l'utente ha messo le mani sulla mappa: spostata, avvicinata
       o un pallino trascinato. Da quel momento quel che si vede e' una sua
       decisione, e nessuno la riscrive — v. `_inquadra`.

       Copre tutti e tre perche' tutti e tre rispondono alla stessa domanda:
       **chi ha deciso cosa c'e' a schermo.** Lo spostamento sceglie da dove
       guardare, il trascinamento dove sta una pagina; un'inquadratura
       automatica dopo l'uno o l'altro e' un cambiamento che l'utente non ha
       chiesto e non capisce. */
    this._presaInMano = false;
    /* Quale quaderno e' disegnato: e' la chiave degli spilli. */
    this._quaderno = null;
    /* Gli spilli letti da disco, per id. Null finche' non si e' letto. */
    this._spilli = null;
  }

  /** Disegna la mappa di *data*. La stessa risposta dell'elenco.
   *
   *  `quaderno` e' il nome, e serve solo come chiave degli spilli: la mappa in
   *  se' non ha bisogno di sapere di chi e'.
   */
  async draw(data, quaderno) {
    const gen = ++this._gen;
    this._quaderno = quaderno || null;
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
    /* Gli spilli **prima** del disegno: applicarli dopo vorrebbe dire far
       partire la fisica da posizioni casuali e poi strattonare i nodi al loro
       posto sotto gli occhi. Una lettura per disegno, non per apertura: la
       linguetta che ritorna esce prima, sopra. */
    const spilli = await this._leggiSpilli();
    if (gen !== this._gen) return;
    for (const n of nodes) {
      const p = spilli?.[n.id];
      if (!p) continue;
      n.x = n.fx = p[0];
      n.y = n.fy = p[1];
    }
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
    /* Un disegno nuovo e' una mappa da capo, quindi si riparte a mani libere:
       i nodi sono oggetti nuovi e gli spilli del disegno precedente non ci sono
       piu' (v. `_trascina`). **Qui e non in `draw`**: per
       una risposta gia' disegnata `draw` esce subito (`this._drawn === data`),
       quindi azzerarlo li' vorrebbe dire buttare via dove l'utente stava
       guardando ogni volta che torna sulla linguetta. */
    this._presaInMano = false;
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

    dot.call(this._trascina(nodes));

    /* Si trascina e si avvicina: su 590x566 un quaderno da sessanta pagine non
       ci sta, e rimpicciolire i pallini non lo renderebbe leggibile. */
    const zoom = d3.zoom()
      .scaleExtent([0.4, 4])
      .on('zoom', (e) => this._onZoom(e, root));
    svg.call(zoom);

    this._sim.on('end', () => {
      /* I nomi si ricollocano **sempre**: dipendono da dove stanno i nodi, non
         da dove guarda l'utente. */
      this._placeLabels(name);
      this._inquadra(svg, zoom, nodes, w, h);
    });
  }

  /** Cosa fa un evento di zoom, gesto o no.
   *
   *  D3 manda lo stesso evento per un dito e per un `zoom.transform` scritto da
   *  noi, e li distingue con `sourceEvent`: c'e' solo nel primo caso. E' la
   *  differenza fra «l'utente ha guardato da qualche parte» e «ci siamo
   *  inquadrati da soli», e serve a `_inquadra` per non passare sopra al dito.
   */
  _onZoom(e, root) {
    if (e.sourceEvent) this._presaInMano = true;
    root.attr('transform', e.transform);
  }

  /** Gli spilli di questo quaderno, o null se non ce ne sono.
   *
   *  Il file puo' non esistere — e' il caso normale, la prima volta — e un 404
   *  non e' un guasto: si torna null e la mappa parte a mani libere. Qualunque
   *  altro inciampo (file illeggibile, JSON rotto da un'altra versione) finisce
   *  nello stesso posto **di proposito**: una disposizione perduta e' un
   *  peccato, una mappa che non si disegna e' un guasto, e fra i due non c'e'
   *  partita.
   */
  async _leggiSpilli() {
    if (!this._quaderno) return null;
    if (this._spilli) return this._spilli[this._quaderno] || null;
    try {
      const r = await api.readWorkspaceFile(FILE_SPILLI);
      this._spilli = JSON.parse(r?.content || '{}') || {};
    } catch (err) {
      this._spilli = {};
    }
    return this._spilli[this._quaderno] || null;
  }

  /** Scrive gli spilli di questo quaderno. Chiamata a fine trascinamento.
   *
   *  **Si salva quel che e' a schermo adesso**, non quel che c'era piu' quel
   *  che si e' aggiunto: una pagina cancellata dal quaderno sparisce dal file
   *  al primo trascinamento successivo, senza che nessuno debba ricordarsene.
   *  Una pagina *rinominata* invece cambia id, quindi il suo spillo resta
   *  orfano e viene buttato allo stesso giro — la disposizione di quella pagina
   *  si perde, ed e' il prezzo di una chiave che e' il percorso.
   *
   *  Gli altri quaderni nel file restano intatti: sono chiavi diverse, e
   *  riscrivere solo la propria e' anche quel che rende innocuo un secondo
   *  disegno aperto altrove.
   */
  async _salvaSpilli(nodes) {
    if (!this._quaderno) return;
    const miei = {};
    for (const n of nodes) {
      if (n.fx === null || n.fx === undefined) continue;
      miei[n.id] = [Math.round(n.fx), Math.round(n.fy)];
    }
    const tutti = { ...(this._spilli || {}), [this._quaderno]: miei };
    this._spilli = tutti;
    const testo = JSON.stringify(tutti);
    try {
      await rpc.writeWorkspaceFile(FILE_SPILLI, testo);
    } catch (err) {
      /* La cartella puo' non esserci su un workspace appena nato: `.jenny/` la
         creano le funzioni che ci scrivono, e questa potrebbe essere la prima.
         Un solo secondo tentativo, poi si lascia perdere in silenzio — la mappa
         a schermo e' gia' come l'utente l'ha messa, e un errore sbandierato per
         una disposizione non salvata sarebbe rumore sopra un gesto riuscito. */
      try {
        await api.createWorkspaceFolder('.jenny');
        await rpc.writeWorkspaceFile(FILE_SPILLI, testo);
      } catch (err2) {
        console.warn('casa.map: spilli non salvati', err2);
      }
    }
  }

  /** Prendere un pallino e spostarlo. **Resta dove lo metti.**
   *
   *  Chiesto dall'utente il 21/09/2026, e la scelta e' sua: o il pallino torna
   *  dove lo vuole la fisica appena lo molli, o ci resta. Il grafo
   *  dell'officina faceva la prima — serviva a guardare sotto, e su una nuvola
   *  annodata la matassa si richiudeva appena mollata. Qui fa la seconda: il
   *  pallino ha gia' un gesto che lo apre, quindi trascinarlo e' l'unico altro
   *  motivo per metterci il dito sopra, e «sbircia e lascia richiudere» non e'
   *  un motivo. Con lo spillo una nuvola stretta si apre a mano una volta e poi
   *  si legge.
   *
   *  Lo spillo e' l'`end` che **non** rilascia `fx`/`fy`: le forze continuano a
   *  tirare gli altri, e questo sta fermo. Dura quanto il disegno — i nodi
   *  nascono da `toSimulation` a ogni `_render`, quindi una mappa ridisegnata
   *  riparte senza spilli. Ricordarli sarebbe una terza decisione (dove, con
   *  che chiave, e cosa succede quando una pagina cambia nome) e va chiesta a
   *  parte.
   *
   *  **`clickDistance` e' la parte che si rompe per prima**, ed e' anche il
   *  motivo per cui il numero ha un commento suo (v. `SOGLIA_TOCCO`): sotto la
   *  soglia il gesto resta un tocco e la pagina si apre, sopra D3 sopprime il
   *  click e il pallino si e' solo spostato. Senza, ogni trascinamento aprirebbe
   *  anche la pagina — cioe' ti butterebbe nel lettore proprio mentre stavi
   *  sistemando la mappa.
   *
   *  `alphaTarget(0.3).restart()` riaccende la fisica mentre trascini, o gli
   *  altri nodi resterebbero fermi e i fili si allungherebbero da soli senza
   *  che niente si riassesti. Si spegne all'`end`, e la quiete che segue
   *  ricolloca i nomi (v. `_placeLabels`, chiamato da `sim.on('end')`).
   */
  _trascina(nodes) {
    return d3.drag()
      .clickDistance(SOGLIA_TOCCO)
      .on('start', (e, d) => {
        /* Da qui la mappa e' sua: senza questo, la quiete dopo il
           trascinamento reinquadrerebbe la nuvola e sposterebbe sotto gli occhi
           il pallino appena messo a posto. */
        this._presaInMano = true;
        if (!e.active) this._sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (e, d) => {
        d.fx = e.x;
        d.fy = e.y;
      })
      .on('end', (e) => {
        if (!e.active) this._sim.alphaTarget(0);
        // E `fx`/`fy` restano: e' tutto lo spillo.
        /* Si salva alzando il dito, che e' l'unico momento in cui uno spillo
           nasce o si sposta. Non si aspetta: la scrittura va su localhost e il
           gesto e' gia' finito. */
        this._salvaSpilli(nodes);
      });
  }

  /** Inquadra la nuvola: **una volta, e mai contro il dito.**
   *
   *  A fisica ferma la nuvola sta dove l'hanno lasciata le forze — che non e' il
   *  centro. Visto al banco: sei nodi raccolti in basso a destra e mezza stanza
   *  vuota. Si inquadra a riposo e non a ogni tick: inseguire una simulazione
   *  che si assesta vuol dire farla ballare sotto lo sguardo.
   *
   *  **E qui c'era un difetto, chiesto dall'utente il 21/09/2026: «né drag né
   *  pan, niente».** Questa riga girava senza condizioni. La simulazione si
   *  ferma cinque secondi circa dopo l'apertura della linguetta (~150 tick a
   *  `alphaDecay(0.045)`, e il Titan ne fa una trentina al secondo): chi apriva
   *  la mappa e spostava subito la vedeva **tornare indietro da sola**. Non era
   *  lo spostamento che non funzionava — era lo spostamento riscritto un istante
   *  dopo.
   *
   *  La guardia vale anche in avanti, ed e' la ragione per cui arriva prima del
   *  trascinamento dei nodi (v. `.agent/casa-mappa-dito-plan.md`): trascinare
   *  fa ripartire la fisica, quindi `end` scatta di nuovo, quindi senza questa
   *  riga **ogni pallino trascinato costerebbe un salto della vista** nel
   *  momento in cui si alza il dito.
   */
  _inquadra(svg, zoom, nodes, w, h) {
    if (this._presaInMano) return;
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
