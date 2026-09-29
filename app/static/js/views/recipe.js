/* #/recipe/<basename> — one Recipe, and the two writes the spec puts here.
 *
 * Four panels, each with its own explicit `loading -> ready | error` state
 * (§9.2's independent-panel-loading rule):
 *
 *   1. the note body (steps, verbatim),
 *   2. the Ingredient and Seasoning rows (the chip row),
 *   3. the Cooking Tools and the Recipe Cooking History,
 *   4. the `待 Obsidian 同步` badge, which is a SECOND request
 *      (`GET /api/cook-logs?date=`) and therefore genuinely fails on its own.
 *
 * 1–3 arrive in the one response §9.16 defines for a detail, and each keeps its
 * own state node so one cannot blank another. 4 is a separate fetch, which is
 * what makes the rule testable rather than decorative: a day with no daily note
 * answers 404 there and the recipe above it still reads.
 *
 * "Nothing here yet" — an empty history, an empty tool list — renders ONLY from
 * a successful response, never during loading and never on failure, because
 * `panels.js`'s `loadPanel` hands `onReady` a successful body and nothing else.
 *
 * **§2i's back-button rule is applied to the happy path AND to the
 * loading-error, not-found, and 503 states.** A failed load still returns to
 * where the user came from; a hardcoded `navigate('#/')` on a detail view
 * reachable from the list, from a shortlist, and from search is exactly the bug
 * the rule exists to prevent. `basename` is only ever sent back as a path
 * SEGMENT the server resolves against its own index — never joined to a path.
 *
 * **F5: the Cooking Log is online-only and is never queued.** Offline the
 * `做过了` control is disabled and says `离线：需要连接后记录`. This file reaches the
 * outbox only through `writeIntent(SHORTLIST_ADD, …)`, and
 * `app/static/js/domain-intents.js` has no intent type for a Cooking Log at all
 * — §9.18.1 records why, and `tests/js/outbox_contract.test.mjs` asserts the
 * absence. Success is never claimed optimistically — the success text is
 * printed only from a 201/200 body, and a queued edit says `已排队` rather than
 * `已加入`.
 *
 * **Every failure that carries a `message` renders it VERBATIM** — `error.message`
 * from `api.js`, which IS the server's string. This view composes no substitute
 * sentence for a code the server already worded, and it never reaches into
 * `error.data.message`, which is the same string by a different door.
 *
 * **The Cooking Log's revision is PER DATE, and it is kept in a per-date map
 * rather than in one `let` for the mount.** A Cooking Log write is a statement
 * about a *date* — `POST /api/cook-logs` names one daily note — and a note
 * revision is `sha256` of one date's bytes. So "the revision this view knows"
 * is not a scalar, it is one revision per date the view has read, and holding a
 * single one is a category error that the server's compare-and-swap then
 * reports to the user as a conflict that did not happen: log a cook for
 * 2026-03-10, change the date to 2026-03-12, tap 记录 again, and the second
 * write carried 03-10's revision, so it 409'd and 03-12's note got no link.
 * Nothing had been edited. The map is what makes the request carry the revision
 * of **the note it is about to write**, which is the whole of the contract:
 * `null` for a date this view has never read (there is no base to compare
 * against, and F4's `retryable` promises exactly that replay will work once the
 * note exists) and a real revision for a date it has read, so an out-of-band
 * edit between the read and the write still 409s. A clear-and-refetch on a date
 * change would reconstruct the same information with a network round trip and a
 * window in which the write must either block or guess.
 *
 * **Every in-flight `.disabled = true` is paired with `unstickOnTimeout`** and
 * its `clear()` runs in a `finally`, so a hung fetch re-enables the controls
 * instead of locking the app up. The offline disable is a STATE rather than a
 * flight, and is undone by the `online` event.
 */

import {
  apiFetch,
  getSession,
  isReadOnly,
  parseStrict,
  strictQuery,
} from '../api.js?v=__APP_VERSION__';
import {
  currentEntryEpoch,
  goBack,
  readViewState,
  saveViewState,
} from '../router.js?v=__APP_VERSION__';
import { isDebugEnabled } from '../prefs.js?v=__APP_VERSION__';
import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import { chipRow, scoredSlots } from '../chips.js?v=__APP_VERSION__';
import { SHORTLIST_ADD, writeIntent } from '../domain-intents.js?v=__APP_VERSION__';
import { headline } from '../logic/format.js?v=__APP_VERSION__';
import { unstickOnTimeout } from '../pwa/unstick-on-timeout.js?v=__APP_VERSION__';
import {
  emptyState,
  errorState,
  isSourceUnavailable,
  loadingState,
  loadPanel,
  sourceUnavailableState,
} from '../panels.js?v=__APP_VERSION__';

/** D3's closed enum, in the order the controls are laid out. */
export const MEALS = Object.freeze([
  { slot: 'breakfast', label: '早餐' },
  { slot: 'lunch', label: '午餐' },
  { slot: 'dinner', label: '晚餐' },
]);

/** F5's stated offline reason. One string, so there is one wording to keep. */
export const OFFLINE_REASON = '离线：需要连接后记录';

/** §13.5's badge text.
 *
 * **No longer driven by `cook_log_receipts.recipe_tracker_synced`.** That column
 * is inserted as 0 and nothing ever writes 1, so a badge keyed on it was on
 * screen permanently. The verdict is now the detail payload's `pendingCookDates`
 * — the server's own comparison, made without a write — and the field has been
 * removed from `GET /api/cook-logs` so it cannot be read here again by accident.
 */
export const TRACKER_BADGE = '待 Obsidian 同步';

const UNSTICK_DELAY_MS = 15000;

const recipePath = (noteName) => `/api/recipes/${encodeURIComponent(noteName)}`;

/**
 * `today` in the app's own timezone, `YYYY-MM-DD`.
 *
 * Not `toISOString().slice(0, 10)`: that is UTC, so a 20:00 cook in
 * `America/New_York` would be filed under tomorrow's daily note. The session
 * publishes `appTimezone` precisely so "today" has one definition, and the
 * server derives the daily-note path from that same setting.
 */
