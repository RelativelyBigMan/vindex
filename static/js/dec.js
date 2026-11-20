/**
 * Exact decimal arithmetic for amounts (up to 6 decimals), using BigInt micro-units.
 * JS floats can't hold money exactly; the server re-validates with Python's Decimal anyway.
 */
const SCALE = 1000000n;
const PATTERN = /^\d{1,12}(\.\d{1,6})?$/;

/** "850.5" -> 850500000n, or null when it isn't a plain non-negative amount. */
export function toMicro(text) {
  const value = String(text ?? "").trim();
  if (!PATTERN.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * SCALE + BigInt(fraction.padEnd(6, "0"));
}

/** 850500000n -> "850.5" */
export function fromMicro(micro) {
  const whole = micro / SCALE;
  const fraction = (micro % SCALE).toString().padStart(6, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : `${whole}`;
}

/** Share of `part` in `total` as a whole percentage 0..100 (for bar widths only). */
export function percent(part, total) {
  const a = toMicro(part), b = toMicro(total);
  if (a === null || b === null || b === 0n) return 0;
  return Number((a * 100n) / b);
}
