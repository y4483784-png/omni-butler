export interface AuthUser {
  id: number;
  username: string;
  name: string;
  is_admin: boolean;
}

export interface SessionSummary {
  id: number;
  title: string;
  updated_at: string;
}

export interface Citation {
  index: number;
  filename: string;
  snippet: string;
  title?: string;
  url?: string;
  source_type?: "kb" | "web" | string;
}

export interface ChatMsg {
  id?: number;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  scheduleCard?: ScheduleCard | null;
  artifact?: ArtifactPayload | null;
  feedback?: "up" | "down" | null;
}

export interface ScheduleCard {
  id: number;
  title: string;
  start_at: string;
  end_at: string;
  participants: string[];
  status: "active" | "cancelled" | string;
}

export interface KbDocument {
  id: number;
  filename: string;
  status: "pending" | "processing" | "ready" | "failed" | string;
  stage?: string;
  error: string;
  chunk_count: number;
  char_count?: number;
  warning?: string;
  parser_version?: number;
  created_at: string;
}

export type StreamHandlers = {
  signal?: AbortSignal;
  onTitle?: (title: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onIntent?: (
    intent: "chat" | "rag" | "web_search" | "calendar" | "data_analysis" | string,
    forced: boolean
  ) => void;
  onAck?: (path: "fast" | "agent") => void;
  onStatus?: (phase: string) => void;
  onTtft?: (ms: number, path: string) => void;
  onThinking?: (steps: string[]) => void;
  onScheduleCard?: (card: ScheduleCard) => void;
  onArtifact?: (artifact: ArtifactPayload) => void;
};

export interface ArtifactPayload {
  kind?: "code" | "image" | "document" | string;
  title?: string;
  language?: string;
  content?: string;
  image_base64?: string;
  svg?: string;
  chart_points?: { label: string; series?: string; value: number; x?: number }[];
}

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(fn: () => void) {
  unauthorizedHandler = fn;
}

async function parseError(r: Response, fallback: string): Promise<string> {
  try {
    const data = await r.json();
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map(String).join("; ");
    if (typeof data?.msg === "string") return data.msg;
  } catch {
    /* ignore */
  }
  const text = await r.text().catch(() => "");
  return text || fallback;
}

function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
}

async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (!headers.has("X-Request-ID")) {
    headers.set("X-Request-ID", newRequestId());
  }
  const r = await fetch(input, { credentials: "include", ...init, headers });
  const path = typeof input === "string" ? input : input.toString();
  if (r.status === 401 && !path.includes("/api/auth/login")) {
    unauthorizedHandler?.();
  }
  return r;
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const r = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Request-ID": newRequestId() },
    credentials: "include",
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) throw new Error(await parseError(r, "登录失败"));
  return r.json();
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
}

export async function fetchMe(): Promise<AuthUser | null> {
  const r = await apiFetch("/api/auth/me");
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(await parseError(r, "无法获取用户信息"));
  return r.json();
}

export async function createUser(
  username: string,
  password: string,
  name?: string
): Promise<AuthUser> {
  const r = await apiFetch("/api/auth/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, name: name || "" }),
  });
  if (!r.ok) throw new Error(await parseError(r, "创建用户失败"));
  return r.json();
}

export async function changePassword(password: string, newPassword: string): Promise<void> {
  const r = await apiFetch("/api/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password, new_password: newPassword }),
  });
  if (!r.ok) throw new Error(await parseError(r, "修改密码失败"));
}

export interface ToolAuditRow {
  id: number;
  request_id: string;
  user_id: number | null;
  tool: string;
  decision: string;
  risk: string;
  reason: string;
  engine: string;
  ok: boolean | null;
  evidence_count: number;
  elapsed_ms: number;
  created_at: string;
}

export async function listToolAudits(limit = 50): Promise<ToolAuditRow[]> {
  const r = await apiFetch(`/api/audit/tools?limit=${limit}`);
  if (!r.ok) throw new Error(await parseError(r, "加载审计失败"));
  return r.json();
}