export function todayIn(timeZone) {
  const now = new Date();
  if (!timeZone) {
    const pad = (value) => String(value).padStart(2, '0');
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const pick = (type) => parts.find((part) => part.type === type).value;
  return `${pick('year')}-${pick('month')}-${pick('day')}`;
}

function appTimezone() {
  const session = getSession();
  return (session && session.appTimezone) || null;
}

/** One `label: value` line, skipped when the server sent nothing.
 *
 * `format` composes the displayed text from the raw one. It exists so a derived
 * value — the tracker's age, say — is composed *before* the node is built rather
 * than written into it afterwards: patching `textContent` on a node that is
 * already in the tree is a second mutation of the same fact, and it is the shape
 * that lets a stale value survive a re-render.
 */
function field(label, value, format) {
  if (value === null || value === undefined || value === '') return null;
  // An EMPTY LIST is nothing too. `cookingPatterns: []` is a real payload — the
  // tracker wrote the key and no pattern qualified — and joining it to `''` with
  // `、` would render a labelled row with an empty value, which reads as "the app
  // lost this" rather than "there was nothing to say".
  if (Array.isArray(value) && value.length === 0) return null;
  const joined = Array.isArray(value) ? value.join('、') : String(value);
  return el('div', { class: 'field' }, [
    el('span', { class: 'field__label', text: label }),
    el('span', { class: 'field__value', text: format ? format(joined) : joined }),
  ]);
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Whole calendar days from `stamp` to `today`, or `null` if either is unreadable.
 *
 * `today` is the caller's already-localised `YYYY-MM-DD` (`todayIn(appTimezone())`),
 * so this function does no timezone work at all — it subtracts two date indices.
 * That is why it is pure and takes `today` as an argument: a view test pins the
 * bucket against a fixed date instead of racing the wall clock.
 *
 * **Calendar days, not a duration.** "3 天前" for a cook logged at 23:55 and
 * read at 00:04 is the answer a person means; an elapsed-hours figure would say
 * "0 天前" and then a number of hours, and the number of hours is a fact about
 * when the phone was picked up.
 *
 * `null` covers three unreadable cases and never becomes `NaN`. `auto_updated` is
 * the tracker's own free text — a space-separated `YYYY-MM-DD HH:mm`, which is
 * not the `T` a bare `Date.parse` wants — so it is read by hand. A stamp in the
 * future is also `null`: a clock-skew bug rendered as `-1 天前` would be a wrong
 * fact wearing a real one's clothes.
 */
export function ageInDays(stamp, today) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(stamp || ''));
  if (!match) return null;
  const then = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  // Compare in UTC on purpose: both sides are already Y-M-D in the app timezone,
  // so the arithmetic is date-index arithmetic and no offset can shift a day.
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(today);
  if (!parts) return null;
  const now = Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  const days = Math.round((now - then) / DAY_MS);
  return days >= 0 ? days : null;
}

/**
 * The bucket label for a day count, or `''` when there is no count.
 *
 * Buckets are deliberately coarse. "45 天" is really 1.5 months, and a label
 * that slid between units as it aged would be wrong in a way nobody could
 * check; the exact date is always rendered beside this, so the rounding is
 * visible rather than hidden. A panel reading "6 个月前 · 2026-03-11" cannot be
 * wrong in a way that matters; one reading "184 天前" invites a reading of
 * precision it does not have.
 */
export function ageLabel(days) {
  if (days === null) return '';
  if (days === 0) return '今天';
  if (days === 1) return '昨天';
  if (days < 30) return `${days} 天前`;
  if (days < 365) return `${Math.floor(days / 30)} 个月前`;
  const years = Math.floor(days / 365);
  const months = Math.floor((days % 365) / 30);
  return months > 0 ? `${years} 年 ${months} 个月前` : `${years} 年前`;
}

/**
 * The `⚠ 还没算进次数` block, or `null` when nothing is behind.
 *
 * **This is the one part of the panel that is about the app rather than about
 * the recipe.** The nine fields above are recomputed only when the note is opened
 * in Obsidian, so after a cook logged here they are correct-looking and stale, and
 * nothing else on this surface would say so. The remedy is named because the
 * user has to perform it — open the note — and a warning without a remedy is
 * just anxiety.
 *
 * It is deliberately NOT a count the client invents from `cookingCount`: that
 * field is the tracker's, and the difference between it and reality is what this
 * block is reporting, so deriving one from the other would be circular.
 */
function trackerRemedy(autoUpdated, today) {
  const lastRun = ageLabel(ageInDays(autoUpdated, today));
  return lastRun
    ? `Obsidian 的 recipeTracker 上次跑在 ${autoUpdated}（${lastRun}）。在 Obsidian 里打开这份菜谱，次数才会更新。`
    : '在 Obsidian 里打开这份菜谱，次数才会更新。';
}

/**
 * The mirror image of `stalenessPanel`: a cook retracted here whose date the
 * tracker's frontmatter still counts. `pendingRetractionDates` is the server's
 * verdict (the retracted date is `last_cooked` and the tracker has not run since);
 * this only renders it. Same remedy, because the tracker is what fixes both.
 */
function retractionPanel(pendingRetractionDates, autoUpdated, today) {
  if (!Array.isArray(pendingRetractionDates) || pendingRetractionDates.length === 0) return null;
  return el('div', { class: 'history-stale', dataset: { role: 'history-retracted' } }, [
    el('p', {
      class: 'history-stale__head',
      text: `⚠ 有 ${pendingRetractionDates.length} 次撤销还没从上面的次数里扣掉`,
    }),
    el('p', { class: 'history-stale__body', text: `在这里撤销的：${pendingRetractionDates.join('、')}` }),
    el('p', { class: 'history-stale__body', text: trackerRemedy(autoUpdated, today) }),
  ]);
}

