export type UnsavedRow = {
  id?: number;
  role: string;
  content: string;
  reasoning?: string | null;
  fold?: "hidden" | "streaming" | "collapsed" | "opened";
  citations?: unknown;
  scheduleCard?: unknown;
  artifact?: unknown;
};

export function overlayUnsavedError<T extends UnsavedRow>(before: T[], synced: T[]): T[] {
  const beforeLast = before.at(-1);
  const syncedHasAssistantError = synced.some(
    (message) =>
      message.role === "assistant" &&
      (message.content.includes("模型调用失败") || message.content.startsWith("⚠️"))
  );
  if (
    beforeLast?.role !== "assistant" ||
    !String(beforeLast.content || "").startsWith("⚠️") ||
    syncedHasAssistantError
  ) {
    return synced;
  }
  const merged = [...synced];
  const last = merged.at(-1);
  const lastIsOlderTurn =
    last?.id != null && last.id !== beforeLast.id && before.some((message) => message.id === last.id);
  if (last?.role !== "assistant" || lastIsOlderTurn) {
    const { id: _ignored, ...rest } = beforeLast;
    const unsaved = {
      ...rest,
      citations: [],
      scheduleCard: null,
      artifact: null,
    } satisfies UnsavedRow;
    merged.push(unsaved as T);
    return merged;
  }
  merged[merged.length - 1] = { ...last, content: beforeLast.content };
  return merged;
}
