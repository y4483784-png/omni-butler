import { FormEvent, useState } from "react";
import { changePassword } from "../api/client";

type Props = {
  onClose: () => void;
};

export function ChangePasswordPanel({ onClose }: Props) {
  const [password, setPassword] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setMessage("");
    if (next !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    try {
      await changePassword(password, next);
      setMessage("密码已更新");
      setPassword("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "修改失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-overlay" onClick={onClose}>
      <form className="admin-card" onClick={(e) => e.stopPropagation()} onSubmit={onSubmit}>
        <h2>修改密码</h2>
        <label className="auth-label">
          当前密码
          <input
            className="auth-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <label className="auth-label">
          新密码（至少 6 位）
          <input
            className="auth-input"
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            required
            minLength={6}
          />
        </label>
        <label className="auth-label">
          确认新密码
          <input
            className="auth-input"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
            minLength={6}
          />
        </label>
        {error ? <p className="auth-error">{error}</p> : null}
        {message ? <p className="admin-ok">{message}</p> : null}
        <div className="admin-actions">
          <button type="button" className="admin-cancel" onClick={onClose}>
            关闭
          </button>
          <button type="submit" className="auth-submit" disabled={busy}>
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </div>
  );
}
