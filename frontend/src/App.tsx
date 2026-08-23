import { useEffect, useState } from "react";
import { SessionSidebar } from "./components/SessionSidebar";
import { ChatWindow } from "./components/ChatWindow";
import { ArtifactsPanel } from "./components/ArtifactsPanel";
import { KnowledgeBasePanel } from "./components/KnowledgeBasePanel";
import { MemoryPanel } from "./components/MemoryPanel";
import { LoginPage } from "./components/LoginPage";
import { useStore } from "./store/useStore";
import { fetchMe, listSessions, setUnauthorizedHandler, type AuthUser } from "./api/client";
import { abortAllChatStreams } from "./services/chatStream";

export default function App() {
  const { activeId, artifactOpen, kbPanelOpen, memoryPanelOpen, setSessions, resetWorkspace } = useStore();
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      abortAllChatStreams();
      resetWorkspace();
      setUser(null);
    });
    fetchMe().then(setUser);
  }, [resetWorkspace]);

  useEffect(() => {
    if (!user) {
      abortAllChatStreams();
      resetWorkspace();
      return;
    }
    listSessions()
      .then((rows) => {
        setSessions(rows);
        const { activeId: current } = useStore.getState();
        if (current != null && !rows.some((s) => s.id === current)) {
          useStore.getState().setActive(null);
        }
      })
      .catch(() => {
        setSessions([]);
        useStore.getState().setActive(null);
      });
  }, [user?.id, setSessions, resetWorkspace]);

  if (user === undefined) {
    return <div className="auth-loading">加载中…</div>;
  }

  if (!user) {
    return (
      <LoginPage
        onLogin={(next) => {
          abortAllChatStreams();
          resetWorkspace();
          setUser(next);
        }}
      />
    );
  }

  return (
    <div
      className={`app${artifactOpen ? " with-artifact" : ""}${kbPanelOpen || memoryPanelOpen ? " with-kb" : ""}`}
    >
      <SessionSidebar
        user={user}
        onLogout={() => {
          abortAllChatStreams();
          resetWorkspace();
          setUser(null);
        }}
      />
      <main className="main">
        {activeId ? (
          <ChatWindow sessionId={activeId} />
        ) : (
          <div className="empty">从左侧选择或新建一个会话开始</div>
        )}
      </main>
      <KnowledgeBasePanel />
      <MemoryPanel />
      <ArtifactsPanel />
    </div>
  );
}
