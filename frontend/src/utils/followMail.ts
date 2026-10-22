import type { FollowMailDraft } from "../api/client";

export function formatFollowUtterance(event: { title: string; start_at: string; all_day?: boolean }): string {
  const start = event.start_at || "";
  const day = start.slice(0, 10);
  if (event.all_day || start.length < 16) return `帮我整理这场会的纪要：${event.title}，${day} 全天`;
  return `帮我整理这场会的纪要：${event.title}，${day} ${start.slice(11, 16)} 开始`;
}

export function mailtoHref(mail: FollowMailDraft): string {
  const to = mail.to
    .map((person) => person.email.trim())
    .filter((email) => email.includes("@"))
    .join(",");
  const query = new URLSearchParams();
  query.set("subject", mail.subject);
  query.set("body", mail.body);
  return `mailto:${to}?${query.toString().replace(/\+/g, "%20")}`;
}

export function claimFollowRows(
  ids: readonly string[],
  done: ReadonlySet<string>,
  inflight: { current: boolean },
): string[] {
  if (inflight.current) return [];
  const pending = ids.filter((id) => !done.has(id));
  if (!pending.length) return [];
  inflight.current = true;
  return pending;
}
