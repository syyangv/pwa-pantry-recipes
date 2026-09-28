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
import { goBack, readViewState, saveViewState } from '../router.js?v=__APP_VERSION__';
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

/** §13.5's badge text, driven by `cook_log_receipts.recipe_tracker_synced`. */
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

/** One `label: value` line, skipped when the server sent nothing. */
function field(label, value) {
  if (value === null || value === undefined || value === '') return null;
  const text = Array.isArray(value) ? value.join('、') : String(value);
  return el('div', { class: 'field' }, [
    el('span', { class: 'field__label', text: label }),
    el('span', { class: 'field__value', text }),
  ]);
}

function historyPanel(history = {}) {
  const rows = [
    field('第一次做', history.firstCooked),
    field('最近一次', history.lastCooked),
    field('做过次数', history.cookingCount),
    field('频率', history.cookingFrequency),
    field('年份', history.cookingYears),
    field('近期活跃', history.recentActivity),
    field('喜欢的季节', history.favoriteSeason),
    field('做法模式', history.cookingPatterns),
    field('frontmatter 自动更新于', history.autoUpdated),
  ].filter(Boolean);
  if (rows.length === 0) {
    return emptyState({
      title: '还没有做过这道菜。',
      body: '记一次之后，Obsidian 的 recipeTracker 会在打开这份笔记时把次数写回 frontmatter。',
      dataset: { role: 'empty-history' },
    });
  }
  return el('div', { class: 'fields', dataset: { role: 'history' } }, rows);
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
  let noteRevision = null;
  let inFlight = false;

  const name = typeof params.basename === 'string' ? params.basename : '';
  const strict = parseStrict(window.location.search);
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
  const detachScroll = trackScroll(() => saveViewState({ scrollTop: window.scrollY }));

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

  const logSlot = el('div', { dataset: { role: 'log-slot' } }, [
    el('div', { class: 'settings-actions' }, [logButton]),
    offlineReason,
    status,
    logError,
    picker,
  ]);

  root.appendChild(
    el('section', { class: 'panel', dataset: { view: 'recipe' } }, [
      backRow,
      title,
      head,
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
      const again = el('button', { type: 'button', class: 'button', text: '再试一次' });
      again.addEventListener('click', () =>
        submit(server.date || dateInput.value, server.currentRevision || noteRevision),
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
      noteRevision = result.noteRevision || null;
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

  listen(confirm, 'click', () => submit(dateInput.value || todayIn(appTimezone()), noteRevision));

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

  /* --- the `待 Obsidian 同步` badge: a SECOND, independent request ------- */

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
          // the NEXT write is a compare-and-swap against bytes the server has
          // just shown rather than against nothing.
          noteRevision = (result && result.noteRevision) || noteRevision;
          const entry = ((result && result.entries) || []).find((item) => item.recipeNote === name);
          if (entry && entry.trackerSynced !== true) {
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
          historySlot.appendChild(historyPanel(recipe.history || {}));
          if (typeof resumeTop === 'number' && resumeTop > 0 && window.scrollY !== resumeTop) {
            window.scrollTo(0, resumeTop);
          }
          refreshBadge(todayIn(appTimezone()));
        },
        onError: (error) => {
          head.textContent = '';
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
