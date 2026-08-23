import { useEffect, useState } from "react";
import { listToolAudits, type ToolAuditRow } from "../api/client";

type Props = {
  onClose: () => void;
};

export function AuditPanel({ onClose }: Props) {
  const [rows, setRows] = useState<ToolAuditRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    listToolAudits(80)
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "加载失败"));
  }, []);

  return (
    <div className="admin-overlay" onClick={onClose}>
      <div className="admin-card audit-card" onClick={(e) => e.stopPropagation()}>
        <h2>工具审计</h2>
        <p className="auth-sub">谁在何时调用了搜索 / 沙箱 / 日程。容器重建后仍保留在数据库。</p>
        {error ? <p className="auth-error">{error}</p> : null}
        <div className="audit-table-wrap">
          <table className="audit-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>用户</th>
                <th>工具</th>
                <th>结果</th>
                <th>请求号</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.created_at.replace("T", " ").slice(0, 19)}</td>
                  <td>{row.user_id ?? "—"}</td>
                  <td>{row.tool || "—"}</td>
                  <td>{row.decision}</td>
                  <td className="audit-rid" title={row.request_id}>
                    {row.request_id.slice(0, 8) || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && !error ? <p className="auth-sub">暂无记录</p> : null}
        </div>
        <div className="admin-actions">
          <button type="button" className="admin-cancel" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
