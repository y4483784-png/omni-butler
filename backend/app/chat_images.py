from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

MEDIA_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
INLINE_CAP_BYTES = 32 * 1024 * 1024
BODY_CAP_BYTES = 48 * 1024 * 1024

_DATA_PREFIX = "data:"
_BASE64_MARK = ";base64,"


class ImageRejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class UnsupportedContent(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RequestBodyTooLarge(Exception):
    def __init__(self, size: int) -> None:
        self.size = size
        super().__init__(str(size))


@dataclass(frozen=True)
class ChatImage:
    media_type: str
    data: bytes


def sniff_media(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _upload_cap(max_upload_mb: int) -> int:
    return max(0, int(max_upload_mb)) * 1024 * 1024


def _base64_char_cap(max_bytes: int) -> int:
    return 4 * ((max_bytes + 2) // 3) + 8


def decode_chat_image(media_type: str, data_base64: str, *, max_upload_mb: int) -> ChatImage:
    if media_type not in MEDIA_TYPES:
        raise ImageRejected("media_type")
    if not isinstance(data_base64, str) or data_base64 == "":
        raise ImageRejected("base64")
    cap = min(_upload_cap(max_upload_mb), INLINE_CAP_BYTES)
    if len(data_base64) > _base64_char_cap(cap):
        raise ImageRejected("size")
    try:
        data = base64.b64decode(data_base64, validate=True)
    except Exception as exc:
        raise ImageRejected("base64") from exc
    sniffed = sniff_media(data)
    if sniffed is None or sniffed != media_type:
        raise ImageRejected("signature")
    if len(data) > cap:
        raise ImageRejected("size")
    return ChatImage(media_type=media_type, data=data)


def decode_chat_images(parts: Sequence[Any], *, max_upload_mb: int) -> tuple[ChatImage, ...]:
    images: list[ChatImage] = []
    for part in parts:
        if isinstance(part, Mapping):
            media_type = part.get("media_type")
            data_base64 = part.get("data_base64")
        else:
            media_type = getattr(part, "media_type", None)
            data_base64 = getattr(part, "data_base64", None)
        if not isinstance(media_type, str) or not isinstance(data_base64, str):
            raise ImageRejected("media_type")
        images.append(decode_chat_image(media_type, data_base64, max_upload_mb=max_upload_mb))
    return tuple(images)


def data_url(image: ChatImage) -> str:
    encoded = base64.b64encode(image.data).decode("ascii")
    return f"data:{image.media_type};base64,{encoded}"


def content_blocks(text: str, images: Sequence[ChatImage]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for image in images:
        blocks.append({"type": "image_url", "image_url": {"url": data_url(image)}})
    return blocks


def attach_user_images(messages: list[dict], images: Sequence[ChatImage]) -> list[dict]:
    if not images:
        return messages
    copied = [dict(message) for message in messages]
    for index in range(len(copied) - 1, -1, -1):
        message = copied[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            return copied
        if not isinstance(content, str):
            raise UnsupportedContent("user_content")
        message["content"] = content_blocks(content, images)
        return copied
    raise UnsupportedContent("no_user_message")


def _allowed_data_url(url: str) -> bool:
    for media_type in MEDIA_TYPES:
        prefix = f"{_DATA_PREFIX}{media_type}{_BASE64_MARK}"
        if not url.startswith(prefix):
            continue
        payload = url[len(prefix) :]
        if payload == "":
            return False
        try:
            data = base64.b64decode(payload, validate=True)
        except Exception:
            return False
        return sniff_media(data) == media_type
    return False


def wire_user_blocks(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    if not blocks:
        raise UnsupportedContent("empty_blocks")
    cleaned: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise UnsupportedContent("block")
        kind = block.get("type")
        if kind == "text":
            if set(block) != {"type", "text"} or not isinstance(block.get("text"), str):
                raise UnsupportedContent("text_block")
            cleaned.append({"type": "text", "text": block["text"]})
            continue
        if kind == "image_url":
            image = block.get("image_url")
            if set(block) != {"type", "image_url"} or not isinstance(image, dict):
                raise UnsupportedContent("image_block")
            if set(image) != {"url"} or not isinstance(image.get("url"), str):
                raise UnsupportedContent("image_block")
            if not _allowed_data_url(image["url"]):
                raise UnsupportedContent("image_url")
            cleaned.append({"type": "image_url", "image_url": {"url": image["url"]}})
            continue
        raise UnsupportedContent("block_type")
    return cleaned


def wire_messages(messages: list[dict] | None) -> list[dict[str, Any]]:
    wired: list[dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            wired.append({"role": "", "content": ""})
            continue
        role = message.get("role")
        role_text = role if isinstance(role, str) else ""
        content = message.get("content")
        if content is None or isinstance(content, str):
            wired.append({"role": role_text, "content": content or ""})
            continue
        if isinstance(content, list):
            if role_text != "user":
                raise UnsupportedContent("content_list")
            wired.append({"role": role_text, "content": wire_user_blocks(content)})
            continue
        raise UnsupportedContent("content")
    return wired


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""

