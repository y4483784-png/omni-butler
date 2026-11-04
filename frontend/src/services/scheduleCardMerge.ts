type ScheduleCardPhase = {
  phase?: string;
  expires_at?: string;
  confirm_token?: string;
};

export function mergeStreamScheduleCard<T extends ScheduleCardPhase>(
  current: T | null | undefined,
  incoming: T | null | undefined,
): T | null | undefined {
  if (incoming === undefined) return current;
  return incoming;
}

type ScheduleRow = {
  id?: number;
  scheduleCard?: ScheduleCardPhase | null;
};

export function overlaySettledScheduleCards<T extends ScheduleRow>(before: T[], synced: T[]): T[] {
  return synced.map((row) => row);
}
