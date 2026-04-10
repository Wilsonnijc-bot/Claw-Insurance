import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  User,
  Phone,
  Calendar,
  Clock,
  MessageCircle,
  RefreshCw,
  Check,
  FileText,
  X,
} from 'lucide-react';
import type { Client, VoiceMemo, VoiceMemoDetail } from '../../types';
import { VoiceRecorder } from './VoiceRecorder';

const PANEL_TRIGGER_BUTTON_BASE =
  'w-full flex h-11 items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-xs font-semibold transition-all btn-press';

// WhatsApp Sync Button Component
const WhatsAppSyncButton: React.FC<{
  clientId: string;
  onSync: (clientId: string) => Promise<void>;
}> = ({ clientId, onSync }) => {
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState(false);
  const [error, setError] = useState('');
  const [syncProgress, setSyncProgress] = useState(0);

  const handleSync = async () => {
    if (syncing || synced) return;
    setSyncing(true);
    setError('');
    setSyncProgress(10);
    const progressTimer = window.setInterval(() => {
      setSyncProgress((prev) => Math.min(prev + (prev < 55 ? 9 : 4), 88));
    }, 800);
    try {
      await onSync(clientId);
      window.clearInterval(progressTimer);
      setSyncProgress(100);
      setSynced(true);
      window.setTimeout(() => {
        setSynced(false);
        setSyncProgress(0);
      }, 3000);
    } catch (err) {
      window.clearInterval(progressTimer);
      setSyncProgress(0);
      setError(err instanceof Error ? err.message : '同步失败');
    } finally {
      window.clearInterval(progressTimer);
      setSyncing(false);
    }
  };

  return (
    <div className="space-y-2">
      <button
        onClick={handleSync}
        disabled={syncing}
        title="同步 WhatsApp 聊天记录"
        className={`${PANEL_TRIGGER_BUTTON_BASE} ${
          synced
            ? 'bg-success/8 text-success border-success/20'
            : syncing
            ? 'bg-surface-warm text-medium-gray border-border-subtle cursor-wait'
            : 'bg-white text-deep-slate border-border-subtle hover:border-deep-trust/30 hover:bg-surface-warm hover:shadow-sm'
        }`}
      >
        {synced ? (
          <>
            <Check className="w-3.5 h-3.5" strokeWidth={2} />
            同步完成
          </>
        ) : syncing ? (
          <>
            <RefreshCw className="w-3.5 h-3.5 animate-spin" strokeWidth={1.5} />
            正在同步...
          </>
        ) : (
          <>
            <MessageCircle className="w-3.5 h-3.5 text-[#25D366]" strokeWidth={1.5} />
            一键同步 WhatsApp 聊天记录
          </>
        )}
      </button>
      {syncing && (
        <div className="mt-2 overflow-hidden rounded-full bg-surface-warm">
          <div
            className="h-1.5 rounded-full bg-gradient-to-r from-warning via-deep-trust to-deep-trust transition-all duration-500 ease-out"
            style={{ width: `${syncProgress}%` }}
          />
        </div>
      )}
      {error && (
        <p className="mt-2 text-[11px] text-safety-red">
          {error}
        </p>
      )}
    </div>
  );
};

interface ClientProfileProps {
  client: Client | null;
  voiceMemos: VoiceMemo[];
  voiceMemoBrowserOpen: boolean;
  voiceMemoLoading: boolean;
  voiceMemoError?: string | null;
  selectedVoiceMemoId: string | null;
  selectedVoiceMemoDetail: VoiceMemoDetail | null;
  selectedVoiceMemoLoading: boolean;
  selectedVoiceMemoError?: string | null;
  recordingState: 'idle' | 'recording' | 'processing';
  recordingTime: number;
  recordingError?: string | null;
  recordingSuccessToken: number;
  draftTranscript: string | null;
  draftNoteName: string;
  draftError?: string | null;
  isSavingDraft: boolean;
  onDraftNoteNameChange: (value: string) => void;
  onDraftChange: (value: string) => void;
  onSaveDraft: () => void | Promise<void>;
  onCancelDraft: () => void;
  onToggleVoiceMemoBrowser: () => void;
  onSelectVoiceMemo: (noteId: string) => void | Promise<void>;
  onStartRecording: () => void | Promise<void>;
  onStopRecording: () => void | Promise<void>;
  onSyncWhatsApp: (clientId: string) => Promise<void>;
}

