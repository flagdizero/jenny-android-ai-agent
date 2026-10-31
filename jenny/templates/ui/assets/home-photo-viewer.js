/** La casa — una foto della galleria a tutto schermo.
 *
 *  Si apre dalla foto aperta nel foglio della galleria: la foto **intera**,
 *  nelle sue proporzioni, con un filo di bordo carta, su un fondo che e' la
 *  stessa foto sfocata e scurita. Sopra il nome della scheda e «1 di 3» con la
 *  ✕; sotto la nota scritta a mano, la data e lo stato, e un puntino per foto.
 *  Si scorre fra le foto col dito (o con ← →), si ingrandisce con due dita o
 *  con un tocco (`shared/pinch-zoom.js`, lo stesso della chat).
 *
 *  E' un `<dialog>` aperto con `showModal()`: il foglio da cui si apre e' gia'
 *  nel top layer, e un `div` con qualunque `z-index` gli starebbe sotto. Porta
 *  la classe `image-lightbox` per la catena di Indietro della casa
 *  (`HomeApp.handleHardwareBack` la chiude prima dei fogli, con `__jennyClose`)
 *  e per il «c'e' qualcosa aperto?» di Jenny.
 *
 *  I `blob:` delle foto sono della galleria (`PhotoUrls`): qui non si revocano.
 */

import { attachPinchZoom } from './shared/pinch-zoom.js';
import { watchHorizontalSwipe } from './shared/horizontal-swipe.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * @param photos   le foto della galleria, nell'ordine della galleria
 *                 (`{ path, caption, created, status }`).
 * @param index    quella da cui si parte.
 * @param title    il nome della scheda.
 * @param urlOf    `(path) => Promise<string>`: il `blob:` di una foto.
 * @param words    `{ count(n, total), day(iso), state(photo), close, prev, next }`.
 * @param onIndex  `(i) => void`: la foto davanti e' cambiata.
 * @returns `close()`.
 */
export function openPhotoViewer({ photos, index = 0, title = '', urlOf, words, onIndex = null }) {
  if (!photos?.length) return () => {};
  let at = Math.max(0, Math.min(index, photos.length - 1));

  const box = el('dialog', 'image-lightbox home-photo-viewer');
  const bg = el('div', 'home-photo-viewer-bg');
  bg.setAttribute('aria-hidden', 'true');
  const top = el('div', 'home-photo-viewer-top');
  const count = el('span', 'home-photo-viewer-count');
  const x = el('button', 'home-photo-viewer-close');
  x.type = 'button';
  x.setAttribute('aria-label', words.close);
  x.innerHTML = '<i class="ti ti-x" aria-hidden="true"></i>';
  top.append(count, x);
  const stage = el('div', 'home-photo-viewer-stage');
  const frame = el('div', 'home-photo-viewer-frame');
  stage.appendChild(frame);
  const bottom = el('div', 'home-photo-viewer-bottom');
  const note = el('p', 'home-photo-viewer-note');
  const sub = el('p', 'home-photo-viewer-sub');
  const dots = el('div', 'home-photo-viewer-dots');
  dots.setAttribute('aria-hidden', 'true');
  if (photos.length > 1) {
    for (let i = 0; i < photos.length; i++) dots.appendChild(el('i'));
  }
  bottom.append(note, sub, dots);
  box.append(bg, top, stage, bottom);

  let img = null;
  const show = (i) => {
    at = (i + photos.length) % photos.length;
    const photo = photos[at];
    /* Un'immagine nuova a ogni foto: il pinch di quella prima (scala, spostamento)
       non deve restare appiccicato alla prossima. */
    img = el('img', 'home-photo-viewer-img');
    img.alt = photo.caption || '';
    frame.replaceChildren(img);
    attachPinchZoom(img);
    const path = photo.path;
    urlOf(path).then((url) => {
      if (!url || photos[at].path !== path) return;
      img.src = url;
      bg.style.backgroundImage = `url("${url}")`;
    });
    count.textContent = words.count(at + 1, photos.length, title);
    note.textContent = photo.caption || '';
    note.hidden = !photo.caption;
    sub.textContent = [words.day(photo.created), words.state(photo)].filter(Boolean).join(' · ');
    [...dots.children].forEach((d, k) => d.classList.toggle('is-on', k === at));
    box.setAttribute('aria-label', words.count(at + 1, photos.length, title));
    onIndex?.(at);
  };

  /* Il dito di lato, a foto non ingrandita: la prossima o la precedente. Il
     riconoscimento e' quello delle pagine della casa (`horizontal-swipe.js`:
     soglia d'asse, dominanza, spazio o velocita'); da ingrandita lo stesso
     gesto sposta la foto (`pinch-zoom.js`), e qui non parte. */
  if (photos.length > 1) {
    watchHorizontalSwipe(stage, {
      canStart: () => !img?.style.transform,
      onEnd: ({ direction, confirm }) => {
        if (confirm) show(at + (direction === 'next' ? 1 : -1));
      },
    });
  }

  const previousFocus = document.activeElement;
  const close = () => {
    if (box.open) box.close();
    box.remove();
    if (previousFocus && previousFocus.isConnected) previousFocus.focus?.();
  };
  box.__jennyClose = close;
  x.addEventListener('click', close);
  box.addEventListener('cancel', (e) => {
    e.preventDefault();
    close();
  });
  box.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') show(at + 1);
    else if (e.key === 'ArrowLeft') show(at - 1);
    else return;
    e.preventDefault();
  });

  document.body.appendChild(box);
  show(at);
  box.showModal();
  x.focus();
  return close;
}