export async function* streamChat(
  sessionId: number,
  message: string,
  handlers?: StreamHandlers & {
    useKb?: boolean;
    documentIds?: number[];
    regenerateMessageId?: number;
  }
): AsyncGenerator<string> {
  const body: Record<string, unknown> = {
    session_id: sessionId,
    message,
    use_kb: Boolean(handlers?.useKb),
    document_ids: handlers?.documentIds?.length ? handlers.documentIds : [],
  };
  if (handlers?.regenerateMessageId != null) {
    body.regenerate_message_id = handlers.regenerateMessageId;
  }
  const resp = await apiFetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: handlers?.signal,
  });
  if (!resp.ok) throw new Error(await parseError(resp, "生成失败"));
  if (!resp.body) return;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const json = line.slice(5).trim();
      if (!json) continue;
      try {
        const evt = JSON.parse(json);
        if (evt.type === "token") yield evt.content as string;
        else if (evt.type === "session_title") handlers?.onTitle?.(evt.content as string);
        else if (evt.type === "citations") handlers?.onCitations?.(evt.citations || []);
        else if (evt.type === "intent")
          handlers?.onIntent?.(evt.intent || "chat", Boolean(evt.forced));
        else if (evt.type === "ack")
          handlers?.onAck?.((evt.path as "fast" | "agent") || "agent");
        else if (evt.type === "status")
          handlers?.onStatus?.(String(evt.phase || ""));
        else if (evt.type === "ttft")
          handlers?.onTtft?.(Number(evt.ms) || 0, String(evt.path || ""));
        else if (evt.type === "thinking")
          handlers?.onThinking?.(Array.isArray(evt.steps) ? evt.steps : []);
        else if (evt.type === "schedule_card") handlers?.onScheduleCard?.(evt.card);
        else if (evt.type === "artifact") handlers?.onArtifact?.(evt.artifact);
        else if (evt.type === "error") throw new Error(evt.content || "模型调用失败");
      } catch (e) {
        if (e instanceof Error) throw e;
      }
    }
  }
}

export async function listSessions(): Promise<SessionSummary[]> {
  const r = await apiFetch("/api/sessions");
  if (!r.ok) throw new Error(await parseError(r, "加载会话失败"));
  return r.json();
}

export async function createSession(): Promise<SessionSummary> {
  const r = await apiFetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!r.ok) throw new Error(await parseError(r, "创建会话失败"));
  return r.json();
}

export async function deleteSession(id: number): Promise<void> {
  const r = await apiFetch(`/api/sessions/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error("删除会话失败");
}

export async function renameSession(id: number, title: string): Promise<SessionSummary> {
  const r = await apiFetch(`/api/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!r.ok) throw new Error("重命名失败");
  return r.json();
}

export async function loadMessages(sessionId: number): Promise<ChatMsg[]> {
  const r = await apiFetch(`/api/sessions/${sessionId}/messages`);
  if (!r.ok) throw new Error("加载消息失败");
  return r.json();
}

export async function setMessageFeedback(
  messageId: number,
  rating: "up" | "down" | null
): Promise<void> {
  const r = await apiFetch(`/api/messages/${messageId}/feedback`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });
  if (!r.ok) {
    throw new Error(await parseError(r, "反馈提交失败"));
  }
}

export async function listDocuments(): Promise<KbDocument[]> {
  const r = await apiFetch("/api/kb/documents");
  if (!r.ok) throw new Error("加载知识库失败");
  return r.json();
}

export async function uploadDocuments(files: FileList | File[]): Promise<KbDocument[]> {
  const fd = new FormData();
  Array.from(files).forEach((f) => fd.append("files", f));
  const r = await apiFetch("/api/kb/documents", { method: "POST", body: fd });
  if (!r.ok) {
    throw new Error(await parseError(r, "上传失败"));
  }
  return r.json();
}

export async function deleteDocument(id: number): Promise<void> {
  const r = await apiFetch(`/api/kb/documents/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error("删除文档失败");
}

export async function reingestDocument(id: number): Promise<KbDocument> {
  const r = await apiFetch(`/api/kb/documents/${id}/reingest`, { method: "POST" });
  if (!r.ok) {
    throw new Error(await parseError(r, "重新解析失败"));
  }
  return r.json();
}

export async function cancelCalendarEvent(id: number): Promise<ScheduleCard> {
  const r = await apiFetch(`/api/calendar/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error("撤销日程失败");
  return r.json();
}

export async function updateCalendarEvent(
  id: number,
  payload: Partial<Pick<ScheduleCard, "title" | "start_at" | "end_at" | "participants">>
): Promise<ScheduleCard> {
  const r = await apiFetch(`/api/calendar/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error("修改日程失败");
  return r.json();
}

export type MemoryKind = "identity" | "preference" | "entity" | string;

export interface MemoryItem {
  id: number;
  kind: MemoryKind;
  key: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export async function listMemories(): Promise<MemoryItem[]> {
  const r = await apiFetch("/api/memory");
  if (!r.ok) throw new Error(await parseError(r, "加载记忆失败"));
  return r.json();
}

export async function createMemory(payload: {
  kind: MemoryKind;
  key: string;
  content: string;
}): Promise<MemoryItem> {
  const r = await apiFetch("/api/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await parseError(r, "添加记忆失败"));
  return r.json();
}

export async function updateMemory(
  id: number,
  payload: Partial<Pick<MemoryItem, "kind" | "key" | "content">>
): Promise<MemoryItem> {
  const r = await apiFetch(`/api/memory/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await parseError(r, "更新记忆失败"));
  return r.json();
}

export async function deleteMemory(id: number): Promise<void> {
  const r = await apiFetch(`/api/memory/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await parseError(r, "删除记忆失败"));
}