function stalenessPanel(pendingCookDates, autoUpdated, today) {
  if (!Array.isArray(pendingCookDates) || pendingCookDates.length === 0) return null;
  const dates = pendingCookDates.join('、');
  const remedy = trackerRemedy(autoUpdated, today);
  return el('div', { class: 'history-stale', dataset: { role: 'history-stale' } }, [
    el('p', {
      class: 'history-stale__head',
      text: `⚠ 有 ${pendingCookDates.length} 次记录还没算进上面的次数`,
    }),
    el('p', { class: 'history-stale__body', text: `在这里记的：${dates}` }),
    el('p', { class: 'history-stale__body', text: remedy }),
  ]);
}

/**
 * `频率` — the tracker's average gap in days, or nothing.
 *
 * **`recipeTracker` writes `0` whenever `cooking_count` is 1** (`Math.round(
 * daysBetween / totalCount)` is guarded by `totalCount > 1`, and the guard's
 * own else-branch is `0`). So a 0 does not mean "every zero days" — it means
 * *there is no average to report*, and it is what 7 of the 16 real notes carry.
 * Rendering it as a number puts a confident false figure in a field the user
 * would otherwise trust, which is the whole class of thing this panel exists to
 * stop doing.
 *
 * A `0` is omitted rather than shown as `—`: the honest statement is that the
 * value does not exist, and a dash still occupies a labelled row implying one
 * was expected. `cookingCount` beside it already says `1 次`, which is the fact.
 */
function frequencyField(frequency) {
  return frequency === 0 || frequency === null || frequency === undefined
    ? null
    : field('频率', frequency);
}

/**
 * The two hand-authored descriptive fields: where the recipe came from, and how
 * long it takes.
 *
 * `来源` is a LIST in 13 of the 16 real notes and a bare string in 2, so it is
 * rendered through the same `field()` the history uses and the server sends it
 * as a list; the two shapes are already reconciled server-side and a client that
 * re-branched on them would be a second copy of that rule.
 *
 * Returns `null` when neither has a value, so the caller can skip the heading
 * rather than print `配方信息` above an empty box.
 */
function metaPanel(source = [], durationMinutes = null) {
  const rows = [field('来源', source), field('时长（分钟）', durationMinutes)].filter(Boolean);
  if (rows.length === 0) return null;
  return el('div', { class: 'fields', dataset: { role: 'recipe-meta' } }, rows);
}

/**
 * Cooking History, plus the one thing the history itself cannot tell you.
 *
 * `pendingCookDates` is the server's comparison, not a client one: it knows which
 * logged cooks the tracker has not counted, and a client that recomputed that
 * from `lastCooked` would be re-deriving the rule that is easy to get subtly
 * wrong (same-day counts as counted). The dates are rendered, never summed, so
 * what the user reads is the evidence and not a total.
 */
function historyPanel(history = {}, pendingCookDates = [], today = '', pendingRetractionDates = []) {
  const autoUpdated = history.autoUpdated;
  const stale = stalenessPanel(pendingCookDates, autoUpdated, today);
  const retracted = retractionPanel(pendingRetractionDates, autoUpdated, today);
  const rows = [
    field('第一次做', history.firstCooked),
    field('最近一次', history.lastCooked),
    field('做过次数', history.cookingCount),
    frequencyField(history.cookingFrequency),
    field('年份', history.cookingYears),
    field('近期活跃', history.recentActivity),
    field('喜欢的季节', history.favoriteSeason),
    field('做法模式', history.cookingPatterns),
    // The stamp AND its age. The date alone is what the tracker wrote and says
    // nothing about whether it is current; the age is the part the user can act
    // on. An unparseable stamp renders as the bare date, never as nothing.
    field('frontmatter 自动更新于', autoUpdated, (raw) => {
      const days = ageInDays(raw, today);
      const label = ageLabel(days);
      return label ? `${raw}（${label}）` : raw;
    }),
  ].filter(Boolean);
  if (rows.length === 0 && !stale && !retracted) {
    return emptyState({
      title: '还没有做过这道菜。',
      body: '记一次之后，Obsidian 的 recipeTracker 会在打开这份笔记时把次数写回 frontmatter。',
      dataset: { role: 'empty-history' },
    });
  }
  return el('div', { class: 'history' }, [
    rows.length > 0 ? el('div', { class: 'fields', dataset: { role: 'history' } }, rows) : null,
    stale,
    retracted,
  ]);
}

/**
 * Cooking Tools are REQUIREMENTS, not stock: displayed, never scored, never
 * matched against the catalog, and in no headline (CONTEXT.md, §9.13.1). They
 * therefore carry a neutral `chip--tool` class and not one of D4's five.
 */
function toolsPanel(tools = []) {
  if (tools.length === 0) {
    return emptyState({
      title: '这份菜谱没写需要什么工具。',
      body: '',
      dataset: { role: 'empty-tools' },
    });
  }
  return el(
    'div',
    { class: 'scroll-row chip-row', dataset: { role: 'tools' } },
    tools.map((tool) => el('span', { class: 'chip chip--tool', dataset: { role: 'tool' }, text: tool })),
  );
}

