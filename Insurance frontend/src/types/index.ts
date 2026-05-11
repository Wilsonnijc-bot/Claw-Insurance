export interface Client {
  id: string;
  name: string;
  avatar?: string;
  status: 'online' | 'offline';
  lastMessage: string;
  lastMessageTime: string;
  autoDraftEnabled: boolean;
  tags: string[];
  label?: string;
  pushName?: string;
  sessionFile?: string;
  sessionReadableDir?: string;
  sessionMetaFile?: string;
  sessionHistoryFile?: string;
  sessionReadableFile?: string;
  /** Number of messages in the session */
  messageCount?: number;
  /** ISO datetime when the session was first created */
  createdAt?: string;
  /** ISO datetime when the session was last updated */
  updatedAt?: string;
  /** Best-effort display name from session meta */
  clientDisplayName?: string;
  /** Phone from session meta (may differ from id in edge cases) */
  clientPhone?: string;
  /** WhatsApp chat ID from session meta */
  clientChatId?: string;
}

export interface Message {
  id: string;
  clientId: string;
  sender: 'client' | 'ai' | 'agent';
  content: string;
  timestamp: string;
  isAIDraft?: boolean;
}

export interface VoiceMemo {
  id: string;
  clientId: string;
  noteName: string;
  createdAt: string;
}

export interface VoiceMemoDetail extends VoiceMemo {
  transcript: string;
}

export interface LoadingState {
  isGenerating: boolean;
  currentPhase: 0 | 1 | 2 | 3;
  progressText: string;
  subText: string;
}

export interface AppState {
  selectedClientId: string | null;
  recordingState: 'idle' | 'recording' | 'processing';
  broadcastMode: boolean;
  aiLoading: LoadingState | null;
}

export const thinkingStates = [
  { text: "思考中", subtext: "分析客户需求" },
  { text: "感受中", subtext: "理解情感语境" },
  { text: "运算中", subtext: "匹配最佳方案" },
  { text: "消息邮递中", subtext: "准备发送建议" }
] as const;
