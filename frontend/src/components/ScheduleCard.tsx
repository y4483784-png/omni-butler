import { useMemo, useRef, useState, type FormEvent } from "react";
import {
  CalendarConflictError,
  cancelCalendarEvent,
  confirmGoogleCalendarChange,
  createCalendarEvent,
  formatPointedUtterance,
  rsvpCalendarEvent,
  updateCalendarEvent,
  type ScheduleCandidate,
  type ScheduleCard as Card,
  type ScheduleSlot,
} from "../api/client";
import { formatFollowUtterance } from "../utils/followMail";
import { isExpired } from "../utils/scheduleExpiry";
import { guestsFromWire, participantsForSubmit, type Guest } from "../utils/guestWire";

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function toLocalInput(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fromLocalInput(value: string): string {
  if (!value) return "";
  return value.length === 16 ? `${value}:00` : value;
}

function wallMs(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) return null;
  return Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5])
  );
}

function fromWallMs(ms: number): string {
  const date = new Date(ms);
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

function durationMinutes(startAt: string, endAt: string): number {
  const start = wallMs(startAt);
  const end = wallMs(endAt);
  if (start == null || end == null || end <= start) return 60;
  return Math.round((end - start) / 60000);
}

function addMinutes(startAt: string, minutes: number): string {
  const start = wallMs(startAt);
  if (start == null) return "";
  const span = minutes > 0 ? minutes : 60;
  return fromWallMs(start + span * 60000);
}

function formatListWhen(item: { start_at: string; end_at: string; all_day?: boolean }): string {
  if (item.all_day) {
    const day = (item.start_at || "").slice(0, 10);
    return day ? `${day} 全天` : "全天";
  }
  return formatWhen(item.start_at, item.end_at);
}

function formatWhen(startAt: string, endAt: string): string {
  if (!startAt && !endAt) return "时间待补充";
  const start = startAt ? new Date(startAt) : null;
  const end = endAt ? new Date(endAt) : null;
  if (!start || Number.isNaN(start.getTime())) return startAt || "时间待补充";
  const day = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())} ${pad(start.getHours())}:${pad(start.getMinutes())}`;
  if (!end || Number.isNaN(end.getTime())) return day;
  return `${day} - ${pad(end.getHours())}:${pad(end.getMinutes())}`;
}

function rsvpLabel(status: string): string {
  if (status === "accepted") return "已接受";
  if (status === "declined") return "已拒绝";
  if (status === "tentative") return "已暂定";
  return "";
}

type EmailAddress = string & { readonly __brand: "EmailAddress" };
type PersonName = string & { readonly __brand: "PersonName" };

type DialogGuest = Extract<Guest, { kind: "named-invite" | "name-only" }>;

type GuestEditor =
  | { readonly open: false }
  | {
      readonly open: true;
      readonly name: string;
      readonly email: string;
      readonly error: GuestEditorError | null;
    };

type GuestEditorError = "name-required" | "name-has-at" | "email-invalid" | "duplicate";

type GuestRow = {
  readonly key: string;
  readonly primary: string;
  readonly email: string;
  readonly decision: "将邀请" | "不邀请";
};

function isPersonName(value: string): value is PersonName {
  return value.length > 0 && !value.includes("@");
}

function isDialogEmail(value: string): value is EmailAddress {
  if (/[\s<>、,，]/.test(value)) return false;
  const at = value.indexOf("@");
  return at > 0 && at === value.lastIndexOf("@") && at < value.length - 1;
}

function commitGuest(
  input: { readonly name: string; readonly email: string },
  existing: readonly Guest[],
): { ok: true; guest: DialogGuest } | { ok: false; error: GuestEditorError } {
  const name = input.name.trim();
  const email = input.email.trim();
  if (!isPersonName(name)) {
    return { ok: false, error: name.includes("@") ? "name-has-at" : "name-required" };
  }
  let guest: DialogGuest;
  if (email.length === 0) {
    guest = { kind: "name-only", name };
  } else if (isDialogEmail(email)) {
    guest = { kind: "named-invite", name, email };
  } else {
    return { ok: false, error: "email-invalid" };
  }
  if (existing.some((item) => sameGuest(item, guest))) return { ok: false, error: "duplicate" };
  return { ok: true, guest };
}

function sameGuest(a: Guest, b: Guest): boolean {
  if (a.kind === "name-only" || b.kind === "name-only") {
    return a.kind === "name-only" && b.kind === "name-only" && a.name === b.name;
  }
  return a.email.toLowerCase() === b.email.toLowerCase();
}

function appendGuest(guests: readonly Guest[], guest: Guest): readonly Guest[] {
  if (guests.some((item) => sameGuest(item, guest))) return guests;
  return [...guests, guest];
}

function removeGuest(guests: readonly Guest[], guest: Guest): readonly Guest[] {
  if (!guests.some((item) => sameGuest(item, guest))) return guests;
  return guests.filter((item) => !sameGuest(item, guest));
}

function guestRow(guest: Guest): GuestRow {
  switch (guest.kind) {
    case "named-invite":
      return {
        key: `email:${guest.email.toLowerCase()}`,
        primary: guest.name,
        email: guest.email,
        decision: "将邀请",
      };
    case "email-only":
      return {
        key: `email:${guest.email.toLowerCase()}`,
        primary: guest.email,
        email: "",
        decision: "将邀请",
      };
    case "name-only":
      return {
        key: `name:${guest.name}`,
        primary: guest.name,
        email: "",
        decision: "不邀请",
      };
    default: {
      const _exhaustive: never = guest;
      return _exhaustive;
    }
  }
}

const guestEditorErrorTextByKind = {
  "name-required": "请填写姓名",
  "name-has-at": "姓名不要写成邮箱",
  "email-invalid": "邮箱格式不对",
  duplicate: "已经添加过",
} satisfies Record<GuestEditorError, string>;

function guestEditorErrorText(error: GuestEditorError): string {
  return guestEditorErrorTextByKind[error];
}

function savedEventId(card: Card): number | null {
  const id = card.event_id ?? card.id;
  return typeof id === "number" ? id : null;
}

export function ScheduleCard({
  card,
  onChange,
  onAsk,
}: {
  card: Card;
  onChange: (next: Card) => void;
  onAsk?: (text: string) => void;
}) {
  const expired = isExpired(card);
  const legacy = !card.phase && !card.mode;
  const writable = card.phase === "draft" || legacy;
  const locked = !writable || expired || card.phase === "cancelled";
  const [title, setTitle] = useState(card.title || "");
  const [startAt, setStartAt] = useState(toLocalInput(card.start_at));
  const [endAt, setEndAt] = useState(toLocalInput(card.end_at));
  const [guests, setGuests] = useState<readonly Guest[]>(() => guestsFromWire(card.participants));
  const [editor, setEditor] = useState<GuestEditor>({ open: false });
  const guestDialogRef = useRef<HTMLDialogElement>(null);
  const [location, setLocation] = useState(card.location || "");
  const [description, setDescription] = useState(card.description || "");
  const [durationMin, setDurationMin] = useState(durationMinutes(toLocalInput(card.start_at), toLocalInput(card.end_at)));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [meetOn, setMeetOn] = useState(Boolean(card.meet) && !card.meet_link);

  const mode = card.mode || (legacy ? "update" : "create");
  const canEdit = !locked && mode !== "cancel";
  const blockForm = card.kind === "block" && (mode === "create" || mode === "update");
  const showMeet =
    !blockForm && !card.meet_link && (mode === "create" || mode === "update");
  const conflicts = card.conflicts || [];
  const slots = card.suggested_slots || [];
  const candidates = card.candidates || [];
  const items = card.items || [];

  const statusLabel = useMemo(() => {
    if (expired || card.phase === "expired") return "已过期";
    if (card.phase === "cancelled") return "未确认";
    if (card.status === "cancelled") return "已撤销";
    if (card.phase === "confirmed" || legacy) return card.status === "cancelled" ? "已撤销" : "已安排";
    if (mode === "cancel") return "待确认删除";
    if (mode === "choose") return "请选择";
    return "待确认";
  }, [card.phase, card.status, expired, legacy, mode]);

  function applySlot(slot: ScheduleSlot) {
    setStartAt(toLocalInput(slot.start_at));
    setEndAt(toLocalInput(slot.end_at));
    setError("");
  }

  function chooseCandidate(candidate: ScheduleCandidate) {
    if (!candidate || candidate.id == null) return;
    const nextMode = card.action === "cancel" ? "cancel" : "update";
    onChange({
      ...card,
      mode: nextMode,
      phase: "draft",
      event_id: candidate.id,
      title: candidate.title,
      start_at: candidate.start_at,
      end_at: candidate.end_at,
      candidates: [],
    });
    setTitle(candidate.title);
    setStartAt(toLocalInput(candidate.start_at));
    setEndAt(toLocalInput(candidate.end_at));
  }

  function openGuestEditor() {
    if (!canEdit || editor.open) return;
    const dialog = guestDialogRef.current;
    if (dialog == null || dialog.open) return;
    setEditor({ open: true, name: "", email: "", error: null });
    dialog.showModal();
  }

  function submitGuestEditor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canEdit || !editor.open) return;
    const committed = commitGuest({ name: editor.name, email: editor.email }, guests);
    if (!committed.ok) {
      setEditor({ open: true, name: editor.name, email: editor.email, error: committed.error });
      return;
    }
    setGuests((current) => appendGuest(current, committed.guest));
    setEditor({ open: false });
    guestDialogRef.current?.close();
  }

  async function confirm() {
    setError("");
    const payload = {
      title: title.trim(),
      start_at: fromLocalInput(startAt),
      end_at: fromLocalInput(endAt),
      participants: participantsForSubmit(blockForm, guests),
      location: blockForm ? "" : location.trim(),
      description: description.trim(),
      meet: showMeet && meetOn,
    };
    setBusy(true);
    try {
      if (mode === "create") {
        const saved = await createCalendarEvent(payload);
        onChange({ ...card, ...saved, mode: "create", phase: "confirmed", conflicts: [], suggested_slots: [], candidates: [] });
        return;
      }
      const id = savedEventId(card);
      const externalId = (card.external_id || "").trim();
      if ((mode === "cancel" || mode === "update") && externalId) {
        const saved = await confirmGoogleCalendarChange(
          mode === "cancel"
            ? {
                action: "cancel",
                title: payload.title,
                start_at: payload.start_at,
                end_at: payload.end_at,
                participants: [],
                external_id: externalId,
              }
            : {
                action: "update",
                title: payload.title,
                start_at: payload.start_at,
                end_at: payload.end_at,
                participants: payload.participants,
                location: payload.location,
                description: payload.description,
                external_id: externalId,
                meet: payload.meet,
              }
        );
        onChange({
          ...card,
          ...saved,
          mode,
          phase: "confirmed",
          conflicts: [],
          suggested_slots: [],
          candidates: [],
        });
        return;
      }
      if ((mode === "cancel" || mode === "update") && !externalId && id == null) {
        setError("还没有选中日程");
        return;
      }
      if (id == null) {
        setError("还没有选中日程");
        return;
      }
      if (mode === "cancel") {
        const saved = await cancelCalendarEvent(id);
        onChange({ ...card, ...saved, mode: "cancel", phase: "confirmed" });
        return;
      }
      const saved = await updateCalendarEvent(id, payload);
      onChange({ ...card, ...saved, mode: "update", phase: "confirmed", conflicts: [], suggested_slots: [] });
    } catch (err) {
      if (err instanceof CalendarConflictError) {
        onChange({ ...card, conflicts: err.conflicts, suggested_slots: err.suggestedSlots });
        setError("这个时间有冲突，可以改用卡片上的空闲时段。");
        return;
      }
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function reply(externalId: string, status: "accepted" | "declined" | "tentative") {
    setError("");
    setBusy(true);
    try {
      const saved = await rsvpCalendarEvent(externalId, status);
      const next = (card.items || []).map((item) =>
        item.external_id === externalId ? { ...item, rsvp: saved.rsvp || status } : item
      );
      onChange({ ...card, items: next });
    } catch (err) {
      setError(err instanceof Error ? err.message : "回复邀请失败");
    } finally {
      setBusy(false);
    }
  }

  function useSlot(slot: ScheduleSlot) {
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    onChange({
      mode: "create",
      phase: "draft",
      action: "create",
      title: "",
      start_at: slot.start_at,
      end_at: slot.end_at,
      kind: "meeting",
      participants: card.participants || [],
      location: "",
      description: "",
      status: "",
      expires_at: expiresAt,
      items: [],
      event_id: null,
      conflicts: [],
      suggested_slots: [],
      candidates: [],
    });
  }

  if (card.readonly) {
    const when = card.all_day
      ? `${(card.start_at || "").slice(0, 10)} 全天`.trim()
      : formatWhen(card.start_at, card.end_at);
    return (
      <div className="schedule-card is-locked">
        <div className="schedule-head">
          <strong>{card.title || "（无标题）"}</strong>
          <span className="schedule-status">来自 Google</span>
        </div>
        <div className="schedule-meta">{when}</div>
      </div>
    );
  }

  if (mode === "list") {
    return (
      <div className="schedule-card">
        {items.length ? (
          <ul className="schedule-list">
            {items.map((item, index) => (
              <li key={`${item.start_at}-${item.title}-${index}`}>
                <span>{formatListWhen(item)}</span>
                <span>{item.title || "（无标题）"}</span>
                {onAsk && item.title ? (
                  <>
                    <button type="button" className="ghost-btn" onClick={() => onAsk(formatPointedUtterance(item))}>
                      会前准备
                    </button>
                    <button type="button" className="ghost-btn" onClick={() => onAsk(formatFollowUtterance(item))}>
                      会后跟进
                    </button>
                  </>
                ) : null}
                {item.meet_link ? <span>{item.meet_link}</span> : null}
                {item.external_id && item.rsvp ? (
                  <span className="schedule-actions">
                    {rsvpLabel(item.rsvp) ? <span className="schedule-status">{rsvpLabel(item.rsvp)}</span> : null}
                    <button type="button" className="ghost-btn" disabled={busy} onClick={() => void reply(item.external_id || "", "accepted")}>
                      接受
                    </button>
                    <button type="button" className="ghost-btn" disabled={busy} onClick={() => void reply(item.external_id || "", "declined")}>
                      拒绝
                    </button>
                    <button type="button" className="ghost-btn" disabled={busy} onClick={() => void reply(item.external_id || "", "tentative")}>
                      暂定
                    </button>
                  </span>
                ) : null}
              </li>
            ))}
            {error ? <div className="schedule-note">{error}</div> : null}
          </ul>
        ) : null}
        {slots.length ? (
          <div className="schedule-slots">
            {slots.map((slot) => (
              <button
                key={`${slot.start_at}-${slot.end_at}`}
                type="button"
                className="ghost-btn"
                onClick={() => useSlot(slot)}
              >
                用这个时段 {formatWhen(slot.start_at, slot.end_at)}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  if (mode === "suggest") {
    return (
      <div className="schedule-card">
        <div className="schedule-head">
          <strong>空闲时段</strong>
          <span className="schedule-status">仅供选择</span>
        </div>
        <div className="schedule-slots">
          {slots.map((slot) => (
            <button
              key={`${slot.start_at}-${slot.end_at}`}
              type="button"
              className="ghost-btn"
              onClick={() => useSlot(slot)}
            >
              用这个时段 {formatWhen(slot.start_at, slot.end_at)}
            </button>
          ))}
        </div>
      </div>
    );
  }

  const writeModes = new Set(["create", "update", "cancel", "choose"]);
  if (!legacy && !writeModes.has(mode)) return null;

  const confirmLabel = mode === "cancel" ? "确认删除" : mode === "update" || legacy ? "确认修改" : "确认创建";
  const showForm = mode !== "choose";

  return (
    <div className={`schedule-card ${locked && card.phase !== "draft" ? "is-cancelled" : ""} ${locked ? "is-locked" : ""}`}>
      <div className="schedule-head">
        <strong>{title || card.title || "日程"}</strong>
        <span className="schedule-status">{statusLabel}</span>
      </div>
      {showForm ? (
        <>
        <form
          className="schedule-form"
          onSubmit={(event) => {
            event.preventDefault();
            void confirm();
          }}
        >
          <label>
            标题
            <input value={title} onChange={(event) => setTitle(event.target.value)} disabled={locked || mode === "cancel"} />
          </label>
          <label>
            开始
            <input
              type="datetime-local"
              value={startAt}
              onChange={(event) => {
                const next = event.target.value;
                setStartAt(next);
                if (blockForm) setEndAt(addMinutes(next, durationMin));
              }}
              disabled={locked || mode === "cancel"}
            />
          </label>
          {blockForm ? (
            <label>
              时长
              <input
                type="number"
                min={1}
                value={durationMin}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  const minutes = Number.isFinite(next) && next > 0 ? Math.round(next) : 60;
                  setDurationMin(minutes);
                  setEndAt(addMinutes(startAt, minutes));
                }}
                disabled={locked}
              />
            </label>
          ) : (
            <>
              <label>
                结束
                <input type="datetime-local" value={endAt} onChange={(event) => setEndAt(event.target.value)} disabled={locked || mode === "cancel"} />
              </label>
              <div className="schedule-guest-field">
                <span>参与人</span>
                <div className="schedule-guests">
                  {guests.map((guest) => {
                    const row = guestRow(guest);
                    return (
                      <div className="schedule-guest-row" key={row.key}>
                        <span>{row.primary}</span>
                        {row.email ? <span className="schedule-guest-email">{row.email}</span> : null}
                        <span className="schedule-guest-decision">{row.decision}</span>
                        <button
                          type="button"
                          className="ghost-btn"
                          disabled={!canEdit}
                          onClick={() => {
                            if (!canEdit) return;
                            setGuests((current) => removeGuest(current, guest));
                          }}
                        >
                          移除
                        </button>
                      </div>
                    );
                  })}
                  <button type="button" className="ghost-btn" disabled={!canEdit} onClick={openGuestEditor}>
                    添加
                  </button>
                </div>
              </div>
              {showMeet ? (
                <label className="schedule-meet">
                  <input type="checkbox" checked={meetOn} onChange={(event) => setMeetOn(event.target.checked)} disabled={locked} />
                  添加 Google Meet
                </label>
              ) : null}
              {card.meet_link ? <div className="schedule-note">{card.meet_link}</div> : null}
              {card.meet_status === "pending" ? <div className="schedule-note">会议链接还没准备好</div> : null}
              <label>
                地点
                <input value={location} onChange={(event) => setLocation(event.target.value)} disabled={locked || mode === "cancel"} />
              </label>
            </>
          )}
          <label>
            说明
            <input value={description} onChange={(event) => setDescription(event.target.value)} disabled={locked || mode === "cancel"} />
          </label>
          {conflicts.length ? (
            <div className="schedule-note">冲突：{conflicts.map((item) => `${item.title} ${formatWhen(item.start_at, item.end_at)}`).join("；")}</div>
          ) : null}
          {slots.length ? (
            <div className="schedule-slots">
              {slots.map((slot) => (
                <button key={`${slot.start_at}-${slot.end_at}`} type="button" className="ghost-btn" disabled={locked} onClick={() => applySlot(slot)}>
                  {formatWhen(slot.start_at, slot.end_at)}
                </button>
              ))}
            </div>
          ) : card.conflicts?.length ? (
            <div className="schedule-note">这段时间没有可建议的空隙。</div>
          ) : null}
          {error ? <div className="schedule-note">{error}</div> : null}
          <div className="schedule-actions">
            {mode === "cancel" ? (
              <button
                type="button"
                className="ghost-btn"
                disabled={locked || busy}
                onClick={() => onChange({ ...card, phase: "cancelled" })}
              >
                先不删
              </button>
            ) : null}
            <button type="submit" className="ghost-btn" disabled={locked || busy || mode === "choose"}>
              {confirmLabel}
            </button>
          </div>
        </form>
        <dialog
          ref={guestDialogRef}
          className="schedule-guest-dialog"
          onClose={() => setEditor({ open: false })}
        >
          <form onSubmit={submitGuestEditor}>
            <label>
              参与人（姓名）
              <input
                value={editor.open ? editor.name : ""}
                onChange={(event) => {
                  const name = event.target.value;
                  setEditor((current) => (current.open ? { ...current, name } : current));
                }}
              />
            </label>
            <label>
              邮箱
              <input
                value={editor.open ? editor.email : ""}
                onChange={(event) => {
                  const email = event.target.value;
                  setEditor((current) => (current.open ? { ...current, email } : current));
                }}
              />
            </label>
            {editor.open && editor.error ? <div className="schedule-note">{guestEditorErrorText(editor.error)}</div> : null}
            <div className="schedule-actions">
              <button type="button" className="ghost-btn" onClick={() => guestDialogRef.current?.close()}>
                取消
              </button>
              <button type="submit" className="ghost-btn">
                确定
              </button>
            </div>
          </form>
        </dialog>
        </>
      ) : (
        <div className="schedule-slots">
          {candidates.map((candidate) => (
            <button key={candidate.id} type="button" className="ghost-btn" disabled={locked} onClick={() => chooseCandidate(candidate)}>
              {candidate.title} {formatWhen(candidate.start_at, candidate.end_at)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
