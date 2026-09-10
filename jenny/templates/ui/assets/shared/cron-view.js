/** Il modello di vista della sezione «Programmazione»: dal payload alle righe.
 *
 *  Modulo puro **di proposito**: niente DOM, niente `window`, niente i18n
 *  importato — la traduzione arriva come funzione `tr` dal chiamante. Sta in
 *  `shared/` per la stessa ragione di `launcher-rank.js`: è l'unica parte del
 *  pannello che si può provare senza un telefono, e i casi che deve azzeccare
 *  sono precisamente quelli che sul telefono non si riesce a fabbricare — un
 *  worker spento, uno scheduler fermo, una scadenza nel passato.
 *
 *  Tre regole di vocabolario che il pannello non deve reinventare, perché il
 *  servizio le ha già decise (v. `jenny/cron/types.py`):
 *
 *  1. `silenced` **non è un fallimento**: è un monitor che ha guardato e non
 *     aveva niente da dire. Colorarlo di rosso insegna a ignorare i colori.
 *  2. `could_not_check` **non è un `error`**: è il terzo stato, «ha girato ma il
 *     controllo non è avvenuto». Confonderli cancella la distinzione per cui
 *     quello stato esiste — un monitor rotto sembrava un giardino sano.
 *  3. `skipped` non è né l'uno né l'altro: non è partito, e va detto senza
 *     allarme.
 */

/* Come si rende ciascuno dei cinque esiti. `tone` è la sola cosa che il CSS
   guarda; il testo passa da `tr`. */
const STATUS_TONE = {
  ok: 'ok',
  silenced: 'neutral',
  skipped: 'neutral',
  could_not_check: 'warn',
  error: 'bad',
};

/* Sotto questa distanza si dice «adesso» invece di «fra 0 minuti». */
const NOW_WINDOW_MS = 45_000;

const MINUTE = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;

/** Il tono di un esito, o `neutral` per un esito che questa versione non conosce.
 *
 *  Il default è `neutral` e non `bad`: uno store scritto da una versione più
 *  nuova può portare un valore che qui non c'è, e trattare l'ignoto come un
 *  guasto è il modo di far sembrare rotto un aggiornamento riuscito. */
export function statusTone(status) {
  return STATUS_TONE[status] ?? 'neutral';
}

/** Un job è rotto, inerte, spento o sano — in quest'ordine di precedenza.
 *
 *  `broken` prima di `inert` perché un worker che ha una sequenza di controlli
 *  mancati sta dicendo qualcosa di più urgente di «la config ti ha spento», e
 *  `inert` prima di `off` perché un job spento dall'utente non è una notizia. */
export function jobHealth(job) {
  if (job?.health?.consecutive_could_not_check > 0) return 'broken';
  if (job?.effective === 'inert') return 'inert';
  if (job?.effective === 'disabled' || job?.enabled === false) return 'off';
  return 'ok';
}

/* Ordine di lettura: prima quel che chiede attenzione, poi il prossimo a
   suonare. Un `next_run_at_ms` assente vale `Infinity` — la stessa convenzione
   di `CronService.list_jobs` — così un job senza scadenza finisce in fondo
   invece che in cima. */
const HEALTH_RANK = { broken: 0, inert: 1, ok: 2, off: 3 };

export function compareJobs(a, b) {
  const rank = HEALTH_RANK[jobHealth(a)] - HEALTH_RANK[jobHealth(b)];
  if (rank !== 0) return rank;
  const an = a?.next_run_at_ms ?? Infinity;
  const bn = b?.next_run_at_ms ?? Infinity;
  if (an !== bn) return an - bn;
  return String(a?.name ?? '').localeCompare(String(b?.name ?? ''));
}

/** Una durata in una frase breve: «30 minuti», «2 ore», «3 giorni».
 *
 *  Non usa `Intl.RelativeTimeFormat` perché serve anche senza segno (per
 *  «ogni N»), e avere due strade per la stessa unità le farebbe divergere. */
export function humanizeDuration(ms, { tr, locale } = {}) {
  const total = Math.abs(Math.round(ms));
  if (total < MINUTE) {
    return unit(tr, locale, 'second', Math.max(1, Math.round(total / 1000)));
  }
  if (total < HOUR) return unit(tr, locale, 'minute', Math.round(total / MINUTE));
  if (total < DAY) return unit(tr, locale, 'hour', round1(total / HOUR));
  return unit(tr, locale, 'day', round1(total / DAY));
}

