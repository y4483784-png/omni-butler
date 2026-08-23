import { create } from "zustand";
import type { SessionSummary, ChatMsg } from "../api/client";

import type { ChartPoint } from "../utils/chartPoints";

export interface Artifact {
  id: string;
  title: string;
  language: string;
  content: string;
  kind?: "code" | "image" | "document";
  imageUrl?: string;
  svg?: string;
  chartPoints?: ChartPoint[];
}

export interface SessionChatState {
  messages: ChatMsg[];
  busy: boolean;
  thinking: boolean;
  thinkingSteps: string[];
  routeHint: string;
  phaseHint: string;
  /** true after loadMessages or first send in this tab session */
  hydrated: boolean;
}

interface StoreState {
  sessions: SessionSummary[];
  activeId: number | null;
  sessionChats: Record<number, SessionChatState>;
  artifact: Artifact | null;
  artifactOpen: boolean;
  useKb: boolean;
  kbPanelOpen: boolean;
  memoryPanelOpen: boolean;
  kbFocusIds: number[];
  setSessions: (s: SessionSummary[]) => void;
  setActive: (id: number | null) => void;
  updateSessionTitle: (id: number, title: string) => void;
  touchSession: (id: number) => void;
  patchSessionChat: (id: number, patch: Partial<SessionChatState>) => void;
  setSessionMessages: (id: number, messages: ChatMsg[]) => void;
  clearSessionChat: (id: number) => void;
  openArtifact: (a: Artifact) => void;
  closeArtifact: () => void;
  setUseKb: (v: boolean) => void;
  setKbPanelOpen: (v: boolean) => void;
  setMemoryPanelOpen: (v: boolean) => void;
  toggleKbFocus: (id: number) => void;
  clearKbFocus: () => void;
  resetWorkspace: () => void;
}

const emptyChat = (): SessionChatState => ({
  messages: [],
  busy: false,
  thinking: false,
  thinkingSteps: [],
  routeHint: "",
  phaseHint: "",
  hydrated: false,
});

export const useStore = create<StoreState>((set) => ({
  sessions: [],
  activeId: null,
  sessionChats: {},
  artifact: null,
  artifactOpen: false,
  useKb: false,
  kbPanelOpen: false,
  memoryPanelOpen: false,
  kbFocusIds: [],
  setSessions: (s) => set({ sessions: s }),
  setActive: (id) => set({ activeId: id, artifactOpen: false, artifact: null }),
  updateSessionTitle: (id, title) =>
    set((state) => ({
      sessions: state.sessions.map((s) => (s.id === id ? { ...s, title } : s)),
    })),
  touchSession: (id) =>
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === id ? { ...s, updated_at: new Date().toISOString() } : s
      ),
    })),
  patchSessionChat: (id, patch) =>
    set((state) => ({
      sessionChats: {
        ...state.sessionChats,
        [id]: { ...(state.sessionChats[id] ?? emptyChat()), ...patch },
      },
    })),
  setSessionMessages: (id, messages) =>
    set((state) => ({
      sessionChats: {
        ...state.sessionChats,
        [id]: { ...(state.sessionChats[id] ?? emptyChat()), messages, hydrated: true },
      },
    })),
  clearSessionChat: (id) =>
    set((state) => {
      const next = { ...state.sessionChats };
      delete next[id];
      return { sessionChats: next };
    }),
  openArtifact: (a) => set({ artifact: a, artifactOpen: true }),
  closeArtifact: () => set({ artifactOpen: false }),
  setUseKb: (v) => set({ useKb: v }),
  setKbPanelOpen: (v) =>
    set((state) => ({
      kbPanelOpen: v,
      memoryPanelOpen: v ? false : state.memoryPanelOpen,
    })),
  setMemoryPanelOpen: (v) =>
    set((state) => ({
      memoryPanelOpen: v,
      kbPanelOpen: v ? false : state.kbPanelOpen,
    })),
  toggleKbFocus: (id) =>
    set((state) => ({
      kbFocusIds: state.kbFocusIds.includes(id)
        ? state.kbFocusIds.filter((x) => x !== id)
        : [...state.kbFocusIds, id],
    })),
  clearKbFocus: () => set({ kbFocusIds: [] }),
  resetWorkspace: () =>
    set({
      sessions: [],
      activeId: null,
      sessionChats: {},
      artifact: null,
      artifactOpen: false,
      useKb: false,
      kbPanelOpen: false,
      memoryPanelOpen: false,
      kbFocusIds: [],
    }),
}));
