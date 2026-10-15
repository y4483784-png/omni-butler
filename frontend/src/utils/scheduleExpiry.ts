/** Confirm-card expiry. Offset-less stamps are UTC wall time from older backends. */

const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;

export function parseExpiresAt(value: string): Date {
  const stamp = value.trim();
  if (!stamp) return new Date(NaN);
  // `new Date("2026-10-15T06:42:00")` is browser-local. A UTC container wrote that
  // clock, so a UTC+8 page read a fresh 30-minute draft as ~7.5h in the past.
  return new Date(HAS_OFFSET.test(stamp) ? stamp : `${stamp}Z`);
}

export function isExpired(
  card: { phase?: string; expires_at?: string },
  now = Date.now(),
): boolean {
  if (card.phase !== "draft" || !card.expires_at) return false;
  const expires = parseExpiresAt(card.expires_at).getTime();
  return !Number.isNaN(expires) && expires <= now;
}
