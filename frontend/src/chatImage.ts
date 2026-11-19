export const CHAT_MEDIA_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"] as const;

export type ChatMediaType = (typeof CHAT_MEDIA_TYPES)[number];

export interface ChatImagePayload {
  media_type: ChatMediaType;
  data_base64: string;
}

export interface LocalImagePreview {
  mediaType: ChatMediaType;
  url: string;
}

export const INLINE_IMAGE_CAP_BYTES = 32 * 1024 * 1024;

export function isChatMediaType(value: string): value is ChatMediaType {
  return (
    value === "image/jpeg" ||
    value === "image/png" ||
    value === "image/gif" ||
    value === "image/webp"
  );
}

export function sniffImage(bytes: Uint8Array): ChatMediaType | null {
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xd8) return "image/jpeg";
  if (
    bytes.length >= 4 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47
  ) {
    return "image/png";
  }
  if (bytes.length >= 6) {
    const head = String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5]);
    if (head === "GIF87a" || head === "GIF89a") return "image/gif";
  }
  if (bytes.length >= 12) {
    const riff = String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]);
    const webp = String.fromCharCode(bytes[8], bytes[9], bytes[10], bytes[11]);
    if (riff === "RIFF" && webp === "WEBP") return "image/webp";
  }
  return null;
}

export function bytesToBase64(bytes: Uint8Array): string {
  const parts: string[] = [];
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    const slice = bytes.subarray(index, Math.min(bytes.length, index + chunk));
    let binary = "";
    for (let cursor = 0; cursor < slice.length; cursor += 1) {
      binary += String.fromCharCode(slice[cursor]);
    }
    parts.push(binary);
  }
  return btoa(parts.join(""));
}

export function imageFromFile(file: File, bytes: Uint8Array): ChatImagePayload | null {
  if (bytes.byteLength > INLINE_IMAGE_CAP_BYTES) return null;
  const sniffed = sniffImage(bytes);
  if (sniffed === null) return null;
  const declared = file.type === "image/jpg" ? "image/jpeg" : file.type;
  if (declared !== "" && declared !== sniffed) return null;
  return { media_type: sniffed, data_base64: bytesToBase64(bytes) };
}