export const ClientProfile: React.FC<ClientProfileProps> = ({
  client,
  voiceMemos,
  voiceMemoBrowserOpen,
  voiceMemoLoading,
  voiceMemoError,
  selectedVoiceMemoId,
  selectedVoiceMemoDetail,
  selectedVoiceMemoLoading,
  selectedVoiceMemoError,
  recordingState,
  recordingTime,
  recordingError,
  recordingSuccessToken,
  draftTranscript,
  draftNoteName,
  draftError,
  isSavingDraft,
  onDraftNoteNameChange,
  onDraftChange,
  onSaveDraft,
  onCancelDraft,
  onToggleVoiceMemoBrowser,
  onSelectVoiceMemo,
  onStartRecording,
  onStopRecording,
  onSyncWhatsApp,
}) => {
  useEffect(() => {
    if (!voiceMemoBrowserOpen) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onToggleVoiceMemoBrowser();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [voiceMemoBrowserOpen, onToggleVoiceMemoBrowser]);

  if (!client) {
    return (
      <div className="w-[400px] flex flex-col h-full bg-white border-l border-border-light items-center justify-center">
        <div className="w-20 h-20 rounded-full bg-surface flex items-center justify-center mb-4 border border-border-subtle">
          <User className="w-10 h-10 text-medium-gray/30" strokeWidth={1.5} />
        </div>
        <p className="text-medium-gray text-sm">选择客户查看详情</p>
      </div>
    );
  }

  const maskedName = client.name.charAt(0) + '**';

  const rawPhone = client.clientPhone || client.id || '';
  const maskedPhone = rawPhone.length > 7
    ? rawPhone.slice(0, 3) + '****' + rawPhone.slice(-4)
    : rawPhone.length > 3
    ? rawPhone.slice(0, 2) + '**' + rawPhone.slice(-1)
    : rawPhone || '—';

  const formatCreatedAt = (iso: string | undefined): string => {
    if (!iso) return '';
    try {
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return '';
      return `${date.getFullYear()}年${date.getMonth() + 1}月加入`;
    } catch {
      return '';
    }
  };

  const formatClientDisplay = (value: string): string => {
    const normalized = value.trim();
    if (!normalized) {
      return '';
    }
    const digits = normalized.replace(/\D/g, '');
    if (digits.length === 11 && digits.startsWith('852')) {
      return `+852 ${digits.slice(3, 7)} ${digits.slice(7)}`;
    }
    if (normalized.startsWith('+')) {
      return normalized;
    }
    if (digits.length > 8) {
      return `+${digits}`;
    }
    return normalized;
  };

  const formatNoteCreatedAt = (iso: string): string => {
    try {
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) {
        return '时间待确认';
      }
      return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return '时间待确认';
    }
  };

  const createdLabel = formatCreatedAt(client.createdAt);
  const displayNameCandidate = (client.clientDisplayName || client.label || client.pushName || '').trim();
  const displayName = formatClientDisplay(displayNameCandidate);
  const hasDisplayName =
    !!displayName &&
    displayNameCandidate.replace(/\D/g, '') !== rawPhone.replace(/\D/g, '');
  const handleCloseVoiceMemoModal = () => {
    if (voiceMemoBrowserOpen) {
      onToggleVoiceMemoBrowser();
    }
  };

  return (
    <div className="w-[400px] flex flex-col h-full bg-white border-l border-border-light">
      <div className="border-b border-border-light bg-gradient-to-b from-blue-50/20 via-white to-white px-6 pb-5 pt-6">
        <div className="flex flex-col items-center">
          <div className="relative">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-deep-trust to-warm-navy text-xl font-bold text-white ring-[3px] ring-white shadow-elevated">
              {client.name.charAt(0)}
            </div>
          </div>

          <h2
            className="mt-4 text-lg font-bold tracking-tight text-deep-slate"
            title={`${client.name} (隐私保护)`}
          >
            {maskedName}
          </h2>

          <p className="mt-1.5 flex items-center gap-1.5 text-[11px] font-medium text-medium-gray">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                client.status === 'online' ? 'bg-success' : 'bg-gray-300'
              }`}
            />
            {client.status === 'online' ? '在线' : '离线'}
          </p>
        </div>

        <div className="mt-6 w-full border-t border-border-subtle/80 pt-5">
          <div className="w-full max-w-[318px] space-y-3.5 pr-4">
            <div className="flex items-center gap-3 text-sm">
              <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-deep-trust/[0.07] text-deep-trust">
                <Phone className="h-3.5 w-3.5" strokeWidth={1.6} />
              </div>
              <p className="min-w-0 text-sm font-medium tracking-tight text-deep-slate">
                {maskedPhone}
              </p>
            </div>

            {hasDisplayName && (
              <div className="flex items-center gap-3 text-sm">
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-deep-trust/[0.07] text-deep-trust">
                  <User className="h-3.5 w-3.5" strokeWidth={1.6} />
                </div>
                <p className="min-w-0 break-words text-sm font-medium tracking-tight text-deep-slate">
                  {displayName}
                </p>
              </div>
            )}

            {createdLabel && (
              <div className="flex items-center gap-3 text-sm">
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-deep-trust/[0.07] text-deep-trust">
                  <Calendar className="h-3.5 w-3.5" strokeWidth={1.6} />
                </div>
                <p className="min-w-0 text-sm font-medium tracking-tight text-deep-slate">
                  {createdLabel}
                </p>
              </div>
            )}
          </div>
        </div>

        <div className="mt-6">
          <WhatsAppSyncButton
            clientId={client.id}
            onSync={onSyncWhatsApp}
          />
        </div>
      </div>

      <div className="border-b border-border-light bg-white px-4 py-4">
        <div className="space-y-3">
          <VoiceRecorder
            recordingState={recordingState}
            recordingTime={recordingTime}
            error={recordingError}
            successToken={recordingSuccessToken}
            draftTranscript={draftTranscript}
            draftNoteName={draftNoteName}
            draftError={draftError}
            isSavingDraft={isSavingDraft}
            onDraftNoteNameChange={onDraftNoteNameChange}
            onDraftChange={onDraftChange}
            onSaveDraft={onSaveDraft}
            onCancelDraft={onCancelDraft}
            onStartRecording={onStartRecording}
            onStopRecording={onStopRecording}
          />

          <button
            onClick={onToggleVoiceMemoBrowser}
            className={`${PANEL_TRIGGER_BUTTON_BASE} bg-surface-warm text-deep-slate border-border-subtle hover:border-deep-trust/20 hover:bg-white hover:shadow-sm`}
            title="查看已保存笔记"
          >
            <FileText className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.6} />
            查看已保存笔记
          </button>
        </div>
      </div>

      <div className="flex-1 bg-white" />

      {voiceMemoBrowserOpen && typeof document !== 'undefined' && createPortal(
        <div className="fixed inset-0 z-[90] flex items-center justify-center px-4 py-6">
          <div
            className="absolute inset-0 bg-deep-slate/35 backdrop-blur-[3px]"
            onClick={handleCloseVoiceMemoModal}
          />

          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="saved-notes-title"
            className="relative flex max-h-[min(88vh,760px)] w-full max-w-4xl flex-col overflow-hidden rounded-[28px] border border-white/80 bg-white shadow-[0_32px_90px_-24px_rgba(15,23,42,0.45)] animate-fade-in"
          >
            <div className="border-b border-border-light bg-gradient-to-br from-white via-white to-surface-warm px-6 py-5">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-deep-trust/[0.08] text-deep-trust">
                      <FileText className="w-4.5 h-4.5" strokeWidth={1.6} />
                    </div>
                    <div>
                      <h3 id="saved-notes-title" className="text-lg font-bold tracking-tight text-deep-slate">
                        已保存笔记
                      </h3>
                      <p className="mt-1 text-xs text-medium-gray">
                        {voiceMemos.length} 条记录
                      </p>
                    </div>
                  </div>
                </div>

                <button
                  onClick={handleCloseVoiceMemoModal}
                  aria-label="关闭已保存笔记弹窗"
                  className="rounded-xl p-2 text-medium-gray transition-colors hover:bg-surface hover:text-deep-slate"
                >
                  <X className="w-5 h-5" strokeWidth={1.75} />
                </button>
              </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
              <div className="border-b border-border-light bg-surface-warm/40 lg:w-[320px] lg:border-b-0 lg:border-r">
                <div className="max-h-[260px] overflow-y-auto p-4 lg:max-h-none lg:h-full">
                  {voiceMemoLoading ? (
                    <p className="text-sm text-medium-gray">正在载入笔记目录...</p>
                  ) : voiceMemoError ? (
                    <p className="rounded-xl border border-safety-red/15 bg-safety-red/[0.05] px-4 py-3 text-sm text-safety-red">
                      {voiceMemoError}
                    </p>
                  ) : voiceMemos.length === 0 ? (
                    <p className="text-sm text-medium-gray">暂无线下会面补充</p>
                  ) : (
                    <div className="space-y-2">
                      {voiceMemos.map((memo) => {
                        const isSelected = memo.id === selectedVoiceMemoId;
                        return (
                          <button
                            key={memo.id}
                            onClick={() => void onSelectVoiceMemo(memo.id)}
                            className={`w-full rounded-xl border px-3 py-3 text-left transition-all ${
                              isSelected
                                ? 'border-deep-trust/35 bg-deep-trust/[0.06] shadow-sm'
                                : 'border-border-subtle bg-white hover:border-deep-trust/20 hover:bg-surface-warm'
                            }`}
                          >
                            <p className="text-sm font-semibold text-deep-slate break-words">
                              {memo.noteName}
                            </p>
                            <p className="mt-1 text-[11px] text-medium-gray">
                              {formatNoteCreatedAt(memo.createdAt)}
                            </p>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex min-h-[320px] flex-1 flex-col bg-white">
                <div className="border-b border-border-light px-6 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-deep-trust/[0.06] text-deep-trust">
                        <Clock className="w-4 h-4" strokeWidth={1.6} />
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-deep-slate">
                          {selectedVoiceMemoDetail?.noteName || '笔记内容'}
                        </p>
                        {!selectedVoiceMemoDetail && (
                          <p className="text-[11px] text-medium-gray">请选择一条笔记查看详细内容。</p>
                        )}
                      </div>
                    </div>
                    {selectedVoiceMemoDetail && (
                      <span className="text-[11px] text-medium-gray">
                        {formatNoteCreatedAt(selectedVoiceMemoDetail.createdAt)}
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto px-6 py-5">
                  {selectedVoiceMemoLoading ? (
                    <p className="text-sm text-medium-gray">正在载入笔记内容...</p>
                  ) : selectedVoiceMemoError ? (
                    <p className="rounded-xl border border-safety-red/15 bg-safety-red/[0.05] px-4 py-3 text-sm text-safety-red">
                      {selectedVoiceMemoError}
                    </p>
                  ) : selectedVoiceMemoDetail ? (
                    <p className="text-sm leading-7 text-deep-slate whitespace-pre-wrap">
                      {selectedVoiceMemoDetail.transcript}
                    </p>
                  ) : (
                    <p className="text-sm text-medium-gray">请选择一条笔记查看详细内容。</p>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
};

export default ClientProfile;
