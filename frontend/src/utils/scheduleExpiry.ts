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

type SettledCard = {
  phase?: string;
  expires_at?: string;
  confirm_token?: string;
};

function settled(card: SettledCard): boolean {
  if (card.phase === "confirmed" || card.phase === "cancelled" || card.phase === "expired") return true;
  return isExpired(card);
}

export function mergeStreamScheduleCard<T extends SettledCard>(
  current: T | null | undefined,
  incoming: T | null | undefined,
): T | null | undefined {
  if (current != null && settled(current)) return current;
  if (incoming === undefined) return current;
  return incoming;
}

type SettledRow = {
  id?: number;
  scheduleCard?: SettledCard | null;
};

function sameClaim(left: SettledCard | null | undefined, right: SettledCard | null | undefined): boolean {
  return Boolean(left?.confirm_token) && left?.confirm_token === right?.confirm_token;
}

export function overlaySettledScheduleCards<T extends SettledRow>(before: T[], synced: T[]): T[] {
  return synced.map((row, index) => {
    const local =
      (row.id != null ? before.find((message) => message.id === row.id) : undefined) ??
      before.find((message) => sameClaim(message.scheduleCard, row.scheduleCard)) ??
      before[index];
    const scheduleCard = mergeStreamScheduleCard(local?.scheduleCard, row.scheduleCard);
    if (scheduleCard === row.scheduleCard) return row;
    return { ...row, scheduleCard };
  });
}
