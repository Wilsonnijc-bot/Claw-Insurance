import React, { useState } from 'react';
import { User, Phone, Mail, Calendar, Tag, Clock, MessageCircle, RefreshCw, Check, Hash } from 'lucide-react';
import type { Client, VoiceMemo } from '../../types';
import { VoiceRecorder } from './VoiceRecorder';

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
    <div className="mt-4">
      <button
        onClick={handleSync}
        disabled={syncing}
        title="同步 WhatsApp 聊天记录"
        className={`w-full flex items-center justify-center gap-2 px-3 py-2.5 text-xs font-semibold rounded-lg border transition-all btn-press ${
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
  recordingState: 'idle' | 'recording' | 'processing';
  onStartRecording: () => void;
  onStopRecording: () => void;
  onSyncWhatsApp: (clientId: string) => Promise<void>;
}

export const ClientProfile: React.FC<ClientProfileProps> = ({
  client,
  voiceMemos,
  recordingState,
  onStartRecording,
  onStopRecording,
  onSyncWhatsApp,
}) => {
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

  // Build masked phone: show first 3 + last 4 digits, mask the rest
  const rawPhone = client.clientPhone || client.id || '';
  const maskedPhone = rawPhone.length > 7
    ? rawPhone.slice(0, 3) + '****' + rawPhone.slice(-4)
    : rawPhone.length > 3
    ? rawPhone.slice(0, 2) + '**' + rawPhone.slice(-1)
    : rawPhone || '—';

  // Format created-at date
  const formatCreatedAt = (iso: string | undefined): string => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      return `${d.getFullYear()}年${d.getMonth() + 1}月加入`;
    } catch {
      return '';
    }
  };
  const createdLabel = formatCreatedAt(client.createdAt);

  // Display name: prefer clientDisplayName > label > pushName > name
  const displayName = client.clientDisplayName || client.label || client.pushName || client.name;

  return (
    <div className="w-[400px] flex flex-col h-full bg-white border-l border-border-light">
      {/* Header */}
      <div className="p-6 border-b border-border-light bg-gradient-to-b from-blue-50/30 to-white">
        <div className="flex flex-col items-center">
          {/* Large Avatar */}
          <div className="relative mb-4">
            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center text-white text-xl font-bold ring-[3px] ring-white shadow-elevated">
              {client.name.charAt(0)}
            </div>
            <span
              className={`absolute bottom-0 right-0 w-4 h-4 rounded-full border-[2.5px] border-white shadow-sm ${
                client.status === 'online' ? 'bg-success' : 'bg-gray-300'
              }`}
            />
          </div>

          {/* Name */}
          <h2 className="text-lg font-bold text-deep-slate tracking-tight" title={`${client.name} (隐私保护)`}>
            {maskedName}
          </h2>
          <p className="text-[11px] text-medium-gray mt-1 flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${client.status === 'online' ? 'bg-success' : 'bg-gray-300'}`}></span>
            {client.status === 'online' ? '在线' : '离线'}
          </p>
        </div>

        {/* Quick Info */}
        <div className="mt-6 space-y-3">
          <div className="flex items-center gap-3 text-sm group">
            <div className="w-7 h-7 rounded-lg bg-deep-trust/[0.06] flex items-center justify-center">
              <Phone className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.5} />
            </div>
            <span className="text-deep-slate font-medium">{maskedPhone}</span>
          </div>
          {displayName && displayName !== maskedName && (
            <div className="flex items-center gap-3 text-sm group">
              <div className="w-7 h-7 rounded-lg bg-deep-trust/[0.06] flex items-center justify-center">
                <User className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.5} />
              </div>
              <span className="text-deep-slate font-medium">{displayName}</span>
            </div>
          )}
          {client.clientChatId && (
            <div className="flex items-center gap-3 text-sm group">
              <div className="w-7 h-7 rounded-lg bg-deep-trust/[0.06] flex items-center justify-center">
                <Mail className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.5} />
              </div>
              <span className="text-deep-slate font-medium text-xs truncate max-w-[200px]" title={client.clientChatId}>
                {client.clientChatId}
              </span>
            </div>
          )}
          {createdLabel && (
            <div className="flex items-center gap-3 text-sm group">
              <div className="w-7 h-7 rounded-lg bg-deep-trust/[0.06] flex items-center justify-center">
                <Calendar className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.5} />
              </div>
              <span className="text-deep-slate font-medium">{createdLabel}</span>
            </div>
          )}
          {typeof client.messageCount === 'number' && (
            <div className="flex items-center gap-3 text-sm group">
              <div className="w-7 h-7 rounded-lg bg-deep-trust/[0.06] flex items-center justify-center">
                <Hash className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.5} />
              </div>
              <span className="text-deep-slate font-medium">{client.messageCount} 条消息</span>
            </div>
          )}
        </div>

        {/* Tags */}
        <div className="mt-4">
          <div className="flex items-center gap-1.5 mb-2">
            <Tag className="w-3.5 h-3.5 text-deep-trust/60" strokeWidth={1.5} />
            <span className="text-[11px] font-semibold text-medium-gray uppercase tracking-wider">客户标签</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {client.tags.map((tag) => (
              <span
                key={tag}
                className="px-2.5 py-1 text-xs font-medium bg-surface-warm text-deep-slate rounded-lg border border-border-subtle hover:border-deep-trust/20 transition-colors"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>

        {/* WhatsApp Sync */}
        <WhatsAppSyncButton
          clientId={client.id}
          onSync={onSyncWhatsApp}
        />
      </div>

      {/* Voice Recorder Section */}
      <div className="p-4 border-b border-border-light">
        <VoiceRecorder
          recordingState={recordingState}
          onStartRecording={onStartRecording}
          onStopRecording={onStopRecording}
        />
      </div>

      {/* Voice Memos List */}
      <div className="flex-1 overflow-y-auto p-4">
        <div className="flex items-center gap-1.5 mb-4">
          <div className="w-5 h-5 rounded-md bg-deep-trust/[0.06] flex items-center justify-center">
            <Clock className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.5} />
          </div>
          <span className="text-sm font-bold text-deep-slate">最近联系记录</span>
          <span className="text-[11px] text-medium-gray bg-surface-warm px-1.5 py-0.5 rounded-md font-medium">{voiceMemos.length}</span>
        </div>

        {voiceMemos.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-xs text-medium-gray">暂无语音记录</p>
          </div>
        ) : (
          <div className="space-y-3">
            {voiceMemos.map((memo) => (
              <div
                key={memo.id}
                className="p-3 bg-surface-warm rounded-xl border border-border-subtle hover:border-deep-trust/20 hover:shadow-card transition-all cursor-pointer group"
              >
                {/* Audio Bar */}
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-deep-trust/8 flex items-center justify-center group-hover:bg-deep-trust/15 transition-all shadow-sm">
                    <div className="w-0 h-0 border-t-[5px] border-t-transparent border-l-[8px] border-l-deep-trust border-b-[5px] border-b-transparent ml-0.5" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-1">
                      {[...Array(20)].map((_, i) => (
                        <div
                          key={i}
                          className="w-1 bg-deep-trust/30 rounded-full"
                          style={{
                            height: `${Math.max(4, Math.random() * 20)}px`,
                          }}
                        />
                      ))}
                    </div>
                  </div>
                  <span className="text-xs text-medium-gray font-mono">{memo.duration}</span>
                </div>

                {/* Transcript */}
                {memo.transcript && (
                  <p className="mt-2 text-xs text-medium-gray line-clamp-2">
                    {memo.transcript}
                  </p>
                )}

                {/* Timestamp */}
                <p className="mt-1 text-[10px] text-medium-gray/60">{memo.timestamp}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default ClientProfile;
