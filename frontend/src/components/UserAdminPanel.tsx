import { FormEvent, useState } from "react";
import { createUser } from "../api/client";

type Props = {
  onClose: () => void;
};

export function UserAdminPanel({ onClose }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setMessage("");
    setBusy(true);
    try {
      await createUser(username.trim(), password, name.trim() || undefined);
      setMessage(`已创建用户「${username.trim()}」`);
      setUsername("");
      setPassword("");
      setName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-overlay" onClick={onClose}>
      <form className="admin-card" onClick={(e) => e.stopPropagation()} onSubmit={onSubmit}>
        <h2>创建用户</h2>
        <label className="auth-label">
          用户名
          <input
            className="auth-input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={2}
          />
        </label>
        <label className="auth-label">
          初始密码
          <input
            className="auth-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
          />
        </label>
        <label className="auth-label">
          显示名称（可选）
          <input className="auth-input" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {error ? <p className="auth-error">{error}</p> : null}
        {message ? <p className="admin-ok">{message}</p> : null}
        <div className="admin-actions">
          <button type="button" className="admin-cancel" onClick={onClose}>
            关闭
          </button>
          <button type="submit" className="auth-submit" disabled={busy}>
            {busy ? "创建中…" : "创建"}
          </button>
        </div>
      </form>
    </div>
  );
}
