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
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchClients,
  sendMessage as apiSend,
  deleteClient as apiDeleteClient,
  requestAIDraft as apiAIDraft,
  sendAIDraft as apiSendDraft,
  toggleAutoDraft as apiToggleAutoDraft,
  sendBroadcast as apiBroadcast,
  triggerSync as apiSync,
  fetchStatus,
  addReplyTarget as apiAddReplyTarget,
  restartBridge as apiRestartBridge,
  type ApiClient,
  type SyncResult,
} from '../services/api';
import { nanobotWS, type WSEvent } from '../services/websocket';
import type { Client } from '../types';

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

export interface UseNanobotReturn {
  clients: Client[];
  backendConnected: boolean;
  loading: boolean;
  error: string | null;
  gatewayReady: boolean;
  gatewayPhase: string | null;
  gatewayProgress: number;
  gatewayMessage: string | null;
  whatsappBridgeError: boolean;
  whatsappBridgeMessage: string | null;
  whatsappAuthRequired: boolean;
  whatsappAuthQr: string | null;
  whatsappAuthMessage: string | null;
  selectedClientPhone: string | null;
  autoDraftPhone: string | null;
  autoDraftContent: string | null;
  clearAutoDraft: () => void;
  aiGeneratingPhone: string | null;
  aiGeneratingStatus: string | null;
  messageReloadToken: number;
  refreshClients: () => Promise<void>;
  loadMessages: (phone: string) => Promise<number>;
  waitForMessagesLoaded: (phone: string, reloadToken: number, timeoutMs?: number) => Promise<void>;
  markMessagesLoaded: (phone: string, reloadToken: number) => void;
  sendMessage: (phone: string, content: string) => Promise<void>;
  deleteClient: (phone: string) => Promise<void>;
  requestAIDraft: (phone: string) => Promise<string | null>;
  sendAIDraft: (phone: string, content: string) => Promise<void>;
  toggleAutoDraft: (phone: string, enabled: boolean) => Promise<void>;
  broadcast: (phones: string[], content: string) => Promise<void>;
  syncWhatsApp: (phone: string) => Promise<SyncResult>;
  addReplyTarget: (phone: string, label?: string, autoDraft?: boolean) => Promise<boolean>;
  restartBridge: () => Promise<void>;
}

