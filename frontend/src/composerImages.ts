export const IMAGE_READ_ERROR =
  "只接受 JPEG、PNG、GIF 或 WebP。超过 32MiB 的文件不会读取。服务器默认上限是 20MB，更大的图片会被拒绝并留在输入框。";

export function serverDraftError(detail: string): string {
  if (detail === "size") {
    return "服务器拒绝了这张图片，因为它超过上传上限（默认 20MB）。文字和图片仍留在输入框。";
  }
  if (detail === "body_limit") {
    return "这次请求超过 48MiB，没有发给模型。文字和图片仍留在输入框。";
  }
  if (detail === "signature" || detail === "media_type" || detail === "base64") {
    return "服务器拒绝了这张图片。文字和图片仍留在输入框。";
  }
  return "发送没有被接受。文字和图片仍留在输入框。";
}

export function takeCompletedBatch<T extends { url: string }>(
  pending: readonly T[],
  ticket: number,
  currentTicket: number,
  accepted: readonly T[],
  hadRejection: boolean,
): { pending: T[]; releaseUrls: string[]; showError: boolean } {
  if (ticket !== currentTicket) {
    return {
      pending: [...pending],
      releaseUrls: accepted.map((item) => item.url),
      showError: false,
    };
  }
  return {
    pending: [...pending, ...accepted],
    releaseUrls: [],
    showError: hadRejection,
  };
}

export function removeDraftImage<T extends { id: string; url: string }>(
  pending: readonly T[],
  id: string,
): { pending: T[]; releaseUrl: string | null } {
  const item = pending.find((entry) => entry.id === id);
  if (!item) return { pending: [...pending], releaseUrl: null };
  return {
    pending: pending.filter((entry) => entry.id !== id),
    releaseUrl: item.url,
  };
}

export function draftAfterReject<T extends { url: string }>(
  epoch: number,
  sentEpoch: number,
  snapshot: readonly T[],
): { restore: T[] | null; releaseUrls: string[] } {
  if (epoch === sentEpoch) return { restore: [...snapshot], releaseUrls: [] };
  return { restore: null, releaseUrls: snapshot.map((item) => item.url) };
}

export function urlsReleasedWhenSent(
  pendingUrls: readonly string[],
  shownUrls: readonly string[],
): string[] {
  const shown = new Set(shownUrls);
  return pendingUrls.filter((url) => !shown.has(url));
}

export function keepSentPreviews<T extends { id?: number; role: string; content: string; localImages?: readonly { url: string }[] }>(
  before: readonly T[],
  synced: readonly T[],
): T[] {
  const optimistic = before.filter(
    (row) => row.id == null && row.role === "user" && Boolean(row.localImages && row.localImages.length > 0),
  );
  if (optimistic.length === 0) return [...synced];
  const next = synced.map((row) => ({ ...row }));
  const used = new Set<number>();
  for (const sent of optimistic) {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      if (used.has(index)) continue;
      const row = next[index];
      if (!row || row.role !== "user" || row.content !== sent.content) continue;
      if (row.localImages && row.localImages.length > 0) continue;
      next[index] = { ...row, localImages: sent.localImages };
      used.add(index);
      break;
    }
  }
  return next;
}
