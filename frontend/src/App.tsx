import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { 
  AlertTriangle, MessageSquare, Radio, Shield, LogOut, FileText, 
  ChevronLeft, ChevronRight, Menu, X, Wifi, WifiOff, UserPlus
} from 'lucide-react';
import { ClientList } from './components/ClientList/ClientList';
import { MessageThread } from './components/MessageCenter/MessageThread';
import { MessageInput } from './components/MessageCenter/MessageInput';
import { BroadcastModal } from './components/MessageCenter/BroadcastModal';
import { ClientProfile } from './components/ClientDetail/ClientProfile';
import { LoginPage } from './components/Auth/LoginPage';
import { LogViewer } from './components/Logs/LogViewer';
import { AddReplyTargetModal } from './components/MessageCenter/AddReplyTargetModal';
import { GatewayBootstrapOverlay } from './components/common/GatewayBootstrapOverlay';
import { FloatingStatusNotice } from './components/common/FloatingStatusNotice';
import { useRecording } from './hooks/useRecording';
import { useAIGeneration } from './hooks/useAIGeneration';
import { useLogger } from './hooks/useLogger';
import { useNanobot } from './hooks/useNanobot';
import { fetchOfflineMeetingNoteDetail, fetchOfflineMeetingNotes, saveOfflineMeetingNote } from './services/api';
import type { VoiceMemo, VoiceMemoDetail } from './types';

interface OfflineMeetingDraftState {
  clientId: string;
  noteId: string;
  defaultNoteName: string;
  noteNameInput: string;
  text: string;
}