/* Mezz'ora di scarto su «ogni 2 ore» sparirebbe arrotondando all'intero, e
   «ogni 2 ore» invece di «ogni 2 ore e mezza» e' una schedulazione diversa: un
   decimale si mostra solo quando cambia la frase. */
function round1(value) {
  return Number.isInteger(value) ? value : Math.round(value * 10) / 10;
}

/* Singolare e plurale sono **due chiavi**, scelte qui.
   ``i18n.t`` non sa pluralizzare — interpola ``{n}`` e basta — e «1 minuti» in
   italiano e' sbagliato in un punto che si legge a ogni riga. La scelta sta nel
   modulo puro, cosi' e' provabile senza un telefono; il file i18n porta le due
   forme e non un'euristica. */
function unit(tr, locale, name, n) {
  // Il numero passa da ``Intl.NumberFormat``: interpolarlo grezzo scriveva
  // «1.9 ore fa» su un telefono in italiano — visto sul dispositivo il
  // 10/09/2026. Cambia una virgola, e cambia su ogni riga che porta un'ora e
  // mezza.
  let text;
  try {
    text = new Intl.NumberFormat(locale).format(n);
  } catch {
    text = String(n);
  }
  // La scelta singolare/plurale resta sul **numero**, non sulla stringa
  // formattata: «1,0» non e' `1`, e un confronto sul testo si romperebbe nella
  // prima lingua che formatta diversamente.
  return tr(`cron.unit.${name}${n === 1 ? '' : 's'}`, { n: text });
}

/** La schedulazione in una frase, dalla struttura e non da una stringa inglese.
 *
 *  Il server manda `{kind, every_ms, expr, at_ms, tz}` proprio perché questa
 *  frase la deve comporre chi conosce la lingua dell'utente. Il tool `cron`
 *  compone la sua, in inglese, per il modello: sono due lettori diversi. */
export function describeSchedule(schedule, { tr, locale, timeZone } = {}) {
  const kind = schedule?.kind;
  if (kind === 'every' && schedule.every_ms) {
    return tr('cron.schedule.every', {
      every: humanizeDuration(schedule.every_ms, { tr, locale }),
    });
  }
  if (kind === 'cron' && schedule.expr) {
    return tr('cron.schedule.expr', { expr: schedule.expr });
  }
  if (kind === 'at' && schedule.at_ms) {
    return tr('cron.schedule.at', {
      when: formatAbsolute(schedule.at_ms, { locale, timeZone }),
    });
  }
  return tr('cron.schedule.unknown');
}

/** Data e ora nel fuso **del job**, non in quello del dispositivo.
 *
 *  È la metà che fa sì che il pannello e la chat dicano la stessa ora: il tool
 *  `cron` formatta con `schedule.tz or default_timezone`, e questo fa lo stesso
 *  col `display_timezone` che il payload porta per ogni job.
 *
 *  Un `timeZone` che `Intl` rifiuta non deve portarsi via la riga: si ricade sul
 *  fuso del dispositivo. Capita con uno store scritto a mano, o su una
 *  piattaforma senza il database dei fusi. */
