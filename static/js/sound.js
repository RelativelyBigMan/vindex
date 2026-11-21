/**
 * Button click sounds, synthesised with the Web Audio API (no audio files).
 *
 * Designed to be calm rather than stimulating:
 *  - short (< 60 ms), quiet (peak gain 0.06), soft attack, no reverb or tails
 *  - the same sound for the same action every time (no randomness)
 *  - at most one sound per 60 ms, so rapid clicking never stacks into noise
 *  - only on clicks (never hover, scroll or focus)
 * A toggle in the header mutes it. The choice lives in localStorage on this
 * device only and is never sent anywhere.
 */

const STORAGE_KEY = "vindex:sound";
const MIN_GAP_MS = 60;

/** Each voice: a sine "body" with a quick pitch drop, plus an optional quiet second partial. */
const VOICES = {
  tap:     { from: 880, to: 520, dur: 0.045, gain: 0.05 },
  primary: { from: 660, to: 392, dur: 0.06,  gain: 0.06, partial: 1.5 },
  danger:  { from: 330, to: 220, dur: 0.07,  gain: 0.06 },
  on:      { from: 520, to: 780, dur: 0.05,  gain: 0.05 },
  off:     { from: 780, to: 520, dur: 0.05,  gain: 0.04 },
};

let ctx = null;
let enabled = true;
let lastPlayed = 0;

function readPref() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v !== null) enabled = v === "1";
  } catch { /* storage blocked: keep default */ }
}

function writePref() {
  try { localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0"); } catch { /* ignore */ }
}

/** Created lazily inside a click handler, which satisfies browser autoplay rules. */
function audio() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

function tone(ac, { from, to, dur, gain }, t0) {
  const osc = ac.createOscillator();
  const amp = ac.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(from, t0);
  osc.frequency.exponentialRampToValueAtTime(to, t0 + dur);
  amp.gain.setValueAtTime(0.0001, t0);
  amp.gain.exponentialRampToValueAtTime(gain, t0 + 0.006);   // soft 6 ms attack: no hard "click" transient
  amp.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(amp).connect(ac.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

/** @param {keyof typeof VOICES} name */
export function play(name = "tap", { force = false } = {}) {
  if (!enabled && !force) return;
  const now = performance.now();
  if (now - lastPlayed < MIN_GAP_MS) return;
  lastPlayed = now;
  const ac = audio();
  if (!ac) return;
  const v = VOICES[name] || VOICES.tap;
  const t0 = ac.currentTime + 0.005;
  tone(ac, v, t0);
  if (v.partial) tone(ac, { ...v, from: v.from * v.partial, to: v.to * v.partial, gain: v.gain * 0.3 }, t0);
}

export function isEnabled() { return enabled; }

export function setEnabled(on) {
  enabled = Boolean(on);
  writePref();
  document.querySelectorAll("[data-sound-toggle]").forEach((b) => b.setAttribute("aria-pressed", String(enabled)));
}

function voiceFor(el) {
  if (el.dataset.sound) return el.dataset.sound;
  if (el.classList.contains("btn--danger")) return "danger";
  if (el.classList.contains("btn--primary")) return "primary";
  return "tap";
}

export function initSound() {
  readPref();
  setEnabled(enabled);

  document.addEventListener("click", (e) => {
    const target = e.target instanceof Element ? e.target : null;
    if (!target) return;

    const toggle = target.closest("[data-sound-toggle]");
    if (toggle) {
      setEnabled(!enabled);
      play(enabled ? "on" : "off", { force: true });   // confirm the new state audibly once
      return;
    }

    const el = target.closest("button, .btn, [data-sound]");
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return;
    play(voiceFor(el));
  });
}
