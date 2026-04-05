/**
 * useNanobot — main React hook for connecting the UI to the Nanobot backend.
 *
 * Handles:
 * - Fetching clients and messages from the REST API
 * - Real-time WebSocket updates
 * - AI draft generation
 * - Auto-draft toggling
 * - Broadcast
 * - WhatsApp sync
 *
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchClients,
  fetchMessages,
  sendMessage as apiSend,
  requestAIDraft as apiAIDraft,
  sendAIDraft as apiSendDraft,
  toggleAutoDraft as apiToggleAutoDraft,
  sendBroadcast as apiBroadcast,
  triggerSync as apiSync,
  fetchStatus,
  addReplyTarget as apiAddReplyTarget,
  checkBridge as apiCheckBridge,
  restartBridge as apiRestartBridge,
  type ApiClient,
  type ApiMessage,
} from '../services/api';
import { nanobotWS, type WSEvent } from '../services/websocket';
import type { Client, Message } from '../types';

// ─── Map API types to frontend types ─────────────────────────────────

function apiClientToClient(ac: ApiClient): Client {
  return {
    id: ac.phone || ac.id,
    name: ac.name,
    avatar: undefined,
    status: ac.status,
    lastMessage: ac.lastMessage,
    lastMessageTime: ac.lastMessageTime,
    autoDraftEnabled: ac.autoDraftEnabled ?? false,
    tags: ac.tags,
    label: ac.label,
    pushName: ac.pushName,
    sessionFile: ac.sessionFile,
    sessionReadableDir: ac.sessionReadableDir,
    sessionMetaFile: ac.sessionMetaFile,
    sessionHistoryFile: ac.sessionHistoryFile,
    sessionReadableFile: ac.sessionReadableFile,
    messageCount: ac.messageCount,
    createdAt: ac.createdAt,
    updatedAt: ac.updatedAt,
    clientDisplayName: ac.clientDisplayName,
    clientPhone: ac.clientPhone,
    clientChatId: ac.clientChatId,
  };
}

function apiMessageToMessage(am: ApiMessage, clientId: string): Message {
  return {
    id: am.id || `${am.timestamp}_${Math.random()}`,
    clientId,
    sender: am.sender,
    content: am.content,
    timestamp: formatTimestamp(am.timestamp),
    isAIDraft: am.isAIDraft,
  };
}

function formatTimestamp(ts: string): string {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts.slice(0, 5);
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return ts.slice(0, 5);
  }
}

function gatewayProgressState(status: string, gatewayStarting = false): {
  phase: string;
  progress: number;
  message: string;
} {
  const normalized = gatewayStarting && status === 'launcher' ? 'starting_gateway' : status;
  switch (normalized) {
    case 'starting_gateway':
      return { phase: normalized, progress: 12, message: '正在启动网关...' };
    case 'starting_bridge':
      return { phase: normalized, progress: 28, message: '正在连接 WhatsApp Bridge...' };
    case 'initializing_core':
      return { phase: normalized, progress: 52, message: '正在初始化 Nanobot 核心服务...' };
    case 'starting_services':
      return { phase: normalized, progress: 76, message: '正在连接 WhatsApp 与后台服务...' };
    case 'loading_clients':
      return { phase: normalized, progress: 90, message: '正在读取客户列表与聊天内容...' };
    case 'ready':
    case 'running':
      return { phase: 'ready', progress: 100, message: '数据已同步完成' };
    case 'error':
      return { phase: normalized, progress: 100, message: '网关启动失败' };
    default:
      return { phase: 'launcher', progress: 8, message: '正在等待后台响应...' };
  }
}

// ─── Hook ────────────────────────────────────────────────────────────

export interface UseNanobotReturn {
  clients: Client[];
  messages: Record<string, Message[]>;
  backendConnected: boolean;
  loading: boolean;
  error: string | null;
  gatewayReady: boolean;
  gatewayPhase: string | null;
  gatewayProgress: number;
  gatewayMessage: string | null;
  whatsappBrowserMode: string | null;
  whatsappBrowserReusable: boolean | null;
  whatsappBrowserMessage: string | null;
  whatsappBrowserSeverity: 'warning' | 'error' | null;
  whatsappAuthRequired: boolean;
  whatsappAuthQr: string | null;
  whatsappAuthMessage: string | null;
  selectedClientPhone: string | null;
  /** Phone number of the client whose auto-draft just arrived */
  autoDraftPhone: string | null;
  /** Auto-draft content ready to go into the composer */
  autoDraftContent: string | null;
  /** Call after the draft content has been consumed by the composer */
  clearAutoDraft: () => void;
  /** Phone for which AI is currently generating (null when idle) */
  aiGeneratingPhone: string | null;
  /** Status of the current AI generation: 'started' | 'completed' | 'error' | null */
  aiGeneratingStatus: string | null;
  refreshClients: () => Promise<void>;
  loadMessages: (phone: string) => Promise<void>;
  sendMessage: (phone: string, content: string) => Promise<void>;
  requestAIDraft: (phone: string) => Promise<string | null>;
  sendAIDraft: (phone: string, content: string) => Promise<void>;
  toggleAutoDraft: (phone: string, enabled: boolean) => Promise<void>;
  broadcast: (phones: string[], content: string) => Promise<void>;
  syncWhatsApp: (phone: string) => Promise<void>;
  addReplyTarget: (phone: string, label?: string, autoDraft?: boolean) => Promise<boolean>;
  checkBridge: () => Promise<void>;
  restartBridge: () => Promise<void>;
}

