/**
 * Polling without overlap: the next call is scheduled only after the previous one finished.
 * Slows down while the tab is hidden and refreshes at once when it becomes visible again.
 */
export function poll(fn, ms) {
  let timer = null;
  let stopped = false;
  let running = false;

  async function tick() {
    if (stopped || running) return;
    running = true;
    try { await fn(); } catch { /* fn reports its own errors */ }
    running = false;
    if (!stopped) timer = setTimeout(tick, document.hidden ? ms * 5 : ms);
  }

  function onVisible() {
    if (document.hidden) return;
    clearTimeout(timer);
    tick();
  }

  document.addEventListener("visibilitychange", onVisible);
  tick();
  return {
    stop() {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    },
    /** Refresh now (after an action or a persona switch). */
    now() {
      clearTimeout(timer);
      return tick();
    },
  };
}