export function formatAbsolute(ms, { locale, timeZone } = {}) {
  if (!Number.isFinite(ms)) return '';
  const date = new Date(ms);
  const opts = { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' };
  try {
    return new Intl.DateTimeFormat(locale, timeZone ? { ...opts, timeZone } : opts).format(date);
  } catch {
    return new Intl.DateTimeFormat(locale, opts).format(date);
  }
}

/** Il fuso va nominato **solo quando dice qualcosa**, cioè quando diverge da
 *  quello del dispositivo: `09:00 (Asia/Tokyo)` è utile, `09:00 (Europe/Rome)`
 *  su un telefono a Roma è rumore su ogni riga. */
export function timeZoneNote(timeZone) {
  if (!timeZone) return null;
  let local = null;
  try {
    local = new Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    local = null;
  }
  return local && local === timeZone ? null : timeZone;
}

/** Quando succede (o è successo), con il verdetto «in ritardo».
 *
 *  `overdue` è il punto di questa funzione. `next_run_at_ms` è **persistito**:
 *  lo ricalcolano l'avvio del servizio e la fine di ogni run, quindi con il
 *  servizio fermo — o col processo appena riesumato dopo ore di doze — il valore
 *  nello store è nel passato. Un pannello che lo formattasse e basta scriverebbe
 *  «prossima esecuzione: ieri alle 22:30» e sembrerebbe rotto lui. */
export function formatWhen(ms, { nowMs, tr, locale, timeZone } = {}) {
  if (!Number.isFinite(ms)) return null;
  const delta = ms - nowMs;
  const overdue = delta < -NOW_WINDOW_MS;
  let relative;
  if (Math.abs(delta) <= NOW_WINDOW_MS) relative = tr('cron.when.now');
  else if (delta > 0) {
    relative = tr('cron.when.in', { d: humanizeDuration(delta, { tr, locale }) });
  } else {
    relative = tr('cron.when.ago', { d: humanizeDuration(-delta, { tr, locale }) });
  }
  return {
    absolute: formatAbsolute(ms, { locale, timeZone }),
    relative,
    overdue,
    timeZoneNote: timeZoneNote(timeZone),
  };
}

/** Il banner in cima, o `null`. Uno solo: il più importante.
 *
 *  L'ordine è quello dell'utilità, non della gravità astratta. Uno scheduler
 *  fermo spiega da solo ogni «in ritardo» della lista, quindi viene prima: senza
 *  di lui la stessa spiegazione andrebbe ripetuta su ogni riga. */
export function pickBanner(payload) {
  if (!payload || payload.available === false) return { kind: 'unavailable' };
  if (payload.recovery) {
    return { kind: 'recovered', restoredFrom: payload.recovery.restored_from };
  }
  if (payload.service_running === false) return { kind: 'stopped' };
  const inert = (payload.jobs ?? []).filter((j) => j.effective === 'inert');
  if (inert.length) return { kind: 'inert', jobs: inert.map((j) => j.name) };
  return null;
}

/** I task dell'heartbeat, divisi per quel che chiedono a chi legge.
 *
 *  `checkingNothing` è il caso che questo pannello esiste per dire: il job gira,
 *  registra `ok` a ogni run, e non c'è niente da controllare. Lo store da solo
 *  lo renderebbe come salute perfetta. */
export function heartbeatView(block) {
  if (!block) return null;
  const tasks = block.tasks ?? [];
  return {
    filePresent: block.file_present,
    fileReadable: block.file_readable,
    checkingNothing: block.active_task_count === 0,
    counts: {
      total: tasks.length,
      broken: tasks.filter((t) => t.state === 'broken').length,
      pending: tasks.filter((t) => t.state === 'pending').length,
    },
    tasks,
    orphans: block.orphan_checks ?? [],
  };
}

/** Il modello di vista completo. Una chiamata, tutto quel che la sezione rende. */
export function buildCronView(payload, { nowMs, tr, locale } = {}) {
  const stamp = Number.isFinite(nowMs) ? nowMs : payload?.now_ms;
  const banner = pickBanner(payload);
  if (!payload || payload.available === false) {
    return { available: false, banner, rows: [], counts: null, asOf: stamp };
  }
  const rows = [...(payload.jobs ?? [])].sort(compareJobs).map((job) => {
    const tz = job.display_timezone || payload.default_timezone;
    return {
      id: job.id,
      name: job.name,
      kind: job.kind,
      purpose: job.purpose,
      protected: job.protected,
      health: jobHealth(job),
      monitor: job.mode === 'monitor',
      oneShot: job.one_shot,
      messagePreview: job.message_preview,
      message: job.message,
      schedule: describeSchedule(job.schedule, { tr, locale, timeZone: tz }),
      next: formatWhen(job.next_run_at_ms, { nowMs: stamp, tr, locale, timeZone: tz }),
      last: formatWhen(job.last_run_at_ms, { nowMs: stamp, tr, locale, timeZone: tz }),
      lastStatus: job.last_status,
      lastTone: statusTone(job.last_status),
      lastError: job.last_error,
      couldNotCheck: job.health,
      runs: (job.runs ?? []).map((run) => ({
        atMs: run.run_at_ms,
        status: run.status,
        tone: statusTone(run.status),
        durationMs: run.duration_ms,
        error: run.error,
      })),
      heartbeat: heartbeatView(job.heartbeat),
    };
  });
  return { available: true, banner, rows, counts: payload.counts ?? null, asOf: stamp };
}