function App() {
  // Auth State
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState<{ id: string; name: string } | null>(null);
  const [loginError, setLoginError] = useState('');

  // Layout State - Responsive
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(true);
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
  const [isMobile, setIsMobile] = useState(false);

  // App State
  const [selectedClientId, setSelectedClientId] = useState<string | null>(null);
  const [broadcastMode, setBroadcastMode] = useState(false);
  const [showBroadcastModal, setShowBroadcastModal] = useState(false);
  const [showLogViewer, setShowLogViewer] = useState(false);
  const [showAddTarget, setShowAddTarget] = useState(false);
  const [editingDraftContent, setEditingDraftContent] = useState<string | null>(null);
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [initialConversationLoading, setInitialConversationLoading] = useState(false);
  const [deleteCandidateId, setDeleteCandidateId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deletingClient, setDeletingClient] = useState(false);
  const [voiceMemos, setVoiceMemos] = useState<VoiceMemo[]>([]);
  const [voiceMemoBrowserOpen, setVoiceMemoBrowserOpen] = useState(false);
  const [voiceMemoLoading, setVoiceMemoLoading] = useState(false);
  const [voiceMemoError, setVoiceMemoError] = useState('');
  const [selectedVoiceMemoId, setSelectedVoiceMemoId] = useState<string | null>(null);
  const [selectedVoiceMemoDetail, setSelectedVoiceMemoDetail] = useState<VoiceMemoDetail | null>(null);
  const [voiceMemoDetailLoading, setVoiceMemoDetailLoading] = useState(false);
  const [voiceMemoDetailError, setVoiceMemoDetailError] = useState('');
  const [offlineMeetingDraft, setOfflineMeetingDraft] = useState<OfflineMeetingDraftState | null>(null);
  const [offlineMeetingDraftError, setOfflineMeetingDraftError] = useState('');
  const [savingOfflineMeetingDraft, setSavingOfflineMeetingDraft] = useState(false);
  const [recordingSuccessToken, setRecordingSuccessToken] = useState(0);
  const selectedClientIdRef = useRef<string | null>(null);
  const voiceMemoDetailsRef = useRef<Record<string, VoiceMemoDetail>>({});

  // Nanobot backend hook — only activates after login
  const nanobot = useNanobot(isAuthenticated);

  const clients = nanobot.clients;
  selectedClientIdRef.current = selectedClientId;

  // Hooks
  const { aiLoading, startGeneration, stopGeneration } = useAIGeneration();
  const { logs, addLog, setUser, clearLogs, downloadLogs } = useLogger(isAuthenticated);
  const {
    recordingState,
    recordingTime,
    error: recordingError,
    startRecording,
    stopRecording,
  } = useRecording({
    clientId: selectedClientId,
    enabled: nanobot.backendConnected && !offlineMeetingDraft && !savingOfflineMeetingDraft,
    onTranscriptDraft: (clientId, draft) => {
      if (selectedClientIdRef.current !== clientId) {
        return;
      }
      setOfflineMeetingDraft({
        clientId,
        noteId: draft.noteId,
        defaultNoteName: draft.noteName,
        noteNameInput: draft.noteName,
        text: draft.transcript,
      });
      setOfflineMeetingDraftError('');
    },
  });

  // Check responsive on mount and resize
  useEffect(() => {
    const checkResponsive = () => {
      const width = window.innerWidth;
      const mobile = width < 768;
      const tablet = width >= 768 && width < 1280;

      setIsMobile(mobile);
      
      if (mobile) {
        setLeftSidebarOpen(false);
        setRightPanelOpen(false);
      } else if (tablet) {
        setLeftSidebarOpen(true);
        setRightPanelOpen(false);
      } else {
        setLeftSidebarOpen(true);
        setRightPanelOpen(true);
      }
    };

    checkResponsive();
    window.addEventListener('resize', checkResponsive);
    return () => window.removeEventListener('resize', checkResponsive);
  }, []);

  // Set logger user when authenticated
  useEffect(() => {
    if (currentUser) {
      setUser(currentUser);
    }
  }, [currentUser, setUser]);

  useEffect(() => {
    if (!isAuthenticated || !nanobot.gatewayReady) {
      return;
    }

    if (clients.length === 0) {
      setSelectedClientId(null);
      setInitialConversationLoading(false);
      return;
    }

    if (selectedClientId && clients.some((client) => client.id === selectedClientId)) {
      return;
    }

    const firstClientId = clients[0].id;
    setSelectedClientId(firstClientId);
    setInitialConversationLoading(true);
    void nanobot.loadMessages(firstClientId).finally(() => {
      setInitialConversationLoading(false);
    });
  }, [isAuthenticated, nanobot, clients, selectedClientId]);

  // Auto-draft → composer: when an auto-generated AI draft arrives, put it in the input
  useEffect(() => {
    if (nanobot.autoDraftPhone && nanobot.autoDraftContent) {
      // Draft arrived — stop the thinking indicator
      stopGeneration();
      // If the user is looking at the client whose draft just arrived,
      // immediately load it into the composer textarea.
      if (nanobot.autoDraftPhone === selectedClientId) {
        setEditingDraftContent(nanobot.autoDraftContent);
        setEditingDraftId(`auto_${Date.now()}`);
      }
      // Even if not viewing this client, store it so it'll load when they select it
      // (we clear after consuming)
      nanobot.clearAutoDraft();
    }
  }, [nanobot.autoDraftPhone, nanobot.autoDraftContent, selectedClientId, nanobot, stopGeneration]);

  // Backend-driven AI generating indicator (for both auto-draft and manual paths)
  useEffect(() => {
    if (
      nanobot.aiGeneratingStatus === 'started' &&
      nanobot.aiGeneratingPhone &&
      nanobot.aiGeneratingPhone === selectedClientId
    ) {
      startGeneration();
    } else if (nanobot.aiGeneratingStatus !== 'started' && nanobot.aiGeneratingPhone === null) {
      // AI stopped (completed, error, etc.) — stop is also handled when draft arrives
    }
  }, [nanobot.aiGeneratingStatus, nanobot.aiGeneratingPhone, selectedClientId, startGeneration]);

  // Derived state
  const selectedClient = useMemo(
    () => clients.find((c) => c.id === selectedClientId) || null,
    [clients, selectedClientId]
  );

  useEffect(() => {
    if (!offlineMeetingDraft) {
      return;
    }
    if (!selectedClientId || offlineMeetingDraft.clientId !== selectedClientId) {
      setOfflineMeetingDraft(null);
      setOfflineMeetingDraftError('');
    }
  }, [offlineMeetingDraft, selectedClientId]);

  useEffect(() => {
    setVoiceMemos([]);
    setSelectedVoiceMemoId(null);
    setSelectedVoiceMemoDetail(null);
    setVoiceMemoError('');
    setVoiceMemoDetailError('');
    setVoiceMemoLoading(false);
    setVoiceMemoDetailLoading(false);
    voiceMemoDetailsRef.current = {};
  }, [selectedClientId]);

  const handleSelectVoiceMemo = useCallback(async (noteId: string, providedDetail?: VoiceMemoDetail) => {
    if (!selectedClientId) {
      return;
    }

    const activeClientId = selectedClientId;
    setSelectedVoiceMemoId(noteId);
    setVoiceMemoDetailError('');
    setSelectedVoiceMemoDetail(null);

    if (providedDetail) {
      voiceMemoDetailsRef.current[noteId] = providedDetail;
      setSelectedVoiceMemoDetail(providedDetail);
      setVoiceMemoDetailLoading(false);
      return;
    }

    const cachedDetail = voiceMemoDetailsRef.current[noteId];
    if (cachedDetail && cachedDetail.clientId === activeClientId) {
      setSelectedVoiceMemoDetail(cachedDetail);
      setVoiceMemoDetailLoading(false);
      return;
    }

    setVoiceMemoDetailLoading(true);
    try {
      const { note } = await fetchOfflineMeetingNoteDetail(activeClientId, noteId);
      if (selectedClientIdRef.current !== activeClientId) {
        return;
      }
      const detail: VoiceMemoDetail = {
        id: note.noteId,
        clientId: activeClientId,
        noteName: note.noteName,
        createdAt: note.createdAt,
        transcript: note.transcript,
      };
      voiceMemoDetailsRef.current[noteId] = detail;
      setSelectedVoiceMemoDetail(detail);
    } catch (error) {
      if (selectedClientIdRef.current !== activeClientId) {
        return;
      }
      setSelectedVoiceMemoDetail(null);
      setVoiceMemoDetailError(error instanceof Error ? error.message : '载入笔记失败，请稍后重试。');
    } finally {
      if (selectedClientIdRef.current === activeClientId) {
        setVoiceMemoDetailLoading(false);
      }
    }
  }, [selectedClientId]);

  useEffect(() => {
    if (!selectedClientId || !nanobot.backendConnected) {
      setVoiceMemos([]);
      setSelectedVoiceMemoId(null);
      setSelectedVoiceMemoDetail(null);
      setVoiceMemoLoading(false);
      setVoiceMemoError('');
      return;
    }
    if (!voiceMemoBrowserOpen) {
      setVoiceMemoLoading(false);
      setVoiceMemoError('');
      return;
    }

    let active = true;
    setVoiceMemoLoading(true);
    setVoiceMemoError('');

    fetchOfflineMeetingNotes(selectedClientId)
      .then((response) => {
        if (!active) {
          return;
        }
        const notes = Array.isArray(response.notes) ? response.notes : [];
        const indexedNotes = notes.map((note) => ({
          id: note.noteId,
          clientId: selectedClientId,
          noteName: note.noteName,
          createdAt: note.createdAt,
        }));
        setVoiceMemos(indexedNotes);
        if (indexedNotes.length === 0) {
          setSelectedVoiceMemoId(null);
          setSelectedVoiceMemoDetail(null);
          setVoiceMemoDetailError('');
          return;
        }

        const preferredNoteId = indexedNotes.some((note) => note.id === selectedVoiceMemoId)
          ? selectedVoiceMemoId
          : indexedNotes[indexedNotes.length - 1].id;
        if (!preferredNoteId) {
          return;
        }
        void handleSelectVoiceMemo(preferredNoteId);
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        console.error('Failed to load offline meeting notes:', error);
        setVoiceMemos([]);
        setSelectedVoiceMemoId(null);
        setSelectedVoiceMemoDetail(null);
        setVoiceMemoError(error instanceof Error ? error.message : '载入笔记失败，请稍后重试。');
      })
      .finally(() => {
        if (!active) {
          return;
        }
        setVoiceMemoLoading(false);
      });

    return () => {
      active = false;
    };
  }, [selectedClientId, nanobot.backendConnected, voiceMemoBrowserOpen, handleSelectVoiceMemo]);

  useEffect(() => {
    if (!voiceMemoBrowserOpen) {
      return;
    }
    if (!selectedVoiceMemoId && voiceMemos.length > 0) {
      void handleSelectVoiceMemo(voiceMemos[voiceMemos.length - 1].id);
    }
  }, [voiceMemos, selectedVoiceMemoId, voiceMemoBrowserOpen, handleSelectVoiceMemo]);

  const isClientLoading = initialConversationLoading;
  const showBootstrapOverlay = isAuthenticated && (
    nanobot.loading ||
    !nanobot.gatewayReady ||
    nanobot.whatsappAuthRequired ||
    initialConversationLoading
  );

  // Auth Handlers
  const handleLogin = useCallback((username: string, _password: string) => {
    const user = { id: `user_${Date.now()}`, name: username };
    setCurrentUser(user);
    setIsAuthenticated(true);
    setLoginError('');
  }, []);

  const handleLogout = useCallback(() => {
    addLog('LOGOUT', `用户 ${currentUser?.name} 退出系统`);
    setIsAuthenticated(false);
    setCurrentUser(null);
    setSelectedClientId(null);
    setEditingDraftContent(null);
    setEditingDraftId(null);
    setInitialConversationLoading(false);
    setDeleteCandidateId(null);
    setDeleteError('');
    setDeletingClient(false);
    setVoiceMemos([]);
    setVoiceMemoBrowserOpen(false);
    setVoiceMemoLoading(false);
    setVoiceMemoError('');
    setSelectedVoiceMemoId(null);
    setSelectedVoiceMemoDetail(null);
    setVoiceMemoDetailLoading(false);
    setVoiceMemoDetailError('');
    voiceMemoDetailsRef.current = {};
    setOfflineMeetingDraft(null);
    setOfflineMeetingDraftError('');
    setSavingOfflineMeetingDraft(false);
    setRecordingSuccessToken(0);
  }, [addLog, currentUser]);

  // Handlers
  const handleSelectClient = useCallback((clientId: string) => {
    setSelectedClientId(clientId);
    // Load messages from backend if connected
    if (nanobot.backendConnected) {
      nanobot.loadMessages(clientId);
    }
    if (isMobile) {
      setLeftSidebarOpen(false);
    }
  }, [isMobile, nanobot]);

  const handleToggleAutoDraft = useCallback((clientId: string) => {
    const client = clients.find(c => c.id === clientId);
    const newState = !client?.autoDraftEnabled;
    if (!nanobot.backendConnected) {
      return;
    }
    nanobot.toggleAutoDraft(clientId, newState);
  }, [clients, nanobot]);

  const pickNextClientId = useCallback((clientId: string): string | null => {
    const currentIndex = clients.findIndex((client) => client.id === clientId);
    if (currentIndex === -1) {
      return null;
    }
    return clients[currentIndex + 1]?.id ?? clients[currentIndex - 1]?.id ?? null;
  }, [clients]);

  const handleSyncWhatsApp = useCallback(async (clientId: string) => {
    if (!nanobot.backendConnected) {
      return;
    }

    const result = await nanobot.syncWhatsApp(clientId);

    const finishWithTranscriptLoad = async () => {
      await nanobot.refreshClients();
      if (selectedClientId === clientId) {
        const reloadToken = await nanobot.loadMessages(clientId);
        await nanobot.waitForMessagesLoaded(clientId, reloadToken, 15000);
      }
    };

    if (result.backendSuccess) {
      void finishWithTranscriptLoad().catch((error) => {
        console.warn('WhatsApp sync succeeded but transcript refresh did not finish cleanly:', error);
      });
      return;
    }

    throw new Error('未检测到正确客户会话中的同步聊天记录，请确认打开的是正确联系人后再试');
  }, [nanobot, selectedClientId]);

  const handleSendMessage = useCallback(
    async (content: string) => {
      if (!selectedClientId) return;
      const wasDraftEdit = !!editingDraftId;

      // Clear editing state
      if (editingDraftId) {
        setEditingDraftId(null);
      }

      // If this was an edited AI draft, route through sendAIDraft (saves to JSONL + sends)
      if (wasDraftEdit && nanobot.backendConnected) {
        await nanobot.sendAIDraft(selectedClientId, content);
        return;
      }

      if (!nanobot.backendConnected) {
        return;
      }
      await nanobot.sendMessage(selectedClientId, content);
    },
    [selectedClientId, nanobot, editingDraftId]
  );

  const handleRequestAI = useCallback(() => {
    if (!selectedClientId) return;
    startGeneration();

    if (!nanobot.backendConnected) {
      stopGeneration();
      return;
    }
    nanobot.requestAIDraft(selectedClientId).then((draft) => {
      if (draft && selectedClientId) {
        setEditingDraftContent(draft);
        setEditingDraftId(`ai_${Date.now()}`);
      }
      stopGeneration();
    }).catch(() => {
      stopGeneration();
    });
  }, [selectedClientId, startGeneration, stopGeneration, nanobot]);

  const handleToggleBroadcast = useCallback(() => {
    if (!broadcastMode) {
      setShowBroadcastModal(true);
    }
    setBroadcastMode((prev) => !prev);
  }, [broadcastMode]);

  const handleSendBroadcast = useCallback(
    (clientIds: string[], message: string) => {
      if (!nanobot.backendConnected) {
        return;
      }
      nanobot.broadcast(clientIds, message);

      setBroadcastMode(false);
    },
    [nanobot]
  );

  const handleStartRecording = useCallback(() => {
    return startRecording();
  }, [startRecording]);

  const handleStopRecording = useCallback(() => {
    return stopRecording();
  }, [stopRecording]);

  const handleOfflineMeetingDraftChange = useCallback((value: string) => {
    setOfflineMeetingDraft((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        text: value,
      };
    });
    setOfflineMeetingDraftError('');
  }, []);

  const handleOfflineMeetingDraftNoteNameChange = useCallback((value: string) => {
    setOfflineMeetingDraft((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        noteNameInput: value,
      };
    });
    setOfflineMeetingDraftError('');
  }, []);

  const handleCancelOfflineMeetingDraft = useCallback(() => {
    if (savingOfflineMeetingDraft) {
      return;
    }
    setOfflineMeetingDraft(null);
    setOfflineMeetingDraftError('');
  }, [savingOfflineMeetingDraft]);

  const handleSaveOfflineMeetingDraft = useCallback(async () => {
    if (!selectedClientId || !offlineMeetingDraft || savingOfflineMeetingDraft) {
      return;
    }
    if (offlineMeetingDraft.clientId !== selectedClientId) {
      setOfflineMeetingDraft(null);
      setOfflineMeetingDraftError('');
      return;
    }

    const finalTranscript = offlineMeetingDraft.text;
    const finalNoteName = offlineMeetingDraft.noteNameInput.trim() || offlineMeetingDraft.defaultNoteName;
    if (!finalTranscript.trim()) {
      setOfflineMeetingDraftError('请先确认并填写整理后的内容。');
      return;
    }

    const draftClientId = offlineMeetingDraft.clientId;
    const draftNoteId = offlineMeetingDraft.noteId;
    setSavingOfflineMeetingDraft(true);
    setOfflineMeetingDraftError('');

    try {
      const { note } = await saveOfflineMeetingNote(
        draftClientId,
        finalNoteName,
        finalTranscript,
        draftNoteId,
      );
      setOfflineMeetingDraft(null);
      setOfflineMeetingDraftError('');
      if (selectedClientIdRef.current === draftClientId) {
        const savedNoteIndex: VoiceMemo = {
          id: note.noteId,
          clientId: draftClientId,
          noteName: note.noteName,
          createdAt: note.createdAt,
        };
        const savedNoteDetail: VoiceMemoDetail = {
          ...savedNoteIndex,
          transcript: note.transcript,
        };
        voiceMemoDetailsRef.current[note.noteId] = savedNoteDetail;
        setVoiceMemos((previous) => [
          ...previous.filter((item) => item.id !== note.noteId),
          savedNoteIndex,
        ]);
        setVoiceMemoBrowserOpen(true);
        setSelectedVoiceMemoId(note.noteId);
        setSelectedVoiceMemoDetail(savedNoteDetail);
        setVoiceMemoDetailError('');
        setRecordingSuccessToken((value) => value + 1);
      }
    } catch (error) {
      setOfflineMeetingDraftError(error instanceof Error ? error.message : '保存失败，请稍后重试。');
    } finally {
      setSavingOfflineMeetingDraft(false);
    }
  }, [offlineMeetingDraft, savingOfflineMeetingDraft, selectedClientId]);

  const handleToggleVoiceMemoBrowser = useCallback(() => {
    setVoiceMemoBrowserOpen((current) => !current);
    setVoiceMemoError('');
  }, []);

  const handleOpenLogs = useCallback(() => {
    setShowLogViewer(true);
  }, []);

  const handleClearLogs = useCallback(() => {
    clearLogs();
  }, [clearLogs]);

  const handleOpenDeleteDialog = useCallback((clientId: string) => {
    setDeleteError('');
    setDeleteCandidateId(clientId);
  }, []);

  const handleCloseDeleteDialog = useCallback(() => {
    if (deletingClient) {
      return;
    }
    setDeleteCandidateId(null);
    setDeleteError('');
  }, [deletingClient]);

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteCandidateId || deletingClient || !nanobot.backendConnected) {
      return;
    }

    const targetId = deleteCandidateId;
    const nextClientId = selectedClientId === targetId ? pickNextClientId(targetId) : null;

    setDeletingClient(true);
    setDeleteError('');

    try {
      await nanobot.deleteClient(targetId);
      setDeleteCandidateId(null);

      if (selectedClientId === targetId) {
        setEditingDraftContent(null);
        setEditingDraftId(null);
        setSelectedClientId(nextClientId);
        if (nextClientId) {
          void nanobot.loadMessages(nextClientId);
        }
      }
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setDeletingClient(false);
    }
  }, [deleteCandidateId, deletingClient, nanobot, pickNextClientId, selectedClientId]);

  // Render Login Page if not authenticated
  if (!isAuthenticated) {
    return <LoginPage onLogin={handleLogin} error={loginError} />;
  }

  const deleteTargetClient = deleteCandidateId
    ? clients.find((client) => client.id === deleteCandidateId) || null
    : null;

  return (
    <div className="relative h-screen w-screen flex flex-col bg-light-gray font-sans antialiased overflow-hidden">
      {/* Top Navigation - Fixed, Desktop App Style */}
      <header className="h-14 bg-white/95 backdrop-blur-md border-b border-border-light shadow-header flex items-center justify-between px-5 flex-shrink-0 relative z-10">
        <div className="flex items-center gap-3">
          {/* Mobile Menu Toggle */}
          <button
            onClick={() => setLeftSidebarOpen(!leftSidebarOpen)}
            className="lg:hidden p-2 hover:bg-surface rounded-subtle transition-colors"
          >
            {leftSidebarOpen ? (
              <X className="w-5 h-5 text-deep-slate" strokeWidth={1.5} />
            ) : (
              <Menu className="w-5 h-5 text-deep-slate" strokeWidth={1.5} />
            )}
          </button>

          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center shadow-sm">
            <Shield className="w-[18px] h-[18px] text-white" strokeWidth={1.5} />
          </div>
          <h1 className="text-[15px] font-bold text-deep-slate tracking-tight hidden sm:block">
            InsureAI <span className="text-gradient-brand">销售助手</span>
          </h1>
        </div>

        <div className="flex items-center gap-2 md:gap-4">
          {/* Backend Connection Status */}
          <div className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-semibold rounded-full border transition-all ${
            nanobot.backendConnected
              ? 'bg-success/8 text-success border-success/20'
              : 'bg-warning/10 text-warning border-warning/20'
          }`}>
            {nanobot.backendConnected ? (
              <Wifi className="w-3 h-3" strokeWidth={2} />
            ) : (
              <WifiOff className="w-3 h-3" strokeWidth={2} />
            )}
            <span className="hidden md:inline">
              {nanobot.backendConnected ? 'Nanobot 已连接' : '正在连接后台'}
            </span>
          </div>

          {/* Log Viewer Button */}
          <button
            onClick={handleOpenLogs}
            className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-deep-trust hover:bg-deep-trust/[0.06] rounded-lg transition-all btn-press"
          >
            <FileText className="w-4 h-4" strokeWidth={1.5} />
            <span className="hidden sm:inline">日志</span>
            {logs.length > 0 && (
              <span className="px-1.5 py-0.5 text-[10px] bg-deep-trust text-white rounded-full font-semibold min-w-[20px] text-center">
                {logs.length > 99 ? '99+' : logs.length}
              </span>
            )}
          </button>

          {/* Add Reply Target Button */}
          <button
            onClick={() => setShowAddTarget(true)}
            className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-deep-trust hover:bg-deep-trust/[0.06] rounded-lg transition-all btn-press"
            title="添加回复目标"
          >
            <UserPlus className="w-4 h-4" strokeWidth={1.5} />
            <span className="hidden sm:inline">添加目标</span>
          </button>

          <button
            onClick={() => setShowBroadcastModal(true)}
            className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-deep-trust hover:bg-deep-trust/[0.06] rounded-lg transition-all btn-press"
          >
            <Radio className="w-4 h-4" strokeWidth={1.5} />
            <span className="hidden sm:inline">广播</span>
          </button>

          {/* User Info */}
          <div className="flex items-center gap-2.5 pl-3 md:pl-4 border-l border-border-light">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-warm-navy to-deep-trust flex items-center justify-center text-white text-sm font-semibold ring-2 ring-deep-trust/10">
              {currentUser?.name.charAt(0).toUpperCase()}
            </div>
            <span className="text-sm font-medium text-deep-slate hidden md:block max-w-[100px] truncate">
              {currentUser?.name}
            </span>
            <button
              onClick={handleLogout}
              className="p-2 hover:bg-safety-red/[0.06] rounded-lg transition-all text-medium-gray hover:text-safety-red"
              title="退出登录"
            >
              <LogOut className="w-4 h-4" strokeWidth={1.5} />
            </button>
          </div>
        </div>
      </header>

      {nanobot.whatsappBridgeError && !showBootstrapOverlay && (
        <FloatingStatusNotice
          title="Bridge 异常"
          message={nanobot.whatsappBridgeMessage || 'Bridge 未就绪，历史同步暂时不可用。'}
          severity="error"
          action={{ label: '重启 Bridge', onClick: nanobot.restartBridge }}
        />
      )}

      {!nanobot.whatsappBridgeError && !nanobot.whatsappSyncAvailable && !showBootstrapOverlay && (
        <FloatingStatusNotice
          title="历史同步未准备"
          message={nanobot.whatsappSyncMessage || '当前环境尚未准备 WhatsApp 历史同步。'}
          severity="warning"
        />
      )}

      {showBootstrapOverlay && (
        <GatewayBootstrapOverlay
          progress={nanobot.gatewayProgress}
          title={nanobot.whatsappAuthRequired ? '等待 WhatsApp 重新认证' : '正在同步真实会话数据'}
          description={nanobot.whatsappAuthRequired
            ? (nanobot.whatsappAuthMessage || 'You need to login again to the whatsapp web browser')
            : (nanobot.gatewayMessage || '正在读取客户列表与聊天内容...')}
          authRequired={nanobot.whatsappAuthRequired}
          qrValue={nanobot.whatsappAuthQr}
          authMessage={nanobot.whatsappAuthMessage}
        />
      )}

      {/* Main Content */}
      <main className="flex-1 flex overflow-hidden relative">
        {/* Left Sidebar - Client List */}
        <div 
          className={`absolute lg:relative z-20 h-full transition-all duration-300 ease-out ${
            leftSidebarOpen 
              ? 'translate-x-0' 
              : '-translate-x-full lg:translate-x-0 lg:w-0 lg:opacity-0 lg:overflow-hidden'
          }`}
        >
          <ClientList
            clients={clients}
            selectedClientId={selectedClientId}
            onSelectClient={handleSelectClient}
            onToggleAutoDraft={handleToggleAutoDraft}
            onRequestDeleteClient={handleOpenDeleteDialog}
          />
        </div>

        {/* Left Sidebar Toggle (Desktop) */}
        <button
          onClick={() => setLeftSidebarOpen(!leftSidebarOpen)}
          className="hidden lg:flex absolute left-0 top-1/2 -translate-y-1/2 z-30 w-5 h-12 bg-white border border-border-light rounded-r-lg items-center justify-center shadow-card hover:bg-surface hover:shadow-card-hover transition-all"
          style={{ marginLeft: leftSidebarOpen ? '320px' : '0' }}
        >
          {leftSidebarOpen ? (
            <ChevronLeft className="w-3 h-3 text-medium-gray" strokeWidth={2} />
          ) : (
            <ChevronRight className="w-3 h-3 text-medium-gray" strokeWidth={2} />
          )}
        </button>

        {/* Overlay for mobile when sidebar is open */}
        {leftSidebarOpen && isMobile && (
          <div 
            className="absolute inset-0 bg-black/20 z-10 lg:hidden"
            onClick={() => setLeftSidebarOpen(false)}
          />
        )}

        {/* Center - Message Center */}
        <div className="flex-1 flex flex-col min-w-0 bg-white">
          {/* Message Header */}
          {selectedClient ? (
            <div className="h-14 border-b border-border-light bg-white/80 backdrop-blur-sm flex items-center justify-between px-5 flex-shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <div className="relative flex-shrink-0">
                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center text-white text-sm font-semibold ring-2 ring-deep-trust/10">
                    {selectedClient.name.charAt(0)}
                  </div>
                  <span
                    className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-white ${
                      selectedClient.status === 'online' ? 'bg-success' : 'bg-gray-400'
                    }`}
                  />
                </div>
                <div className="min-w-0">
                  <h2 className="text-sm font-bold text-deep-slate truncate tracking-tight">
                    {selectedClient.name.charAt(0)}**
                  </h2>
                  <p className="text-[11px] text-medium-gray">
                    {selectedClient.status === 'online' ? '在线' : '离线'}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {selectedClient.autoDraftEnabled && (
                  <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium bg-success/8 text-success rounded-full border border-success/15">
                    <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse"></span>
                    AI自动草稿
                  </span>
                )}
                {/* Mobile toggle right panel */}
                <button
                  onClick={() => setRightPanelOpen(!rightPanelOpen)}
                  className="xl:hidden p-2 hover:bg-surface rounded-subtle transition-colors"
                >
                  <Menu className="w-4 h-4 text-medium-gray" strokeWidth={1.5} />
                </button>
              </div>
            </div>
          ) : (
            <div className="h-14 border-b border-border-light flex items-center px-4">
              <p className="text-sm text-medium-gray">选择客户开始对话</p>
            </div>
          )}

          {/* Message Thread */}
          {selectedClient ? (
            <MessageThread
              clientId={selectedClient.id}
              clientName={selectedClient.name}
              isAILoading={!!aiLoading?.isGenerating || isClientLoading}
              reloadToken={nanobot.messageReloadToken}
              onTranscriptLoaded={nanobot.markMessagesLoaded}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <MessageSquare className="w-12 h-12 text-medium-gray/30 mx-auto mb-3" strokeWidth={1.5} />
                <p className="text-sm text-medium-gray">从左侧选择客户开始对话</p>
              </div>
            </div>
          )}

          {/* Message Input */}
          {selectedClient && (
            <MessageInput
              onSendMessage={handleSendMessage}
              onRequestAI={handleRequestAI}
              aiLoading={aiLoading}
              broadcastMode={broadcastMode}
              onToggleBroadcast={handleToggleBroadcast}
              draftContent={editingDraftContent}
              onDraftConsumed={() => setEditingDraftContent(null)}
            />
          )}
        </div>

        {/* Right Panel - Client Profile */}
        <div 
          className={`absolute xl:relative right-0 z-20 h-full transition-all duration-300 ease-out ${
            rightPanelOpen 
              ? 'translate-x-0' 
              : 'translate-x-full xl:translate-x-0 xl:w-0 xl:opacity-0 xl:overflow-hidden'
          }`}
        >
          <ClientProfile
            client={selectedClient}
            voiceMemos={voiceMemos}
            voiceMemoBrowserOpen={voiceMemoBrowserOpen}
            voiceMemoLoading={voiceMemoLoading}
            voiceMemoError={voiceMemoError}
            selectedVoiceMemoId={selectedVoiceMemoId}
            selectedVoiceMemoDetail={selectedVoiceMemoDetail}
            selectedVoiceMemoLoading={voiceMemoDetailLoading}
            selectedVoiceMemoError={voiceMemoDetailError}
            recordingState={recordingState}
            recordingTime={recordingTime}
            recordingError={recordingError}
            recordingSuccessToken={recordingSuccessToken}
            draftTranscript={
              offlineMeetingDraft && offlineMeetingDraft.clientId === selectedClientId
                ? offlineMeetingDraft.text
                : null
            }
            draftNoteName={
              offlineMeetingDraft && offlineMeetingDraft.clientId === selectedClientId
                ? offlineMeetingDraft.noteNameInput
                : ''
            }
            draftError={offlineMeetingDraftError}
            isSavingDraft={savingOfflineMeetingDraft}
            onDraftNoteNameChange={handleOfflineMeetingDraftNoteNameChange}
            onDraftChange={handleOfflineMeetingDraftChange}
            onSaveDraft={handleSaveOfflineMeetingDraft}
            onCancelDraft={handleCancelOfflineMeetingDraft}
            onToggleVoiceMemoBrowser={handleToggleVoiceMemoBrowser}
            onSelectVoiceMemo={handleSelectVoiceMemo}
            onStartRecording={handleStartRecording}
            onStopRecording={handleStopRecording}
            onSyncWhatsApp={handleSyncWhatsApp}
          />
        </div>

        {/* Overlay for tablet when right panel is open */}
        {rightPanelOpen && !isMobile && window.innerWidth < 1280 && (
          <div 
            className="absolute inset-0 bg-black/20 z-10 xl:hidden"
            onClick={() => setRightPanelOpen(false)}
          />
        )}
      </main>

      {/* Broadcast Modal */}
      <BroadcastModal
        isOpen={showBroadcastModal}
        onClose={() => setShowBroadcastModal(false)}
        clients={clients}
        onSendBroadcast={handleSendBroadcast}
      />

      {/* Add Reply Target Modal */}
      <AddReplyTargetModal
        isOpen={showAddTarget}
        onClose={() => setShowAddTarget(false)}
        onAdd={nanobot.addReplyTarget}
        backendConnected={nanobot.backendConnected}
      />

      {/* Log Viewer Modal */}
      <LogViewer
        logs={logs}
        isOpen={showLogViewer}
        onClose={() => setShowLogViewer(false)}
        onDownload={downloadLogs}
        onClear={handleClearLogs}
        clients={clients.map(c => ({ id: c.id, name: c.name }))}
      />

      {deleteTargetClient && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-deep-slate/30 px-4 backdrop-blur-[2px]">
          <div className="w-full max-w-md rounded-2xl border border-border-light bg-white p-6 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-2xl bg-safety-red/[0.08] text-safety-red">
                <AlertTriangle className="h-5 w-5" strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <h3 className="text-lg font-bold text-deep-slate tracking-tight">删除聊天记录</h3>
                <p className="mt-1 text-sm text-medium-gray leading-6">
                  这会永久删除 {deleteTargetClient.name.charAt(0)}** 的会话文件，并把该客户从当前回复目标列表中移除。
                </p>
              </div>
            </div>

            {deleteError && (
              <p className="mt-4 rounded-xl border border-safety-red/15 bg-safety-red/[0.05] px-3 py-2 text-sm text-safety-red">
                {deleteError}
              </p>
            )}

            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                onClick={handleCloseDeleteDialog}
                disabled={deletingClient}
                className="rounded-xl border border-border-light px-4 py-2 text-sm font-medium text-medium-gray transition-colors hover:bg-surface disabled:cursor-not-allowed disabled:opacity-60"
              >
                取消
              </button>
              <button
                onClick={handleConfirmDelete}
                disabled={deletingClient}
                className="rounded-xl bg-safety-red px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-safety-red/90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {deletingClient ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