export function useNanobot(enabled = true): UseNanobotReturn {
  const [clients, setClients] = useState<Client[]>([]);
  const [backendConnected, setBackendConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [gatewayReady, setGatewayReady] = useState(false);
  const [gatewayPhase, setGatewayPhase] = useState<string | null>(null);
  const [gatewayProgress, setGatewayProgress] = useState(0);
  const [gatewayMessage, setGatewayMessage] = useState<string | null>(null);
  const [whatsappBridgeError, setWhatsappBridgeError] = useState(false);
  const [whatsappBridgeMessage, setWhatsappBridgeMessage] = useState<string | null>(null);
  const [whatsappAuthRequired, setWhatsappAuthRequired] = useState(false);
  const [whatsappAuthQr, setWhatsappAuthQr] = useState<string | null>(null);
  const [whatsappAuthMessage, setWhatsappAuthMessage] = useState<string | null>(null);
  const [selectedClientPhone, setSelectedClientPhone] = useState<string | null>(null);
  const [autoDraftPhone, setAutoDraftPhone] = useState<string | null>(null);
  const [autoDraftContent, setAutoDraftContent] = useState<string | null>(null);
  const [aiGeneratingPhone, setAiGeneratingPhone] = useState<string | null>(null);
  const [aiGeneratingStatus, setAiGeneratingStatus] = useState<string | null>(null);
  const [messageReloadToken, setMessageReloadToken] = useState(0);
  const wsUnsubscribes = useRef<(() => void)[]>([]);
  const selectedClientPhoneRef = useRef<string | null>(null);
  const clientListRequestIdRef = useRef(0);
  const messageReloadTokenRef = useRef(0);
  const lastLoadedTranscriptRef = useRef<{ phone: string; reloadToken: number } | null>(null);
  const transcriptLoadWaitersRef = useRef(new Map<string, {
    timer: number;
    waiters: Array<{ resolve: () => void; reject: (error: Error) => void }>;
  }>());

  const bumpMessageReloadToken = useCallback(() => {
    messageReloadTokenRef.current += 1;
    setMessageReloadToken(messageReloadTokenRef.current);
    return messageReloadTokenRef.current;
  }, []);

  const cancelTranscriptLoadWaiters = useCallback((message: string) => {
    transcriptLoadWaitersRef.current.forEach((entry) => {
      window.clearTimeout(entry.timer);
      entry.waiters.forEach((waiter) => waiter.reject(new Error(message)));
    });
    transcriptLoadWaitersRef.current.clear();
  }, []);

  const applyGatewayStatus = useCallback((status: {
    status: string;
    gateway_ready?: boolean;
    gateway_starting?: boolean;
    gateway_error?: string | null;
    whatsapp_bridge_error?: boolean | null;
    whatsapp_bridge_message?: string | null;
    whatsapp_auth_required?: boolean | null;
    whatsapp_auth_qr?: string | null;
    whatsapp_auth_message?: string | null;
  }) => {
    const running = status.status === 'running' || status.gateway_ready === true;
    const progress = gatewayProgressState(running ? 'running' : status.status, status.gateway_starting);
    setBackendConnected(running);
    setGatewayReady(running);
    setGatewayPhase(progress.phase);
    setGatewayProgress(progress.progress);
    setGatewayMessage(status.gateway_error || progress.message);
    setWhatsappBridgeError(Boolean(status.whatsapp_bridge_error));
    setWhatsappBridgeMessage(status.whatsapp_bridge_message ?? null);
    setWhatsappAuthRequired(Boolean(status.whatsapp_auth_required));
    setWhatsappAuthQr(status.whatsapp_auth_qr ?? null);
    setWhatsappAuthMessage(status.whatsapp_auth_message ?? null);
  }, []);

  const clearCurrentThread = useCallback((phone?: string | null) => {
    if (phone && selectedClientPhoneRef.current !== phone) {
      return;
    }
    selectedClientPhoneRef.current = null;
    setSelectedClientPhone(null);
    bumpMessageReloadToken();
  }, [bumpMessageReloadToken]);

  const refreshClientList = useCallback(async (): Promise<Client[] | null> => {
    const requestId = ++clientListRequestIdRef.current;
    const apiClients = await fetchClients();
    if (requestId !== clientListRequestIdRef.current) {
      return null;
    }
    const mappedClients = apiClients.map(apiClientToClient);
    setClients(mappedClients);
    setError(null);
    return mappedClients;
  }, []);

  const loadMessages = useCallback(async (phone: string) => {
    selectedClientPhoneRef.current = phone;
    setSelectedClientPhone(phone);
    return bumpMessageReloadToken();
  }, [bumpMessageReloadToken]);

  const makeTranscriptLoadKey = useCallback((phone: string, reloadToken: number) => `${phone}:${reloadToken}`, []);

  const markMessagesLoaded = useCallback((phone: string, reloadToken: number) => {
    lastLoadedTranscriptRef.current = { phone, reloadToken };
    const key = makeTranscriptLoadKey(phone, reloadToken);
    const entry = transcriptLoadWaitersRef.current.get(key);
    if (!entry) {
      return;
    }
    window.clearTimeout(entry.timer);
    transcriptLoadWaitersRef.current.delete(key);
    entry.waiters.forEach((waiter) => waiter.resolve());
  }, [makeTranscriptLoadKey]);

  const waitForMessagesLoaded = useCallback(async (phone: string, reloadToken: number, timeoutMs: number = 15000) => {
    const loaded = lastLoadedTranscriptRef.current;
    if (loaded && loaded.phone === phone && loaded.reloadToken === reloadToken) {
      return;
    }

    const key = makeTranscriptLoadKey(phone, reloadToken);
    return new Promise<void>((resolve, reject) => {
      const existing = transcriptLoadWaitersRef.current.get(key);
      if (existing) {
        existing.waiters.push({ resolve, reject });
        return;
      }

      const timer = window.setTimeout(() => {
        const pending = transcriptLoadWaitersRef.current.get(key);
        transcriptLoadWaitersRef.current.delete(key);
        (pending?.waiters || []).forEach((waiter) => waiter.reject(new Error('聊天记录加载超时，请重试')));
      }, timeoutMs);
      transcriptLoadWaitersRef.current.set(key, {
        timer,
        waiters: [{ resolve, reject }],
      });
    });
  }, [makeTranscriptLoadKey]);

  const refreshClients = useCallback(async () => {
    try {
      const status = await fetchStatus();
      applyGatewayStatus(status);
      if (status.status === 'running' || status.gateway_ready === true) {
        await refreshClientList();
      }
      setError(null);
    } catch {
      setBackendConnected(false);
    }
  }, [applyGatewayStatus, refreshClientList]);

  useEffect(() => {
    if (!enabled) {
      clientListRequestIdRef.current += 1;
      clearCurrentThread();
      setClients([]);
      setBackendConnected(false);
      setLoading(false);
      setError(null);
      setGatewayReady(false);
      setGatewayPhase(null);
      setGatewayProgress(0);
      setGatewayMessage(null);
      setWhatsappBridgeError(false);
      setWhatsappBridgeMessage(null);
      setWhatsappAuthRequired(false);
      setWhatsappAuthQr(null);
      setWhatsappAuthMessage(null);
      setAutoDraftPhone(null);
      setAutoDraftContent(null);
      setAiGeneratingPhone(null);
      setAiGeneratingStatus(null);
      setMessageReloadToken(0);
      messageReloadTokenRef.current = 0;
      lastLoadedTranscriptRef.current = null;
      cancelTranscriptLoadWaiters('聊天记录加载已取消');
      return;
    }

    let mounted = true;

    const loadClientDirectory = async () => {
      if (!mounted) return;
      const progress = gatewayProgressState('loading_clients');
      setGatewayPhase(progress.phase);
      setGatewayProgress(progress.progress);
      setGatewayMessage(progress.message);
      await refreshClientList();
      if (!mounted) return;
      setLoading(false);
      const ready = gatewayProgressState('ready');
      setGatewayPhase(ready.phase);
      setGatewayProgress(ready.progress);
      setGatewayMessage(ready.message);
    };

    async function init() {
      try {
        const status = await fetchStatus();
        if (!mounted) return;
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
    nanobotWS.connect();

    const unsub1 = nanobotWS.on('new_message', (event: WSEvent) => {
      if (!event.phone) return;
      if (selectedClientPhoneRef.current === event.phone) {
        bumpMessageReloadToken();
      }
      void refreshClients();
    });

    const unsub2 = nanobotWS.on('ai_draft', (event: WSEvent) => {
      if (!event.phone || !event.content) return;
      setAiGeneratingPhone(null);
      setAiGeneratingStatus(null);
      setAutoDraftPhone(event.phone);
      setAutoDraftContent(event.content);
    });

    const unsub3 = nanobotWS.on('auto_draft', (event: WSEvent) => {
      if (!event.phone || !event.content) return;
      setAiGeneratingPhone(null);
      setAiGeneratingStatus(null);
      setAutoDraftPhone(event.phone);
      setAutoDraftContent(event.content);
    });

    const unsub4 = nanobotWS.on('auto_draft_changed', (event: WSEvent) => {
      if (!event.phone) return;
      setClients((prev) =>
        prev.map((client) =>
          client.id === event.phone
            ? { ...client, autoDraftEnabled: event.enabled ?? client.autoDraftEnabled }
            : client
        )
      );
    });

    const unsub5 = nanobotWS.on('ai_generating', (event: WSEvent) => {
      if (!event.phone) return;
      const status = event.status || 'started';
      if (status === 'started') {
        setAiGeneratingPhone(event.phone);
        setAiGeneratingStatus('started');
      } else {
        setAiGeneratingPhone(null);
        setAiGeneratingStatus(null);
      }
    });

    const unsub6 = nanobotWS.on('pong', () => {
      return;
    });

    const unsub7 = nanobotWS.on('reply_target_added', () => {
      void refreshClientList();
    });

    const unsub8 = nanobotWS.on('whatsapp_bridge_status', (event: WSEvent) => {
      setWhatsappBridgeError(Boolean(event.bridgeError ?? event.error));
      setWhatsappBridgeMessage(event.message ?? null);
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
            if (!mounted) return;
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

    const unsub11 = nanobotWS.on('client_deleted', (event: WSEvent) => {
      if (!event.phone) return;
      setClients((prev) => prev.filter((client) => client.id !== event.phone));
      clearCurrentThread(event.phone);
      void refreshClients();
    });

    wsUnsubscribes.current = [
      unsub1,
      unsub2,
      unsub3,
      unsub4,
      unsub5,
      unsub6,
      unsub7,
      unsub8,
      unsub9,
      unsub10,
      unsub11,
    ];

    return () => {
      mounted = false;
      wsUnsubscribes.current.forEach((unsubscribe) => unsubscribe());
      cancelTranscriptLoadWaiters('聊天记录加载已取消');
      nanobotWS.disconnect();
    };
  }, [
    enabled,
    applyGatewayStatus,
    bumpMessageReloadToken,
    cancelTranscriptLoadWaiters,
    clearCurrentThread,
    refreshClientList,
    refreshClients,
  ]);

  const handleSendMessage = useCallback(async (phone: string, content: string) => {
    try {
      await apiSend(phone, content);
      if (selectedClientPhoneRef.current === phone) {
        bumpMessageReloadToken();
      }
      await refreshClients();
    } catch (err) {
      console.error('Failed to send message:', err);
      throw err;
    }
  }, [bumpMessageReloadToken, refreshClients]);

  const handleDeleteClient = useCallback(async (phone: string) => {
    try {
      await apiDeleteClient(phone);
      setClients((prev) => prev.filter((client) => client.id !== phone));
      clearCurrentThread(phone);
      await refreshClients();
    } catch (err) {
      console.error('Failed to delete client:', err);
      throw err;
    }
  }, [clearCurrentThread, refreshClients]);

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
      if (selectedClientPhoneRef.current === phone) {
        bumpMessageReloadToken();
      }
      await refreshClients();
    } catch (err) {
      console.error('Failed to send AI draft:', err);
      throw err;
    }
  }, [bumpMessageReloadToken, refreshClients]);

  const handleToggleAutoDraft = useCallback(async (phone: string, enabled: boolean) => {
    setClients((prev) =>
      prev.map((client) => (client.id === phone ? { ...client, autoDraftEnabled: enabled } : client))
    );
    try {
      await apiToggleAutoDraft(phone, enabled);
    } catch (err) {
      setClients((prev) =>
        prev.map((client) => (client.id === phone ? { ...client, autoDraftEnabled: !enabled } : client))
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
      return await apiSync(phone);
    } catch (err) {
      console.error('Sync failed:', err);
      throw err;
    }
  }, []);

  const handleAddReplyTarget = useCallback(async (phone: string, label?: string, autoDraft?: boolean): Promise<boolean> => {
    try {
      await apiAddReplyTarget(phone, label, autoDraft);
      await refreshClients();
      return true;
    } catch (err) {
      console.error('Add reply target failed:', err);
      return false;
    }
  }, [refreshClients]);

  const handleRestartBridge = useCallback(async () => {
    try {
      await apiRestartBridge();
    } catch (err) {
      console.error('Bridge restart failed:', err);
    }
  }, []);

  return {
    clients,
    backendConnected,
    loading,
    error,
    gatewayReady,
    gatewayPhase,
    gatewayProgress,
    gatewayMessage,
    whatsappBridgeError,
    whatsappBridgeMessage,
    whatsappAuthRequired,
    whatsappAuthQr,
    whatsappAuthMessage,
    selectedClientPhone,
    autoDraftPhone,
    autoDraftContent,
    clearAutoDraft,
    aiGeneratingPhone,
    aiGeneratingStatus,
    messageReloadToken,
    refreshClients,
    loadMessages,
    waitForMessagesLoaded,
    markMessagesLoaded,
    sendMessage: handleSendMessage,
    deleteClient: handleDeleteClient,
    requestAIDraft: handleRequestAIDraft,
    sendAIDraft: handleSendAIDraft,
    toggleAutoDraft: handleToggleAutoDraft,
    broadcast: handleBroadcast,
    syncWhatsApp: handleSync,
    addReplyTarget: handleAddReplyTarget,
    restartBridge: handleRestartBridge,
  };
}