/**
 * @param enabled — When false the hook skips all network activity (API + WS).
 *                  Pass `isAuthenticated` so the gateway connects only after login.
 */
export function useNanobot(enabled = true): UseNanobotReturn {
  const [clients, setClients] = useState<Client[]>([]);
  const [messages, setMessages] = useState<Record<string, Message[]>>({});
  const [backendConnected, setBackendConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [gatewayReady, setGatewayReady] = useState(false);
  const [gatewayPhase, setGatewayPhase] = useState<string | null>(null);
  const [gatewayProgress, setGatewayProgress] = useState(0);
  const [gatewayMessage, setGatewayMessage] = useState<string | null>(null);
  const [whatsappBrowserMode, setWhatsappBrowserMode] = useState<string | null>(null);
  const [whatsappBrowserReusable, setWhatsappBrowserReusable] = useState<boolean | null>(null);
  const [whatsappBrowserMessage, setWhatsappBrowserMessage] = useState<string | null>(null);
  const [whatsappBrowserSeverity, setWhatsappBrowserSeverity] = useState<'warning' | 'error' | null>(null);
  const [whatsappAuthRequired, setWhatsappAuthRequired] = useState(false);
  const [whatsappAuthQr, setWhatsappAuthQr] = useState<string | null>(null);
  const [whatsappAuthMessage, setWhatsappAuthMessage] = useState<string | null>(null);
  const [selectedClientPhone, setSelectedClientPhone] = useState<string | null>(null);
  const [autoDraftPhone, setAutoDraftPhone] = useState<string | null>(null);
  const [autoDraftContent, setAutoDraftContent] = useState<string | null>(null);
  const [aiGeneratingPhone, setAiGeneratingPhone] = useState<string | null>(null);
  const [aiGeneratingStatus, setAiGeneratingStatus] = useState<string | null>(null);
  const wsUnsubscribes = useRef<(() => void)[]>([]);

  // ─── Initial fetch + WebSocket connect ────────────────────────────

  useEffect(() => {
    // Skip all network activity when not enabled (before login)
    if (!enabled) {
      setClients([]);
      setMessages({});
      setBackendConnected(false);
      setLoading(false);
      setError(null);
      setGatewayReady(false);
      setGatewayPhase(null);
      setGatewayProgress(0);
      setGatewayMessage(null);
      setWhatsappBrowserMode(null);
      setWhatsappBrowserReusable(null);
      setWhatsappBrowserMessage(null);
      setWhatsappAuthRequired(false);
      setWhatsappAuthQr(null);
      setWhatsappAuthMessage(null);
      return;
    }

    let mounted = true;

    const applyGatewayStatus = (status: {
      status: string;
      gateway_ready?: boolean;
      gateway_starting?: boolean;
      gateway_error?: string | null;
      whatsapp_browser_mode?: string | null;
      whatsapp_browser_reusable?: boolean | null;
      whatsapp_browser_message?: string | null;
      whatsapp_browser_severity?: string | null;
      whatsapp_auth_required?: boolean | null;
      whatsapp_auth_qr?: string | null;
      whatsapp_auth_message?: string | null;
    }) => {
      const running = status.status === 'running' || status.gateway_ready === true;
      const progress = gatewayProgressState(running ? 'running' : status.status, status.gateway_starting);
      if (!mounted) return;
      setBackendConnected(running);
      setGatewayReady(running);
      setGatewayPhase(progress.phase);
      setGatewayProgress(progress.progress);
      setGatewayMessage(status.gateway_error || progress.message);
      setWhatsappBrowserMode(status.whatsapp_browser_mode ?? null);
      setWhatsappBrowserReusable(status.whatsapp_browser_reusable ?? null);
      setWhatsappBrowserMessage(status.whatsapp_browser_message ?? null);
      setWhatsappBrowserSeverity((status.whatsapp_browser_severity as 'warning' | 'error' | undefined) ?? 'warning');
      setWhatsappAuthRequired(Boolean(status.whatsapp_auth_required));
      setWhatsappAuthQr(status.whatsapp_auth_qr ?? null);
      setWhatsappAuthMessage(status.whatsapp_auth_message ?? null);
    };

    const loadClientDirectory = async () => {
      if (!mounted) return;
      const progress = gatewayProgressState('loading_clients');
      setGatewayPhase(progress.phase);
      setGatewayProgress(progress.progress);
      setGatewayMessage(progress.message);
      const apiClients = await fetchClients();
      if (!mounted) return;
      setClients(apiClients.map(apiClientToClient));
      setError(null);
      setLoading(false);
      const ready = gatewayProgressState('ready');
      setGatewayPhase(ready.phase);
      setGatewayProgress(ready.progress);
      setGatewayMessage(ready.message);
    };

    async function init() {
      try {
        const status = await fetchStatus();
        applyGatewayStatus(status);
        if (status.status === 'running' || status.gateway_ready) {
          await loadClientDirectory();
        } else if (mounted) {
          setLoading(true);
        }
      } catch (err) {
        if (mounted) {
          setBackendConnected(false);
          setGatewayReady(false);
          setLoading(false);
          setError(err instanceof Error ? err.message : '无法连接 Nanobot 后台');
        }
      }
    }

    init();

    // Connect WebSocket
    nanobotWS.connect();

    // Listen for real-time events
    const unsub1 = nanobotWS.on('new_message', (event: WSEvent) => {
      if (!event.phone || !event.content) return;
      const phone = event.phone;
      const newMsg: Message = {
        id: `ws_${Date.now()}_${Math.random()}`,
        clientId: phone,
        sender: (event.sender as 'client' | 'ai' | 'agent') || 'client',
        content: event.content,
        timestamp: formatTimestamp(event.timestamp || new Date().toISOString()),
      };
      setMessages((prev) => ({
        ...prev,
        [phone]: [...(prev[phone] || []), newMsg],
      }));
      // Update client's last message
      setClients((prev) =>
        prev.map((c) =>
          c.id === phone
            ? { ...c, lastMessage: event.content!.slice(0, 80), lastMessageTime: '刚刚' }
            : c
        )
      );
    });

    // ai_draft from manual "AI" button → pipe to composer
    const unsub2 = nanobotWS.on('ai_draft', (event: WSEvent) => {
      if (!event.phone || !event.content) return;
      setAiGeneratingPhone(null);
      setAiGeneratingStatus(null);
      setAutoDraftPhone(event.phone);
      setAutoDraftContent(event.content);
    });

    // Auto-draft arrived from the backend — put directly into the composer
    const unsub3 = nanobotWS.on('auto_draft', (event: WSEvent) => {
      if (!event.phone || !event.content) return;
      setAiGeneratingPhone(null);
      setAiGeneratingStatus(null);
      setAutoDraftPhone(event.phone);
      setAutoDraftContent(event.content);
    });

    // Auto-draft toggle changed by another WS client / backend
    const unsub4 = nanobotWS.on('auto_draft_changed', (event: WSEvent) => {
      if (!event.phone) return;
      setClients((prev) =>
        prev.map((c) =>
          c.id === event.phone
            ? { ...c, autoDraftEnabled: event.enabled ?? c.autoDraftEnabled }
            : c
        )
      );
    });

    // AI generating status from backend (for thinking indicator)
    const unsub5 = nanobotWS.on('ai_generating', (event: WSEvent) => {
      if (!event.phone) return;
      const status = (event as any).status || 'started';
      if (status === 'started') {
        setAiGeneratingPhone(event.phone);
        setAiGeneratingStatus('started');
      } else {
        // completed, error, no_response, no_message — stop generating
        setAiGeneratingPhone(null);
        setAiGeneratingStatus(null);
      }
    });

    const unsub6 = nanobotWS.on('pong', () => {
      // launcher keepalive only; actual readiness comes from gateway/status events
    });

    const unsub7 = nanobotWS.on('reply_target_added', () => {
      fetchClients()
        .then((apiClients) => {
          setClients(apiClients.map(apiClientToClient));
        })
        .catch(() => undefined);
    });

    const unsub8 = nanobotWS.on('whatsapp_browser_status', (event: WSEvent) => {
      setWhatsappBrowserMode((event.mode as string | null | undefined) ?? null);
      setWhatsappBrowserReusable(event.reusable ?? null);
      setWhatsappBrowserMessage(event.message ?? null);
      setWhatsappBrowserSeverity((event.severity as 'warning' | 'error' | undefined) ?? 'warning');
    });

    const unsub9 = nanobotWS.on('whatsapp_auth_status', (event: WSEvent) => {
      setWhatsappAuthRequired(Boolean(event.required));
      setWhatsappAuthQr(event.qr ?? null);
      setWhatsappAuthMessage(event.message ?? null);
      if (event.required) {
        setLoading(true);
      } else {
        void fetchStatus()
          .then((status) => {
            applyGatewayStatus(status);
            if (status.status === 'running' || status.gateway_ready) {
              return loadClientDirectory();
            }
            return undefined;
          })
          .catch((err) => {
            setLoading(false);
            setError(err instanceof Error ? err.message : '读取客户列表失败');
          });
      }
    });

    const unsub10 = nanobotWS.on('gateway_status', (event: WSEvent) => {
      const status = String(event.status || 'launcher');
      const progress = gatewayProgressState(status, Boolean(event.gateway_starting));
      setGatewayPhase(progress.phase);
      setGatewayProgress(progress.progress);
      setGatewayMessage(event.error || progress.message);

      if (status === 'ready') {
        setBackendConnected(true);
        setGatewayReady(true);
        setError(null);
        void loadClientDirectory().catch((err) => {
          setLoading(false);
          setError(err instanceof Error ? err.message : '读取客户列表失败');
        });
      } else if (status === 'error') {
        setLoading(false);
        setBackendConnected(false);
        setGatewayReady(false);
        setError(event.error || '网关启动失败');
      } else {
        setLoading(true);
      }
    });

    wsUnsubscribes.current = [unsub1, unsub2, unsub3, unsub4, unsub5, unsub6, unsub7, unsub8, unsub9, unsub10];

    return () => {
      mounted = false;
      wsUnsubscribes.current.forEach((u) => u());
      nanobotWS.disconnect();
    };
  }, [enabled]);

  // ─── Actions ──────────────────────────────────────────────────────

  const refreshClients = useCallback(async () => {
    try {
      const status = await fetchStatus();
      const running = status.status === 'running' || status.gateway_ready === true;
      const progress = gatewayProgressState(running ? 'running' : status.status, status.gateway_starting);
      setBackendConnected(running);
      setGatewayReady(running);
      setGatewayPhase(progress.phase);
      setGatewayProgress(progress.progress);
      setGatewayMessage(status.gateway_error || progress.message);
      setWhatsappBrowserMode(status.whatsapp_browser_mode ?? null);
      setWhatsappBrowserReusable(status.whatsapp_browser_reusable ?? null);
      setWhatsappBrowserMessage(status.whatsapp_browser_message ?? null);
      setWhatsappBrowserSeverity((status.whatsapp_browser_severity as 'warning' | 'error' | undefined) ?? 'warning');
      setWhatsappAuthRequired(Boolean(status.whatsapp_auth_required));
      setWhatsappAuthQr(status.whatsapp_auth_qr ?? null);
      setWhatsappAuthMessage(status.whatsapp_auth_message ?? null);
      const apiClients = await fetchClients();
      setClients(apiClients.map(apiClientToClient));
      setError(null);
    } catch {
      setBackendConnected(false);
    }
  }, []);

  const loadMessages = useCallback(async (phone: string) => {
    setSelectedClientPhone(phone);
    try {
      const apiMsgs = await fetchMessages(phone);
      setMessages((prev) => ({
        ...prev,
        [phone]: apiMsgs.map((m) => apiMessageToMessage(m, phone)),
      }));
    } catch {
      // Keep existing messages (could be from WS)
    }
  }, []);

  const handleSendMessage = useCallback(async (phone: string, content: string) => {
    setClients((prev) =>
      prev.map((c) =>
        c.id === phone ? { ...c, lastMessage: content, lastMessageTime: '刚刚' } : c
      )
    );

    try {
      await apiSend(phone, content);
    } catch (err) {
      console.error('Failed to send message:', err);
      throw err;
    }
  }, []);

  const handleRequestAIDraft = useCallback(async (phone: string): Promise<string | null> => {
    try {
      const result = await apiAIDraft(phone);
      return result.draft || null;
    } catch (err) {
      console.error('AI draft failed:', err);
      return null;
    }
  }, []);

  const handleSendAIDraft = useCallback(async (phone: string, content: string) => {
    try {
      await apiSendDraft(phone, content);
    } catch (err) {
      console.error('Failed to send AI draft:', err);
      throw err;
    }
  }, []);

  const handleToggleAutoDraft = useCallback(async (phone: string, enabled: boolean) => {
    // Optimistic update
    setClients((prev) =>
      prev.map((c) => (c.id === phone ? { ...c, autoDraftEnabled: enabled } : c))
    );
    try {
      await apiToggleAutoDraft(phone, enabled);
    } catch (err) {
      // Revert
      setClients((prev) =>
        prev.map((c) => (c.id === phone ? { ...c, autoDraftEnabled: !enabled } : c))
      );
      console.error('Failed to toggle auto-draft:', err);
    }
  }, []);

  const clearAutoDraft = useCallback(() => {
    setAutoDraftPhone(null);
    setAutoDraftContent(null);
  }, []);

  const handleBroadcast = useCallback(async (phones: string[], content: string) => {
    try {
      await apiBroadcast(phones, content);
    } catch (err) {
      console.error('Broadcast failed:', err);
    }
  }, []);

  const handleSync = useCallback(async (phone: string) => {
    try {
      await apiSync(phone);
    } catch (err) {
      console.error('Sync failed:', err);
      throw err;
    }
  }, []);

  const handleAddReplyTarget = useCallback(async (phone: string, label?: string, autoDraft?: boolean): Promise<boolean> => {
    try {
      await apiAddReplyTarget(phone, label, autoDraft);
      // Refresh the client list to pick up the new target
      await refreshClients();
      return true;
    } catch (err) {
      console.error('Add reply target failed:', err);
      return false;
    }
  }, [refreshClients]);

  const handleCheckBridge = useCallback(async () => {
    try {
      await apiCheckBridge();
      // Status update will arrive via WS broadcast automatically
    } catch (err) {
      console.error('Bridge check failed:', err);
    }
  }, []);

  const handleRestartBridge = useCallback(async () => {
    try {
      await apiRestartBridge();
      // Status updates will arrive via WS broadcast automatically
    } catch (err) {
      console.error('Bridge restart failed:', err);
    }
  }, []);

  return {
    clients,
    messages,
    backendConnected,
    loading,
    error,
    gatewayReady,
    gatewayPhase,
    gatewayProgress,
    gatewayMessage,
    whatsappBrowserMode,
    whatsappBrowserReusable,
    whatsappBrowserMessage,
    whatsappBrowserSeverity,
    whatsappAuthRequired,
    whatsappAuthQr,
    whatsappAuthMessage,
    selectedClientPhone,
    autoDraftPhone,
    autoDraftContent,
    clearAutoDraft,
    aiGeneratingPhone,
    aiGeneratingStatus,
    refreshClients,
    loadMessages,
    sendMessage: handleSendMessage,
    requestAIDraft: handleRequestAIDraft,
    sendAIDraft: handleSendAIDraft,
    toggleAutoDraft: handleToggleAutoDraft,
    broadcast: handleBroadcast,
    syncWhatsApp: handleSync,
    addReplyTarget: handleAddReplyTarget,
    checkBridge: handleCheckBridge,
    restartBridge: handleRestartBridge,
  };
}
