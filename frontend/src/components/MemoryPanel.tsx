import { FormEvent, useEffect, useState } from "react";
import {
  createMemory,
  deleteMemory,
  listMemories,
  updateMemory,
  type MemoryItem,
  type MemoryKind,
} from "../api/client";
import { useStore } from "../store/useStore";

const KIND_OPTIONS: { value: MemoryKind; label: string }[] = [
  { value: "identity", label: "身份" },
  { value: "preference", label: "偏好" },
  { value: "entity", label: "实体" },
];

function kindLabel(kind: string) {
  return KIND_OPTIONS.find((k) => k.value === kind)?.label ?? kind;
}

export function MemoryPanel() {
  const { memoryPanelOpen, setMemoryPanelOpen } = useStore();
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState<MemoryKind>("preference");
  const [key, setKey] = useState("");
  const [content, setContent] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");

  async function refresh() {
    setItems(await listMemories());
  }

  useEffect(() => {
    if (!memoryPanelOpen) return;
    refresh().catch(() => setErr("加载记忆失败"));
  }, [memoryPanelOpen]);

  if (!memoryPanelOpen) return null;

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    if (!key.trim() || !content.trim()) {
      setErr("请填写键名和内容");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await createMemory({ kind, key: key.trim(), content: content.trim() });
      setKey("");
      setContent("");
      await refresh();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "添加失败");
    } finally {
      setBusy(false);
    }
  }

  async function onSaveEdit(id: number) {
    const next = editDraft.trim();
    if (!next) {
      setErr("内容不能为空");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await updateMemory(id, { content: next });
      setEditingId(null);
      await refresh();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "更新失败");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: number) {
    if (!window.confirm("确定删除这条记忆？删除后新会话将不再使用它。")) return;
    setErr("");
    try {
      await deleteMemory(id);
      if (editingId === id) setEditingId(null);
      await refresh();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "删除失败");
    }
  }

  return (
    <aside className="kb-panel memory-panel" aria-label="记忆管理">
      <div className="kb-head">
        <span className="kb-title">记忆管理</span>
        <button type="button" className="ghost-btn" onClick={() => setMemoryPanelOpen(false)}>
          ×
        </button>
      </div>
      <p className="kb-hint">
        仅保存跨会话仍成立的身份、习惯和重要实体。普通问答、总结文档、一次性任务不会自动记入；也可在此手动增改。
      </p>
      <form className="memory-form" onSubmit={onAdd}>
        <select
          className="memory-select"
          value={kind}
          onChange={(e) => setKind(e.target.value as MemoryKind)}
          aria-label="记忆类型"
        >
          {KIND_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <input
          className="memory-input"
          placeholder="键名，如 name / style"
          value={key}
          maxLength={64}
          onChange={(e) => setKey(e.target.value)}
        />
        <textarea
          className="memory-textarea"
          placeholder="一句中文事实，如：用户希望被称为「小陈」"
          value={content}
          maxLength={240}
          onChange={(e) => setContent(e.target.value)}
        />
        <button type="submit" className="new-btn" disabled={busy}>
          {busy ? "保存中…" : "添加记忆"}
        </button>
      </form>
      {err ? <div className="kb-error">{err}</div> : null}
      <ul className="kb-list">
        {items.length === 0 ? <li className="kb-empty">暂无记忆条目</li> : null}
        {items.map((item) => (
          <li key={item.id} className="kb-item memory-item">
            <div className="kb-item-main">
              <span className="kb-name">
                {kindLabel(item.kind)}
                <span className="memory-key">/{item.key}</span>
              </span>
              {editingId === item.id ? (
                <>
                  <textarea
                    className="memory-textarea"
                    value={editDraft}
                    maxLength={240}
                    onChange={(e) => setEditDraft(e.target.value)}
                  />
                  <div className="memory-edit-actions">
                    <button
                      type="button"
                      className="ghost-btn"
                      disabled={busy}
                      onClick={() => void onSaveEdit(item.id)}
                    >
                      保存
                    </button>
                    <button type="button" className="ghost-btn" onClick={() => setEditingId(null)}>
                      取消
                    </button>
                  </div>
                </>
              ) : (
                <span className="memory-content">{item.content}</span>
              )}
            </div>
            {editingId === item.id ? null : (
              <div className="kb-item-actions">
                <button
                  type="button"
                  className="kb-reparse"
                  onClick={() => {
                    setEditingId(item.id);
                    setEditDraft(item.content);
                    setErr("");
                  }}
                >
                  编辑
                </button>
                <button type="button" className="del-btn" onClick={() => void onDelete(item.id)} title="删除">
                  ×
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </aside>
  );
}
