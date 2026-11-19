import { useEffect, useState } from "react";
import { listDevices, revokeDevice, type DesktopDevice } from "../api/client";

type Props = {
  onClose: () => void;
};

export function DevicesPanel({ onClose }: Props) {
  const [rows, setRows] = useState<DesktopDevice[]>([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDevices()
      .then((items) => {
        if (!cancelled) setRows(items);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onRevoke(id: number) {
    setError("");
    setBusyId(id);
    try {
      await revokeDevice(id);
      setRows(await listDevices());
    } catch (err) {
      setError(err instanceof Error ? err.message : "撤销失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="admin-overlay" onClick={onClose}>
      <div className="admin-card" onClick={(e) => e.stopPropagation()}>
        <h2>设备</h2>
        {error ? <p className="auth-error">{error}</p> : null}
        <div className="audit-table-wrap">
          <table className="audit-table">
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.label}</td>
                  <td>{row.created_at.replace("T", " ").slice(0, 19)}</td>
                  <td>
                    <button
                      type="button"
                      className="sidebar-link"
                      disabled={busyId === row.id}
                      onClick={() => void onRevoke(row.id)}
                    >
                      撤销
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && !error ? <p className="auth-sub">还没有设备</p> : null}
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
