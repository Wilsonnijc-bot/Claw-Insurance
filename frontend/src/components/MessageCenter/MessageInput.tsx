import React, { useState, useRef, useEffect } from 'react';
import { Send, Sparkles, Radio } from 'lucide-react';
import type { LoadingState } from '../../types';

interface MessageInputProps {
  onSendMessage: (content: string) => Promise<void>;
  onRequestAI: () => void;
  aiLoading: LoadingState | null;
  broadcastMode: boolean;
  onToggleBroadcast: () => void;
  /** When set, pre-fills the input with draft content for editing. */
  draftContent?: string | null;
  /** Called after the draft content has been consumed (loaded into input). */
  onDraftConsumed?: () => void;
}

export const MessageInput: React.FC<MessageInputProps> = ({
  onSendMessage,
  onRequestAI,
  aiLoading,
  broadcastMode,
  onToggleBroadcast,
  draftContent,
  onDraftConsumed,
}) => {
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Load draft content into the input when provided
  useEffect(() => {
    if (draftContent) {
      setMessage(draftContent);
      onDraftConsumed?.();
      // Focus the textarea so the user can edit immediately
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [draftContent, onDraftConsumed]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [message]);

  const handleSend = async () => {
    const trimmed = message.trim();
    if (!trimmed || sending) {
      return;
    }

    setSending(true);
    try {
      await onSendMessage(trimmed);
      setMessage('');
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void handleSend();
    }
  };

  return (
    <div className="p-4 bg-white border-t border-border-light">
      {/* Broadcast Mode Toggle */}
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={onToggleBroadcast}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold transition-all ${
            broadcastMode
              ? 'bg-warning/10 text-warning border border-warning/20 shadow-sm'
              : 'bg-surface-warm text-medium-gray border border-border-subtle hover:bg-gray-100 hover:border-border-light'
          }`}
        >
          <Radio className="w-3.5 h-3.5" strokeWidth={1.5} />
          {broadcastMode ? '广播模式已开启' : '广播模式'}
        </button>
        <span className="text-[11px] text-medium-gray/60 font-medium">Cmd + Enter 发送</span>
      </div>

      {/* Input Area */}
      <div className="relative">
        <textarea
          ref={textareaRef}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={broadcastMode ? '输入广播消息...' : '输入消息或请求AI建议...'}
          disabled={aiLoading?.isGenerating || sending}
          className="w-full px-4 py-3 pr-24 bg-surface-warm border border-border-subtle rounded-xl resize-none focus:outline-none focus:ring-2 focus:ring-deep-trust/15 focus:border-deep-trust/40 focus:bg-white transition-all text-sm text-deep-slate placeholder:text-medium-gray/50 disabled:opacity-50"
          rows={1}
        />

        {/* Action Buttons */}
        <div className="absolute right-2 bottom-2 flex items-center gap-1.5">
          {/* AI Request Button */}
          <button
            onClick={onRequestAI}
            disabled={aiLoading?.isGenerating || sending}
            className="flex items-center gap-1 px-3 py-1.5 bg-ai-blue text-deep-trust text-xs font-semibold rounded-lg border border-ai-blue-border hover:bg-ai-blue-border/30 hover:shadow-sm transition-all disabled:opacity-50 btn-press"
            title="请求AI建议"
          >
            <Sparkles className="w-3.5 h-3.5" strokeWidth={1.5} />
            AI
          </button>

          {/* Send Button */}
          <button
            onClick={() => void handleSend()}
            disabled={!message.trim() || aiLoading?.isGenerating || sending}
            className="flex items-center gap-1 px-3 py-1.5 bg-deep-trust text-white text-xs font-semibold rounded-lg hover:bg-deep-trust/90 hover:shadow-md hover:shadow-deep-trust/15 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:shadow-none btn-press"
          >
            <Send className="w-3.5 h-3.5" strokeWidth={1.5} />
            {sending ? '发送中...' : '发送'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default MessageInput;
