import { useEffect, useRef, useState } from "react";
import { useStore } from "../store/useStore";
import {
  listSessions,
  createSession,
  deleteSession,
  renameSession,
  logout,
  type AuthUser,
} from "../api/client";
import { isSessionBusy } from "../services/chatStream";
import { UserAdminPanel } from "./UserAdminPanel";
import { ChangePasswordPanel } from "./ChangePasswordPanel";
import { AuditPanel } from "./AuditPanel";
import { groupSessions } from "../utils/sessionGroups";

type Props = {
  user: AuthUser;
  onLogout: () => void;
};

export function SessionSidebar({ user, onLogout }: Props) {
  const {
    sessions,
    activeId,
    sessionChats,
    setSessions,
    setActive,
    updateSessionTitle,
    clearSessionChat,
    setMemoryPanelOpen,
  } = useStore();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [adminOpen, setAdminOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId != null) inputRef.current?.focus();
  }, [editingId]);

  async function refresh() {
    setSessions(await listSessions());
  }

  async function onNew() {
    try {
      const s = await createSession();
      await refresh();
      setActive(s.id);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "新建会话失败");
    }
  }

  async function onDelete(id: number, e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm("确定删除该会话吗？删除后内容将无法恢复。")) return;
    try {
      await deleteSession(id);
      clearSessionChat(id);
      await refresh();
      if (activeId === id) setActive(null);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "删除失败");
    }
  }

  function startRename(id: number, title: string, e: React.MouseEvent) {
    e.stopPropagation();
    setEditingId(id);
    setDraft(title);
  }

  async function commitRename(id: number) {
    const title = draft.trim() || "新会话";
    setEditingId(null);
    try {
      const s = await renameSession(id, title);
      updateSessionTitle(id, s.title);
    } catch {
      window.alert("重命名失败");
      await refresh();
    }
  }

  async function handleLogout() {
    try {
      await logout();
    } finally {
      onLogout();
    }
  }

  const grouped = groupSessions(sessions);

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="brand">Omni-Butler</span>
        <button className="new-btn" onClick={onNew}>
          ＋ 新建
        </button>
      </div>
      <ul className="session-list">
        {grouped.map((group) => (
          <li key={group.label} className="session-group">
            <div className="session-group-label">{group.label}</div>
            <ul className="session-group-items">
              {group.sessions.map((s) => (
                <li
                  key={s.id}
                  className={s.id === activeId ? "active" : ""}
                  onClick={() => editingId !== s.id && setActive(s.id)}
                >
                  {editingId === s.id ? (
                    <input
                      ref={inputRef}
                      className="session-rename"
                      value={draft}
                      maxLength={40}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={() => commitRename(s.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          commitRename(s.id);
                        } else if (e.key === "Escape") {
                          setEditingId(null);
                        }
                      }}
                    />
                  ) : (
                    <span
                      className="session-title"
                      title="双击重命名"
                      onDoubleClick={(e) => startRename(s.id, s.title, e)}
                    >
                      {s.title}
                      {(sessionChats[s.id]?.busy || isSessionBusy(s.id)) ? (
                        <span className="session-generating" title="生成中">
                          {" "}
                          ·
                        </span>
                      ) : null}
                    </span>
                  )}
                  <button className="del-btn" onClick={(e) => onDelete(s.id, e)} title="删除">
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <div className="sidebar-foot">
        <span className="sidebar-user" title={user.username}>
          {user.name || user.username}
        </span>
        <button type="button" className="sidebar-link" onClick={() => setPasswordOpen(true)}>
          改密
        </button>
        {user.is_admin ? (
          <button type="button" className="sidebar-link" onClick={() => setAdminOpen(true)}>
            用户
          </button>
        ) : null}
        {user.is_admin ? (
          <button type="button" className="sidebar-link" onClick={() => setAuditOpen(true)}>
            审计
          </button>
        ) : null}
        <button type="button" className="sidebar-link" onClick={() => setMemoryPanelOpen(true)}>
          记忆
        </button>
        <button type="button" className="sidebar-link" onClick={handleLogout}>
          退出
        </button>
      </div>
      {adminOpen ? <UserAdminPanel onClose={() => setAdminOpen(false)} /> : null}
      {passwordOpen ? <ChangePasswordPanel onClose={() => setPasswordOpen(false)} /> : null}
      {auditOpen ? <AuditPanel onClose={() => setAuditOpen(false)} /> : null}
    </aside>
  );
}