export function mount(root, params = {}) {
  const listeners = [];
  const panels = [];
  let controller = null;
  /* date -> sha256 of that date's daily note, for the dates THIS view has
   * actually read or written. Deliberately a Map and not a scalar: see the
   * per-date note in the header. A date absent from it has never been read, so
   * the write about to be made is not a compare-and-swap against anything and
   * says so with `baseRevision: null` rather than with another date's hash. */
  const noteRevisions = new Map();
  let inFlight = false;

  const name = typeof params.basename === 'string' ? params.basename : '';
  const strict = parseStrict(window.location.search);
  /** The detail payload's `pendingCookDates`, read by BOTH the history panel and
   * the tracker badge — the two must never disagree about whether the tracker is
   * behind, so they read one value rather than each asking a different question. */
  let pendingCookDates = [];
  /** The detail payload's `retractableCooks` (server clock) and
   * `pendingRetractionDates`. Held in the mount like `pendingCookDates`, for the
   * same reason: the history panel and the badge read one value. */
  let retractableCooks = [];
  let pendingRetractionDates = [];
  const debug = isDebugEnabled();

  function listen(target, type, handler) {
    target.addEventListener(type, handler);
    listeners.push(() => target.removeEventListener(type, handler));
  }

  // The router restores this view's own scroll offset out of the entry's state
  // on a traversal or a resume re-render; here the view keeps that entry's
  // state current while the user is still on it. Scroll only — see the header
  // note in router.js: a save from mount() or unmount() would land in the wrong
  // history entry, because the browser has already pushed the destination by
  // the time a hashchange handler runs.
  //
  // AND NOT AFTER A TRAVERSAL. Reading the entry before the router touches
  // anything is not by itself enough: on a Back traversal the browser dispatches
  // `popstate`, then resets the scroll as it commits the arrival, and that reset
  // is an ordinary scroll event THIS view's listener is still attached to. So
  // the outgoing detail view wrote the engine's mid-restore value into the entry
  // being arrived at, and the router then read the origin's own 640 back as 244
  // (measured in Chromium: `popstate y=0 hist=640` / `scroll y=244 hist=640` /
  // `hashchange y=244 hist=244`) — a restore that lands 400px short and, because
  // `history.state` now holds the wrong number too, stays wrong on a second Back.
  //
  // The guard is the entry epoch rather than a detach on `popstate` because
  // `trackScroll` is rAF-throttled: this comparison happens at the deferred save,
  // which is always after the traversal's own dispatch, so it is right whichever
  // order the engine delivers the events in. A detach is defeated outright by a
  // reset scroll that arrives before `popstate`, and a traversal that never
  // changes the hash would leave a detached view with no tracking at all.
  const entryEpoch = currentEntryEpoch();
  const detachScroll = trackScroll(() => {
    if (currentEntryEpoch() !== entryEpoch) return;
    saveViewState({ scrollTop: window.scrollY });
  });

  /* Same reason as home.js: the router restores with `scrollTo` immediately
   * after `mount()`, and a detail view is a loading state at that moment, so a
   * long Back offset would clamp. `undefined` on a fresh forward navigation, so
   * the re-application is a no-op there. */
  const resumeTop = readViewState().scrollTop;

  const back = el('button', { type: 'button', class: 'button', text: '返回' });
  listen(back, 'click', () => goBack());
  const backRow = el('div', { class: 'settings-actions' }, [back]);

  const title = el('h2', { text: name || '菜谱' });
  const head = el('div', { dataset: { role: 'head' } });
  const chipsSlot = el('div', { dataset: { role: 'chips-slot' } });
  const metaSlot = el('div', { dataset: { role: 'meta-slot' } });
  const metaHead = el('h3', { class: 'settings-subhead', text: '配方信息' });
  const toolsSlot = el('div', { dataset: { role: 'tools-slot' } });
  const stepsSlot = el('div', { dataset: { role: 'steps-slot' } });
  const historySlot = el('div', { dataset: { role: 'history-slot' } });
  const badgeSlot = el('div', { dataset: { role: 'tracker-slot' } });
  const mealSlot = el('div', { class: 'settings-actions', dataset: { role: 'meals' } });
  const status = el('p', { class: 'muted', dataset: { role: 'log-status' } });
  const logError = el('div', { dataset: { role: 'log-error' } });
  const offlineReason = el('p', { class: 'muted', dataset: { role: 'log-reason' } });
  const logButton = el('button', {
    type: 'button',
    class: 'button',
    dataset: { role: 'log-button' },
    text: '做过了',
  });

  const dateInput = el('input', { type: 'date', class: 'log-date', dataset: { role: 'log-date' } });
  const confirm = el('button', { type: 'button', class: 'button', text: '记录' });
  const cancel = el('button', { type: 'button', class: 'button', text: '取消' });
  // `.form-sheet` carries the vendored Pattern L cap
  // (`max-height: calc(100dvh - 32px)`), and `input` is already `font-size:
  // 16px` in the vendored baseline, so iOS does not zoom the picker. `hidden` is
  // exactly what main.js's `isModalOpen()` looks for, which is what stops an
  // update from reloading the page underneath a half-typed date (F18 §4e).
  const picker = el('div', { class: 'form-sheet date-picker', dataset: { role: 'date-picker' } }, [
    el('p', { class: 'muted', text: '记到哪一天？' }),
    dateInput,
    el('div', { class: 'settings-actions' }, [confirm, cancel]),
  ]);
  picker.setAttribute('hidden', '');

  const cooksSlot = el('div', { dataset: { role: 'retract-slot' } });
  const retractStatus = el('p', { class: 'muted', dataset: { role: 'retract-status' } });

  const logSlot = el('div', { dataset: { role: 'log-slot' } }, [
    el('div', { class: 'settings-actions' }, [logButton]),
    offlineReason,
    status,
    logError,
    picker,
    cooksSlot,
    retractStatus,
  ]);

  root.appendChild(
    el('section', { class: 'panel', dataset: { view: 'recipe' } }, [
      backRow,
      title,
      head,
      // Both hidden until `onReady` decides there is something to say: a
      // recipe with no 来源 and a blank 时长 gets no heading above no content.
      metaHead,
      metaSlot,
      el('h3', { class: 'settings-subhead', text: '食材' }),
      chipsSlot,
      el('h3', { class: 'settings-subhead', text: '烹饪工具' }),
      toolsSlot,
      el('h3', { class: 'settings-subhead', text: '做法' }),
      stepsSlot,
      el('h3', { class: 'settings-subhead', text: '烹饪记录' }),
      historySlot,
      badgeSlot,
      mealSlot,
      logSlot,
    ]),
  );

  /* --- the Cooking Log: one tap opens a date picker, then one write ------ */

  /**
   * The revision of `date`'s daily note, or `null` if this view has never read
   * or written that date. `null` is not a weakened contract — it is the honest
   * answer to "I have not seen this note", and the server treats an absent
   * `baseRevision` as "no compare-and-swap", which is exactly what it should
   * mean. A revision for a DIFFERENT date would be the opposite: a compare
   * against bytes this request is not about to replace.
   */
  function revisionFor(date) {
    return date ? noteRevisions.get(date) || null : null;
  }

  /** Record a revision for the date it belongs to, and only if there is one. */
  function rememberRevision(date, revision) {
    if (date && revision) noteRevisions.set(date, revision);
  }

  function openPicker() {
    logError.textContent = '';
    dateInput.value = todayIn(appTimezone());
    picker.removeAttribute('hidden');
  }
  listen(logButton, 'click', () => {
    if (logButton.disabled) return;
    openPicker();
  });
  listen(cancel, 'click', () => picker.setAttribute('hidden', ''));

  function renderLogFailure(error) {
    logError.textContent = '';
    if (error.status === 404) {
      // F4: a missing daily note is a NAMED 404 the app never papers over by
      // creating a file. The server's sentence, the date, and the
      // vault-relative path it named — all verbatim — plus a retry of the
      // IDENTICAL request, so a created note is a one-step detour.
      const server = error.data || {};
      /* `daily_note_missing` publishes no `currentRevision` — F4's envelope for
       * that code is `{requestId, code, message, date, relativePath, retryable}`
       * and nothing else — so the retry must take its base from the DATE the
       * server named, and not from whatever date the view last read. For a note
       * that was missing, that is `null`: the retry replays the request F4 says
       * is retryable, and #25 verified the server honours it (201 once the note
       * exists). A note this view HAD read keeps its own revision, so a
       * delete-and-recreate still surfaces as a conflict instead of silently
       * overwriting whatever the user made in between. */
      const retryDate = server.date || dateInput.value;
      const again = el('button', { type: 'button', class: 'button', text: '再试一次' });
      again.addEventListener('click', () =>
        submit(retryDate, server.currentRevision || revisionFor(retryDate)),
      );
      logError.appendChild(
        errorState({
          title: error.message || '这一天没有日记。',
          detail: server.relativePath
            ? `日记：${server.relativePath}。在 Obsidian 里建好它，再点一次“再试一次”。`
            : '在 Obsidian 里建好这一天的日记，再点一次。',
          code: error.code,
          requestId: error.requestId,
          actions: [again],
          dataset: { role: 'log-missing-note' },
        }),
      );
      return;
    }
    logError.appendChild(
      errorState({
        title: '这次没有记上。',
        detail: error.message || OFFLINE_REASON,
        code: error.code,
        requestId: error.requestId,
        dataset: { role: 'log-failed' },
      }),
    );
  }

  async function submit(date, baseRevision) {
    if (inFlight) return;
    if (navigator.onLine === false) {
      renderLogFailure({ code: 'offline', message: OFFLINE_REASON, status: 0, data: null });
      return;
    }
    inFlight = true;
    logButton.disabled = true;
    confirm.disabled = true;
    logError.textContent = '';
    // A hung fetch re-enables both controls and says so, rather than freezing
    // the screen on a spinner the user cannot escape.
    const clearWatchdog = unstickOnTimeout([logButton, confirm], {
      delay: UNSTICK_DELAY_MS,
      onStall: () =>
        renderLogFailure({
          code: 'request_stalled',
          message: '请求一直没有回来，可以再试一次。',
          status: 0,
          data: null,
        }),
    });
    try {
      const result = await apiFetch('/api/cook-logs', {
        method: 'POST',
        body: { recipeNote: name, date, baseRevision: baseRevision || null },
      });
      picker.setAttribute('hidden', '');
      status.textContent =
        result.status === 'duplicate'
          ? `这一天已经记过 ${name} 了，日记没有再改。`
          : `已记到 ${result.relativePath}`;
      /* Keyed by the date this write was ABOUT, which is the point of the map:
       * the response's revision is `sha256` of that date's bytes and of no
       * other. A `duplicate` writes nothing and publishes no revision, and a
       * cached one is still correct for that date because nothing moved. */
      rememberRevision(date, result.noteRevision);
      /* The detail payload was read BEFORE this write, so it cannot know about
       * the cook just logged — and the badge's verdict is now that payload's
       * `pendingCookDates`. Without this the badge would be silent for exactly
       * the case it exists for.
       *
       * The inference is sound rather than optimistic: a `logged` AND a
       * `duplicate` both mean a receipt now exists for this date, and
       * `recipeTracker` can only have counted it by the note being opened in
       * Obsidian — which cannot have happened in the milliseconds since. The one
       * thing that would break it is a user opening the note in Obsidian while
       * this response was in flight, and in that case the badge is briefly
       * pessimistic until the next load. That is the right direction to be wrong
       * in: it names a remedy, and it does not claim a count it cannot see. */
      if (!pendingCookDates.includes(date)) {
        pendingCookDates = [...pendingCookDates, date].sort();
        // BOTH surfaces, from the same value. The badge and the history panel
        // report one fact; rendering one of them from a second source is the
        // "two implementations of one rule" shape that drifts.
        renderHistory();
        renderTrackerState();
      }
      refreshBadge(date);
    } catch (error) {
      if (error.status === 409) {
        // §8 story 34: both versions side by side, and the user chooses. The
        // server's message is rendered verbatim when F4's envelope carries one
        // (`daily_note_created_concurrently`); `daily_note_changed` publishes
        // only `currentRevision`, and `ApiError` has already fallen back to the
        // code as the message in that case.
        logError.textContent = '';
        picker.setAttribute('hidden', '');
        /* The server has just told us what this date's note holds NOW, so that
         * is the freshest base there is for this date — recorded, and used by
         * the re-offer below. Recording it costs nothing in strictness: it is
         * the server's own value for the note this request is about. */
        rememberRevision(date, (error.data && error.data.currentRevision) || null);
        const again = el('button', { type: 'button', class: 'button', text: '按服务器现状再记一次' });
        again.addEventListener('click', () => {
          logError.textContent = '';
          // §9.3 / F4: the follow-up write carries the FRESH revision, so it is
          // a compare-and-swap against the bytes the server just showed, not a
          // blind overwrite.
          submit(date, (error.data && error.data.currentRevision) || null);
        });
        logError.appendChild(
          el('div', { class: 'error-state', dataset: { panelState: 'conflict', role: 'conflict' } }, [
            el('p', { class: 'error-state__title', text: '日记在这台设备之外被改过了。' }),
            el('div', { class: 'fields' }, [
              field('PWA 想记的', `${name} @ ${date}`),
              field('服务器当前的版本', (error.data && error.data.currentRevision) || '—'),
              field('日记', (error.data && error.data.relativePath) || date),
            ]),
            error.message
              ? el('p', { class: 'muted', dataset: { role: 'server-message' }, text: error.message })
              : null,
            el('p', { class: 'muted', text: '两边都在这里了。这个 app 不会替你覆盖，也不会替你新建日记。' }),
            el('div', { class: 'settings-actions' }, [again]),
          ]),
        );
        return;
      }
      renderLogFailure(error);
    } finally {
      // The settling path MUST clear the watchdog, or the timer outlives the
      // request and re-enables a control the user is already looking at.
      clearWatchdog();
      inFlight = false;
      logButton.disabled = offlineReason.dataset.disabled === 'true';
      confirm.disabled = false;
    }
  }

  // The one place a Cooking Log write is composed from the picker's date, and
  // it asks the map about THAT date. Logging a second cook on another day is
  // the ordinary thing a user does, and before the map this sent the first
  // day's revision against the second day's note: a 409 naming a conflict that
  // never happened, and a cook silently lost.
  listen(confirm, 'click', () => {
    const date = dateInput.value || todayIn(appTimezone());
    submit(date, revisionFor(date));
  });

  /* --- retracting a Cooking Record: the inverse of the write above ------- */

  /** Re-renders the Cooking History from the values this mount holds. */
  function renderHistory() {
    historySlot.textContent = '';
    historySlot.appendChild(
      historyPanel(
        loadedRecipe.history || {},
        pendingCookDates,
        todayIn(appTimezone()),
        pendingRetractionDates,
      ),
    );
  }

  /** "9月29日 13:41" in the configured timezone, from the server's UTC deadline. */
  function deadlineLabel(iso) {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: appTimezone(),
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(at);
  }

  /** Plain words for each refusal; the server publishes the code, this owns the copy. */
  function retractFailureCopy(error) {
    switch (error.code) {
      case 'cook_record_not_removable':
        return '这条记录在 Obsidian 里被改过了（或那天有多处链接），所以这里不会替你删。请直接在那天的日记里处理。';
      case 'retraction_window_closed':
        return '已经超过可撤销的时间，这条记录现在算历史，请在 Obsidian 里改。';
      case 'cook_record_not_found':
        return '这个 app 没有记过这一条，所以不会动你的日记。';
      default:
        return error.message || error.code || '这次没有撤销成功。';
    }
  }

  function renderRetractable() {
    cooksSlot.textContent = '';
    const offline = navigator.onLine === false;
    for (const cook of retractableCooks) {
      const button = el('button', {
        type: 'button',
        class: 'button',
        dataset: { role: 'retract-button' },
        text: '撤销',
      });
      button.disabled = offline || inFlight;
      const actions = el('div', { class: 'settings-actions' }, [button]);
      // Two taps, because this one writes to the vault: the first only asks.
      button.addEventListener('click', () => {
        if (button.disabled) return;
        const sure = el('button', {
          type: 'button',
          class: 'button',
          dataset: { role: 'retract-confirm' },
          text: '确认撤销',
        });
        const back = el('button', {
          type: 'button',
          class: 'button',
          dataset: { role: 'retract-cancel' },
          text: '取消',
        });
        sure.addEventListener('click', () => retract(cook.date));
        back.addEventListener('click', renderRetractable);
        actions.textContent = '';
        actions.appendChild(sure);
        actions.appendChild(back);
      });
      const until = deadlineLabel(cook.retractableUntil);
      cooksSlot.appendChild(
        el('div', { dataset: { role: 'retract-row', date: cook.date } }, [
          el('p', {
            class: 'muted',
            text: until ? `${cook.date} 已记录 · 可撤销至 ${until}` : `${cook.date} 已记录`,
          }),
          actions,
        ]),
      );
    }
  }

  async function retract(date) {
    if (inFlight) return;
    if (navigator.onLine === false) {
      retractStatus.textContent = OFFLINE_REASON;
      return;
    }
    inFlight = true;
    logButton.disabled = true;
    retractStatus.textContent = '';
    try {
      // No `baseRevision`: the server re-derives the removal from the bytes it
      // re-reads under the lock and refuses anything but the exact line the app
      // wrote, so a revision would add a second, weaker check on top of that one.
      const result = await apiFetch(
        `/api/cook-logs/${encodeURIComponent(date)}/${encodeURIComponent(name)}`,
        { method: 'DELETE' },
      );
      retractableCooks = retractableCooks.filter((cook) => cook.date !== date);
      // The receipt is no longer active, so the cook is neither pending nor
      // retractable. The one thing the server would add on a reload is a
      // retraction the tracker still counts: exactly the date it named as
      // `last_cooked`, so that is all this mirrors.
      pendingCookDates = pendingCookDates.filter((d) => d !== date);
      if (
        (loadedRecipe.history || {}).lastCooked === date &&
        !pendingRetractionDates.includes(date)
      ) {
        pendingRetractionDates = [...pendingRetractionDates, date].sort();
      }
      retractStatus.textContent =
        result.status === 'already_retracted'
          ? `${date} 的记录之前已经撤销过了。`
          : `已撤销 ${date} 的记录。`;
      renderHistory();
      renderTrackerState();
    } catch (error) {
      retractStatus.textContent = retractFailureCopy(error);
    } finally {
      inFlight = false;
      logButton.disabled = offlineReason.dataset.disabled === 'true';
      renderRetractable();
    }
  }

  function applyConnectivity() {
    const offline = navigator.onLine === false;
    offlineReason.dataset.disabled = offline ? 'true' : 'false';
    offlineReason.textContent = offline
      ? OFFLINE_REASON
      : isReadOnly()
        ? '只读模式：这一台不会写入日记。'
        : '';
    // F5: disabled and it SAYS why, and nothing is queued for it — the outbox
    // this file does use is reached only through `writeIntent`, which has no
    // type for this write; §9.18.1 records why it must never have one.
    logButton.disabled = offline || inFlight;
    if (offline) status.textContent = '';
    renderRetractable();
  }
  listen(window, 'online', applyConnectivity);
  listen(window, 'offline', applyConnectivity);

  /* --- the meal shortlists: one tap, no dialog (D3) --------------------- */

  const mealButtons = MEALS.map((meal) => {
    const button = el('button', {
      type: 'button',
      class: 'button',
      dataset: { role: 'meal-add', slot: meal.slot },
      text: `+ ${meal.label}`,
    });
    button.addEventListener('click', () => void addToShortlist(meal, button));
    mealSlot.appendChild(button);
    return button;
  });

  async function addToShortlist(meal, button) {
    if (button.disabled) return;
    button.disabled = true;
    // A hung fetch re-enables the control and says so, rather than freezing the
    // screen on a spinner the user cannot escape. The watchdog is armed before
    // the await and cleared in the `finally` below, so it covers the *queue*
    // case too: an intent parked in the outbox is not an in-flight request, and
    // a control left disabled against a queue the user cannot see would be a
    // control that never comes back.
    const clearWatchdog = unstickOnTimeout(button, {
      delay: UNSTICK_DELAY_MS,
      onStall: () => {
        button.textContent = `+ ${meal.label}（超时）`;
      },
    });
    try {
      // §9.18.1: a shortlist edit is one of the two mutations the offline
      // outbox is FOR. `writeIntent` writes through while online and queues
      // durably when the transport or the connection is gone; the intent carries
      // its own `clientId`, which the server binds to a fingerprint of the
      // mutation so the replay is answered from the first delivery's bytes
      // rather than applied a second time.
      const result = await writeIntent(SHORTLIST_ADD, { slot: meal.slot, recipeNote: name });
      if (result.queued) {
        // Queued, not done. The wording says so, because the alternative — a
        // success label on an edit the server has not seen — is the one thing
        // F4 made structural and this app must not reintroduce.
        button.textContent = `+ ${meal.label}（已排队）`;
        status.textContent = `离线：${meal.label}清单的改动已排队，联网后自动送出。`;
        return;
      }
      button.textContent = `已加入${meal.label}`;
      status.textContent = `已加入${meal.label}清单。`;
    } catch (error) {
      // The server's own error is shown rather than an invented one, and a
      // failure never claims the recipe was added — which matters for the
      // refusal F4 made structural: the app never invents state it does not
      // have. The body is `{recipeNote}` per §9.16 and D2, never a path.
      //
      // A 409 `client_id_reused` is reachable here: it means the app spent one
      // idempotency key on two different edits, which is a bug in the app, not
      // something the user did. It surfaces as the server's code rather than
      // being swallowed, and the intent is NOT retried — the vendored outbox has
      // parked it, and retrying a parked intent forever is the tight loop the
      // adapter's own comment warns about.
      button.textContent = `+ ${meal.label}`;
      status.textContent = `${meal.label}清单：${error.message || error.code}`;
    } finally {
      clearWatchdog();
      // F19's gate: the outbox's own counter is the producer now, so this view
      // no longer marks itself. What the counter reports is "the app has not yet
      // confirmed my change" — which covers the in-flight request AND an intent
      // sitting in the outbox, and the recipe view was only ever the former.
      button.disabled = false;
    }
  }

  /** The recipe body this mount last read successfully, kept so the staleness
   * panel can be re-rendered after a write without a second round trip. */
  let loadedRecipe = {};

  /**
   * Renders the badge host from `pendingCookDates`, and nothing else.
   *
   * Split out of `refreshBadge` so the post-write path can re-render the verdict
   * without re-issuing the daily-note read — the request is still there for the
   * **revision** the next write needs, and repeating it to update one boolean
   * would be a second fetch for a fact this view already holds.
   */
  function renderTrackerState() {
    const host = badgeSlot;
    host.textContent = '';
    if (pendingCookDates.length > 0 || pendingRetractionDates.length > 0) {
      host.appendChild(
        el('p', { class: 'pill', dataset: { role: 'tracker-badge' }, text: TRACKER_BADGE }),
      );
      host.appendChild(
        el('p', {
          class: 'muted',
          dataset: { role: 'tracker-note' },
          text: '上面这份 frontmatter 还没追上：在 Obsidian 里打开这份菜谱，recipeTracker 就会把次数写回去。',
        }),
      );
      return;
    }
    host.appendChild(el('p', { class: 'muted', dataset: { role: 'tracker-clear' } }));
  }

  /* --- the `待 Obsidian 同步` badge: a SECOND, independent request ------- */

  /**
   * `pending` is the detail payload's `pendingCookDates`, held in the mount.
   *
   * **The badge's verdict used to come from `entry.trackerSynced`, and that
   * column is inserted as 0 and never flipped** — the read-path comparison the
   * spec describes (§13 step 8) was never implemented, so the test was
   * permanently `!== true` and the badge sat on the screen forever for every
   * recipe ever logged here. The server's own `pendingCookDates` is that
   * comparison, done correctly, and it is already in the response this view
   * holds, so the verdict is free and the second request below is now needed
   * only for the daily-note **revision** that the cook-log write's
   * compare-and-swap uses. Removing the request instead would drop that revision
   * and silently weaken conflict detection, which is why the call stayed.
   */
  function refreshBadge(date) {
    const host = badgeSlot;
    host.textContent = '';
    panels.push(
      loadPanel({
        load: () => apiFetch(`/api/cook-logs?date=${encodeURIComponent(date)}`),
        onLoading: () => host.appendChild(loadingState('正在看今天的日记…')),
        onReady: (result) => {
          host.textContent = '';
          // The read-back is also where the fresh note revision comes from, so
          // the NEXT write TO THIS DATE is a compare-and-swap against bytes the
          // server has just shown rather than against nothing. The `date` is the
          // one this read asked for, so the revision is filed under it — a
          // revision read for 03-10 says nothing about 03-12, and filing it
          // under "whatever was last read" is the bug.
          rememberRevision(date, result && result.noteRevision);
          renderTrackerState();
        },
        onError: (error) => {
          // Its own panel precisely so that "today's daily note does not exist"
          // cannot blank the recipe the user is reading.
          host.textContent = '';
          host.appendChild(
            el('p', {
              class: 'muted',
              dataset: { role: 'tracker-unavailable' },
              text: `今天的日记读不到（${error.message || error.code}），所以看不出有没有待同步。`,
            }),
          );
        },
      }),
    );
  }

  /* --- the detail panels ------------------------------------------------ */

  if (!name) {
    head.appendChild(
      errorState({
        title: '这个地址里没有菜谱名。',
        detail: '',
        code: 'no_basename',
        requestId: null,
        dataset: { role: 'no-basename' },
      }),
    );
  } else {
    controller = new AbortController();
    const signal = controller.signal;
    panels.push(
      loadPanel({
        load: () => apiFetch(`${recipePath(name)}?${strictQuery(strict)}`, { signal }),
        onLoading: () => {
          head.textContent = '';
          head.appendChild(loadingState('正在读这道菜…'));
          chipsSlot.appendChild(loadingState('正在算食材…'));
          toolsSlot.appendChild(loadingState('正在读工具…'));
          stepsSlot.appendChild(loadingState('正在读做法…'));
          historySlot.appendChild(loadingState('正在读烹饪记录…'));
        },
        onReady: (body) => {
          const recipe = (body && body.recipe) || {};
          // One "today" for the whole render, so the history's staleness age and
          // the cook-log badge's date can never disagree about which day it is.
          const today = todayIn(appTimezone());
          const slots = scoredSlots(recipe.ingredients || [], strict);
          title.textContent = recipe.noteName || name;
          head.textContent = '';
          head.appendChild(
            el('p', {
              class: 'recipe-row__headline',
              dataset: { role: 'headline' },
              text: headline({ ingredients: slots, strict }),
            }),
          );
          head.appendChild(el('p', { class: 'muted', dataset: { role: 'note-path' }, text: recipe.notePath || '' }));
          const meta = metaPanel(recipe.source || [], recipe.durationMinutes ?? null);
          metaSlot.textContent = '';
          metaHead.hidden = meta === null;
          if (meta) metaSlot.appendChild(meta);
          chipsSlot.textContent = '';
          chipsSlot.appendChild(chipRow({ ingredients: slots, strict, debug }));
          toolsSlot.textContent = '';
          toolsSlot.appendChild(toolsPanel(recipe.tools || []));
          stepsSlot.textContent = '';
          stepsSlot.appendChild(
            el('div', {
              class: 'steps',
              dataset: { role: 'steps' },
              // The note body VERBATIM: the vault's own bytes, through
              // textContent, with `white-space: pre-wrap`. Never re-wrapped,
              // never re-ordered, never re-typed as a list this app invented.
              text: recipe.steps || '',
            }),
          );
          historySlot.textContent = '';
          loadedRecipe = recipe;
          pendingCookDates = Array.isArray(recipe.pendingCookDates) ? recipe.pendingCookDates : [];
          retractableCooks = Array.isArray(recipe.retractableCooks) ? recipe.retractableCooks : [];
          pendingRetractionDates = Array.isArray(recipe.pendingRetractionDates)
            ? recipe.pendingRetractionDates
            : [];
          historySlot.appendChild(
            historyPanel(recipe.history || {}, pendingCookDates, today, pendingRetractionDates),
          );
          renderRetractable();
          if (typeof resumeTop === 'number' && resumeTop > 0 && window.scrollY !== resumeTop) {
            window.scrollTo(0, resumeTop);
          }
          refreshBadge(today);
        },
        onError: (error) => {
          head.textContent = '';
          // A failed load must not leave a `配方信息` heading above nothing. The
          // error state replaces the panel body, and the heading belongs to it.
          metaHead.hidden = true;
          metaSlot.textContent = '';
          chipsSlot.textContent = '';
          toolsSlot.textContent = '';
          stepsSlot.textContent = '';
          historySlot.textContent = '';
          if (isSourceUnavailable(error)) {
            // F1 again, narrower here: the RECIPE INDEX is unaffected by an
            // unreadable Pantry.md, but §9.16's detail route fails closed as a
            // whole and deliberately publishes no partial recipe. Saying that
            // plainly is the difference between a fixable vault problem and an
            // empty screen.
            head.appendChild(sourceUnavailableState({ error }));
            head.appendChild(
              el('p', {
                class: 'muted',
                dataset: { role: 'recipe-unavailable-note' },
                text: '做法和烹饪记录这一次也读不到：这个响应把食材和正文放在一起，服务器不会只降级其中一半。',
              }),
            );
            return;
          }
          if (error.status === 404) {
            head.appendChild(
              errorState({
                title: error.message || '找不到这道菜。',
                detail: '它可能已经在 Obsidian 里被改名了。',
                code: error.code,
                requestId: error.requestId,
                dataset: { role: 'recipe-not-found' },
              }),
            );
            return;
          }
          head.appendChild(
            errorState({
              title: '读不到这道菜。',
              detail: error.message || '',
              code: error.code,
              requestId: error.requestId,
              dataset: { role: 'recipe-error' },
            }),
          );
        },
      }),
    );
  }

  // Synchronous, not deferred: the control's disabled state is part of the
  // first paint the user sees, and a `setTimeout(0)` would make it depend on a
  // macrotask landing first.
  applyConnectivity();

  return function unmount() {
    for (const panel of panels.splice(0)) panel.abort();
    if (controller) controller.abort();
    controller = null;
    detachScroll();
    for (const off of listeners.splice(0)) off();
    for (const button of mealButtons) button.disabled = false;
    logButton.disabled = false;
    confirm.disabled = false;
    root.textContent = '';
  };
}
