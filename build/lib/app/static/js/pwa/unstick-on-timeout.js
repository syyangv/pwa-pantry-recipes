/**
 * Shared watchdog for any control disabled during an in-flight request.
 *
 * A hung fetch (flaky Tailscale, cold-starting backend) would otherwise
 * leave the control disabled forever — a silent freeze with no error to
 * surface. After a generous stall this re-enables the control(s) and calls
 * `onStall`; the returned `clear()` cancels the timer as soon as the
 * request settles.
 *
 * Dual-mode so both app architectures (Template Part 4f) share ONE
 * implementation — never a per-app copy:
 *
 *   ESM apps (wardrobe/deals) — import it:
 *     import { unstickOnTimeout } from './unstick-on-timeout.js?v=__APP_VERSION__';
 *     const clear = unstickOnTimeout(saveBtn, { delay: 15000, onStall: () => flash('请求超时') });
 *
 *   Classic-script apps (obsidian-daily) — load it as a module script, then
 *     forward through the global at call time (the module executes long
 *     before any user interaction):
 *     <script type="module" src="/js/pwa/unstick-on-timeout.js?v=__APP_VERSION__"></script>
 *     function unstickOnTimeout(c, o) { return globalThis.unstickOnTimeout(c, o); }
 *     saveBtn.disabled = true;
 *     try { await save(); } finally { clear(); }   // settling path MUST clear
 *
 * Contract: every site that sets `.disabled = true` for an in-flight
 * request must pair with this helper (or an equivalent) so a hung fetch
 * can never freeze a control.
 */
export function unstickOnTimeout(controls, options = {}) {
  const list = Array.isArray(controls) ? controls : [controls];
  const delay = Number.isFinite(options.delay) ? options.delay : 20000;
  const onStall = typeof options.onStall === 'function' ? options.onStall : () => {};
  const timer = window.setTimeout(() => {
    for (const control of list) control.disabled = false;
    onStall();
  }, delay);
  return () => window.clearTimeout(timer);
}

// Classic-script consumers get the same canonical implementation through
// the global instead of inlining a second copy.
if (typeof globalThis !== 'undefined') globalThis.unstickOnTimeout = unstickOnTimeout;
