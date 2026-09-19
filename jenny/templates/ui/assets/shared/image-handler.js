/** Attachment Handler — file picker (immagini, foto, file qualsiasi + scatto
 *  fotocamera via chooser di sistema Android), validazione e base64 encoding
 *  per gli allegati della chat. Il nome storico ``ImageHandler`` è mantenuto
 *  per compatibilità con i chiamanti. */

export class ImageHandler {
  constructor() {
    this._items = [];
    /* **Tre secchi, come il gateway** (`ws_parsing.py`). Ce n'erano due, e il
       commento qui sopra diceva «cap per-tipo allineati al server»: non lo
       erano. Il client non sapeva cosa fosse un video e li contava insieme ai
       file generici, tetto 4 — mentre il server ne accetta **uno**. Due video
       passavano di qui e li rifiutava il gateway a messaggio gia' partito, che
       e' esattamente il caso che questi tetti esistono per evitare.

       `over` e' il codice che manderebbe il server, cosi' un rifiuto deciso qui
       si dice con le stesse identiche parole di uno deciso di la'. */
    this._caps = {
      image: { max: 4, maxBytes: 8 * 1024 * 1024, over: 'too_many_images' },
      video: { max: 1, maxBytes: 20 * 1024 * 1024, over: 'too_many_videos' },
      file: { max: 4, maxBytes: 20 * 1024 * 1024, over: 'too_many_files' },
    };
    this._imageTypes = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
    this._videoTypes = ['video/mp4', 'video/webm', 'video/quicktime'];
    this._input = null;
    this.onChange = null;
    /* Un allegato che non entra va **detto**, non fatto sparire. Prima il
       `continue` lo scartava in silenzio: sceglievi cinque foto, ne comparivano
       quattro, e nessuno ti diceva quale mancasse ne' perche'. */
    this.onReject = null;
  }

  _createFileInput() {
    const input = document.createElement('input');
    input.type = 'file';
    // Su Android WebView l'attributo ``accept`` è ignorato: è il chooser di
    // sistema nativo (onShowFileChooser) a decidere le sorgenti disponibili
    // (file/galleria/fotocamera). ``accept`` resta come hint per i browser.
    input.accept = '*/*';
    input.multiple = true;
    input.style.display = 'none';
    input.addEventListener('change', () => this._handleFiles(input.files));
    document.body.appendChild(input);
    this._input = input;
  }

  trigger() {
    if (!this._input) this._createFileInput();
    this._input.value = '';
    this._input.click();
  }

  /* Lo specchio esatto di `classify_media_item` nel gateway, ordine compreso.
     Il ripiego sul nome vale **solo per le immagini**, ed e' voluto: le catture
     da fotocamera Android arrivano spesso senza MIME (`file.type` vuoto o
     `application/octet-stream`), e senza questo ramo uno scatto finirebbe nel
     secchio dei file. Il server fa lo stesso e non ha il gemello per i video:
     copiarlo solo qui rifarebbe nascere la divergenza, al contrario. */
  _kindOf(file) {
    const mime = String(file.type || '').toLowerCase();
    if (this._videoTypes.includes(mime)) return 'video';
    if (this._imageTypes.includes(mime)) return 'image';
    const generic = !mime || mime === 'application/octet-stream';
    if (generic && this._looksLikeImageName(file.name)) return 'image';
    return 'file';
  }

  _looksLikeImageName(name) {
    const ext = (String(name || '').match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || '';
    return ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext);
  }

  _countByKind(kind) {
    return this._items.filter((it) => it.kind === kind).length;
  }

  async _handleFiles(fileList) {
    for (const file of fileList) {
      const kind = this._kindOf(file);
      const cap = this._caps[kind];
      if (this._countByKind(kind) >= cap.max) {
        this.onReject?.(cap.over);
        continue;
      }
      if (file.size > cap.maxBytes) {
        this.onReject?.('size');
        continue;
      }

      const dataUrl = await this._readAsDataUrl(file);
      this._items.push({
        data_url: dataUrl,
        name: file.name,
        mime: file.type || 'application/octet-stream',
        kind,
        file,
      });
    }
    this.onChange?.(this._items);
  }

  _readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  remove(index) {
    this._items.splice(index, 1);
    this.onChange?.(this._items);
  }

  clear() {
    this._items = [];
    this.onChange?.(this._items);
  }

  /** Payload inviato al gateway: solo ``data_url`` + ``name`` (il MIME viaggia
   *  già dentro il data URL). Nome storico per compatibilità coi chiamanti. */
  getImages() {
    return this._items.map(({ data_url, name }) => ({ data_url, name }));
  }

  /** Voci per il rendering ottimistico nella bolla utente (thumb immagini /
   *  chip file), allineate al renderer condiviso ``_renderMediaAttachments``.
   *  Solo per le immagini forziamo ``kind``; per il resto lo deduce dal nome. */
  getAttachmentEntries() {
    /* `file` non si dichiara: senza `kind` il renderer lo deduce dal nome, ed e'
       piu' bravo di noi su un video a cui Android non ha dato un MIME — qui
       sarebbe un file generico, li' diventa un player. */
    return this._items.map(({ data_url, name, kind }) => ({
      url: data_url,
      name,
      ...(kind === 'file' ? {} : { kind }),
    }));
  }

  get count() {
    return this._items.length;
  }
}
