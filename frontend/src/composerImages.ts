import type { ChatMediaType } from "./chatImage";

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

export type PreviewImage = { url: string };

export type PreviewMessage = {
  id?: number;
  role: string;
  content: string;
  localImages?: readonly PreviewImage[];
};

export function mergeHydratedPreviews<T extends PreviewMessage>(
  before: readonly T[],
  synced: readonly T[],
): { messages: T[]; releaseUrls: string[] } {
  const priorIds = new Set(before.flatMap((row) => (row.id == null ? [] : [row.id])));
  const carried = new Map<number, readonly PreviewImage[]>();
  for (const row of before) {
    if (row.id == null || !row.localImages || row.localImages.length === 0) continue;
    carried.set(row.id, row.localImages);
  }
  const optimistic = before.filter(
    (row) => row.id == null && row.role === "user" && Boolean(row.localImages && row.localImages.length > 0),
  );
  const fresh: number[] = [];
  synced.forEach((row, index) => {
    if (row.role !== "user" || row.id == null || priorIds.has(row.id)) return;
    fresh.push(index);
  });
  const next = synced.map((row) => ({ ...row }));
  for (let index = 0; index < next.length; index += 1) {
    const row = next[index];
    if (!row || row.id == null) continue;
    const images = carried.get(row.id);
    if (!images) continue;
    next[index] = { ...row, localImages: images };
  }
  let cursor = 0;
  for (const sent of optimistic) {
    while (cursor < fresh.length) {
      const index = fresh[cursor];
      cursor += 1;
      const row = next[index];
      if (!row || (row.localImages && row.localImages.length > 0)) continue;
      next[index] = { ...row, localImages: sent.localImages };
      break;
    }
  }
  const shown = new Set<string>();
  for (const row of next) {
    for (const image of row.localImages ?? []) shown.add(image.url);
  }
  const releaseUrls: string[] = [];
  for (const row of before) {
    for (const image of row.localImages ?? []) {
      if (!shown.has(image.url)) releaseUrls.push(image.url);
    }
  }
  return { messages: next, releaseUrls };
}

export function keepSentPreviews<T extends PreviewMessage>(before: readonly T[], synced: readonly T[]): T[] {
  return mergeHydratedPreviews(before, synced).messages;
}
export type ComposerImage = {
  id: string;
  url: string;
  mediaType: ChatMediaType;
  dataBase64: string;
};

export type Composer = {
  text: string;
  pending: ComposerImage[];
  ticket: number;
  epoch: number;
  textStamp: number;
  imageStamp: number;
};

export type SendClaim = {
  text: string;
  snapshot: ComposerImage[];
  localImages: { mediaType: ChatMediaType; url: string }[];
  images: { media_type: ChatMediaType; data_base64: string }[];
  sentEpoch: number;
  sentTextStamp: number;
  sentImageStamp: number;
};

export function createComposer(): Composer {
  return { text: "", pending: [], ticket: 0, epoch: 0, textStamp: 0, imageStamp: 0 };
}

export function setComposerText(composer: Composer, text: string): Composer {
  if (text === composer.text) return composer;
  return { ...composer, text, textStamp: composer.textStamp + 1 };
}

export function switchComposerSession(composer: Composer): { composer: Composer; releaseUrls: string[] } {
  return {
    composer: {
      ...composer,
      pending: [],
      ticket: composer.ticket + 1,
      epoch: composer.epoch + 1,
      imageStamp: composer.imageStamp + 1,
    },
    releaseUrls: composer.pending.map((item) => item.url),
  };
}

export function removeComposerImage(
  composer: Composer,
  id: string,
): { composer: Composer; releaseUrl: string | null } {
  const item = composer.pending.find((entry) => entry.id === id);
  if (!item) return { composer, releaseUrl: null };
  return {
    composer: {
      ...composer,
      pending: composer.pending.filter((entry) => entry.id !== id),
      imageStamp: composer.imageStamp + 1,
    },
    releaseUrl: item.url,
  };
}

export function completeComposerRead(
  composer: Composer,
  ticket: number,
  accepted: readonly ComposerImage[],
  hadRejection: boolean,
): { composer: Composer; releaseUrls: string[]; showError: boolean; applied: boolean } {
  if (ticket !== composer.ticket) {
    return {
      composer,
      releaseUrls: accepted.map((item) => item.url),
      showError: false,
      applied: false,
    };
  }
  if (accepted.length === 0) {
    return { composer, releaseUrls: [], showError: hadRejection, applied: true };
  }
  return {
    composer: {
      ...composer,
      pending: [...composer.pending, ...accepted],
      imageStamp: composer.imageStamp + 1,
    },
    releaseUrls: [],
    showError: hadRejection,
    applied: true,
  };
}

export function beginSend(
  composer: Composer,
  busy: boolean,
): { composer: Composer; claim: SendClaim; releaseUrls: string[] } | null {
  const text = composer.text.trim();
  if (busy || (!text && composer.pending.length === 0)) return null;
  const snapshot = composer.pending;
  const localImages = snapshot.map((item) => ({ mediaType: item.mediaType, url: item.url }));
  const next: Composer = {
    ...composer,
    text: "",
    pending: [],
    ticket: composer.ticket + 1,
    textStamp: composer.textStamp + 1,
    imageStamp: composer.imageStamp + 1,
  };
  return {
    composer: next,
    releaseUrls: [],
    claim: {
      text,
      snapshot,
      localImages,
      images: snapshot.map((item) => ({ media_type: item.mediaType, data_base64: item.dataBase64 })),
      sentEpoch: composer.epoch,
      sentTextStamp: next.textStamp,
      sentImageStamp: next.imageStamp,
    },
  };
}

export function rejectSend(
  composer: Composer,
  claim: SendClaim,
  detail: string,
): { composer: Composer; releaseUrls: string[]; error: string } {
  if (composer.epoch !== claim.sentEpoch) {
    return {
      composer,
      releaseUrls: claim.snapshot.map((item) => item.url),
      error: "",
    };
  }
  let next = composer;
  const releaseUrls: string[] = [];
  if (composer.textStamp === claim.sentTextStamp) {
    next = { ...next, text: claim.text, textStamp: next.textStamp + 1 };
  }
  if (composer.imageStamp === claim.sentImageStamp) {
    next = { ...next, pending: [...claim.snapshot], imageStamp: next.imageStamp + 1 };
  } else {
    const kept = new Set(next.pending.map((item) => item.url));
    for (const item of claim.snapshot) {
      if (!kept.has(item.url)) releaseUrls.push(item.url);
    }
  }
  return { composer: next, releaseUrls, error: serverDraftError(detail) };
}

export function previewUrls(messages: readonly PreviewMessage[]): string[] {
  const urls: string[] = [];
  for (const row of messages) {
    for (const image of row.localImages ?? []) urls.push(image.url);
  }
  return urls;
}

export function releasePreviewUrls(urls: readonly string[], revoke: (url: string) => void): void {
  for (const url of urls) revoke(url);
}
