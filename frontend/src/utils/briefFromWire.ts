import type { BriefEmail, BriefFocus, MeetingBrief } from "../api/client";

export type BriefPerson = { email: string; name: string };

export type MailState = "found" | "empty" | "needs_consent" | "unavailable";

export type DocsState = MailState;

export type BriefDocView = {
  file_id: string;
  title: string;
  url: string;
  via: string;
  sentences: string[];
};

export type NormalizedBrief = {
  status: string;
  connected: boolean;
  said: string;
  meeting: {
    title: string;
    start_at: string;
    end_at: string;
    all_day: boolean;
    attendees: BriefPerson[];
  } | null;
  candidates: NonNullable<NormalizedBrief["meeting"]>[];
  mail: { state: MailState; emails: BriefEmail[]; focus: BriefFocus[] };
  docs: { state: DocsState; docs: BriefDocView[] };
};

function personFrom(value: unknown): BriefPerson | null {
  if (typeof value === "string") {
    const text = value.trim();
    if (!text) return null;
    if (text.includes("@")) return { email: text, name: "" };
    return { email: "", name: text };
  }
  if (!value || typeof value !== "object") return null;
  const record = value as { email?: unknown; name?: unknown };
  const email = String(record.email ?? "").trim();
  const name = String(record.name ?? "").trim();
  if (!email && !name) return null;
  return { email, name };
}

function meetingFrom(value: MeetingBrief["meeting"] | null | undefined): NormalizedBrief["meeting"] {
  if (!value) return null;
  const attendees = (value.attendees ?? [])
    .map((item) => personFrom(item))
    .filter((item): item is BriefPerson => item !== null);
  return {
    title: value.title || "",
    start_at: value.start_at || "",
    end_at: value.end_at || "",
    all_day: Boolean(value.all_day),
    attendees,
  };
}

function asMailState(value: unknown): MailState {
  if (value === "found" || value === "empty" || value === "needs_consent" || value === "unavailable") return value;
  return "empty";
}

export function briefFromWire(card: MeetingBrief): NormalizedBrief {
  const meeting = meetingFrom(card.meeting);
  const candidates = (card.candidates ?? [])
    .map((item) => meetingFrom(item))
    .filter((item): item is NonNullable<NormalizedBrief["meeting"]> => item !== null);
  if (card.mail || card.docs) {
    const mail = card.mail;
    const docs = card.docs;
    return {
      status: card.status,
      connected: Boolean(card.connected),
      said: card.said || "",
      meeting,
      candidates,
      mail: {
        state: asMailState(mail?.state),
        emails: mail?.emails ?? [],
        focus: mail?.focus ?? [],
      },
      docs: {
        state: asMailState(docs?.state),
        docs: (docs?.docs ?? []).map((item) => ({
          file_id: item.file_id,
          title: item.title,
          url: item.url,
          via: item.via,
          sentences: item.sentences ?? [],
        })),
      },
    };
  }
  const legacy = card.status;
  const mailState: MailState =
    legacy === "ready"
      ? "found"
      : legacy === "needs_consent" || legacy === "unavailable" || legacy === "empty"
        ? legacy
        : "empty";
  return {
    status: legacy === "choose" || legacy === "not_found" ? legacy : "ready",
    connected: Boolean(card.connected),
    said: card.said || "",
    meeting,
    candidates,
    mail: {
      state: mailState,
      emails: card.emails ?? [],
      focus: card.focus ?? [],
    },
    docs: { state: "empty", docs: [] },
  };
}

export function consentLabel(card: NormalizedBrief): string | null {
  const mail = card.mail.state === "needs_consent";
  const docs = card.docs.state === "needs_consent";
  if (!mail && !docs) return null;
  if (!card.connected) return "连接 Google 账户";
  if (mail && docs) return "授权读取 Gmail 和 Drive";
  if (docs) return "授权读取 Drive";
  return "授权读取 Gmail";
}

export function viaLabel(via: string): string {
  if (via === "attachment") return "日程附件";
  if (via === "title_and_people") return "标题和参会人";
  if (via === "title") return "标题";
  return "";
}
