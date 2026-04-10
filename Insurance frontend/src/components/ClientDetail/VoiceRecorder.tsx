import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, Loader2, Mic, Square, X } from 'lucide-react';

const PANEL_TRIGGER_BUTTON_BASE =
  'w-full flex h-11 items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-xs font-semibold transition-all btn-press';

interface VoiceRecorderProps {
  recordingState: 'idle' | 'recording' | 'processing';
  recordingTime: number;
  error?: string | null;
  successToken: number;
  draftTranscript: string | null;
  draftNoteName: string;
  draftError?: string | null;
  isSavingDraft: boolean;
  onDraftNoteNameChange: (value: string) => void;
  onDraftChange: (value: string) => void;
  onSaveDraft: () => void | Promise<void>;
  onCancelDraft: () => void;
  onStartRecording: () => void | Promise<void>;
  onStopRecording: () => void | Promise<void>;
}

export const VoiceRecorder: React.FC<VoiceRecorderProps> = ({
  recordingState,
  recordingTime,
  error,
  successToken,
  draftTranscript,
  draftNoteName,
  draftError,
  isSavingDraft,
  onDraftNoteNameChange,
  onDraftChange,
  onSaveDraft,
  onCancelDraft,
  onStartRecording,
  onStopRecording,
}) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const previousSuccessTokenRef = useRef(successToken);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const hasDraft = draftTranscript !== null;
  const normalizedDraft = draftTranscript ?? '';
  const normalizedDraftNoteName = draftNoteName ?? '';
  const canSaveDraft = normalizedDraft.trim().length > 0 && !isSavingDraft;
  const canDismissModal = recordingState === 'idle' && !hasDraft && !isSavingDraft;

  useEffect(() => {
    if (successToken <= previousSuccessTokenRef.current) {
      previousSuccessTokenRef.current = successToken;
      return;
    }

    setShowSuccess(true);
    const successTimer = window.setTimeout(() => {
      setShowSuccess(false);
    }, 2000);
    const closeTimer = window.setTimeout(() => {
      setIsModalOpen(false);
    }, 700);

    previousSuccessTokenRef.current = successToken;
    return () => {
      window.clearTimeout(successTimer);
      window.clearTimeout(closeTimer);
    };
  }, [successToken]);

  useEffect(() => {
    if (recordingState === 'recording' || recordingState === 'processing' || hasDraft) {
      setIsModalOpen(true);
    }
  }, [hasDraft, recordingState]);

  useEffect(() => {
    if (!isModalOpen || !hasDraft) {
      return;
    }
    textareaRef.current?.focus();
  }, [hasDraft, isModalOpen]);

  useEffect(() => {
    if (!isModalOpen || !canDismissModal) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsModalOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [canDismissModal, isModalOpen]);

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const handlePrimaryAction = () => {
    if (hasDraft || recordingState === 'processing') {
      return;
    }
    if (recordingState === 'idle') {
      void onStartRecording();
    } else if (recordingState === 'recording') {
      void onStopRecording();
    }
  };

  const handleLaunchFlow = () => {
    setIsModalOpen(true);
    if (hasDraft || recordingState !== 'idle') {
      return;
    }
    void onStartRecording();
  };

  const handleCloseModal = () => {
    if (!canDismissModal) {
      return;
    }
    setIsModalOpen(false);
  };

  const handleCancelDraft = () => {
    if (isSavingDraft) {
      return;
    }
    onCancelDraft();
    setIsModalOpen(false);
  };

  const waveHeights = useMemo(
    () => [16, 28, 20, 34, 18, 30, 22, 36, 24, 32, 16, 28, 20, 34, 18, 30, 22, 36, 24, 32],
    [],
  );

  const launcherButtonLabel = '线下会面纪要录音';

  const launcherStatus = draftError || error
    ? (draftError || error)
    : hasDraft
    ? '转写草稿待确认'
    : recordingState === 'processing'
    ? '整理中'
    : recordingState === 'recording'
    ? `录音中 ${formatTime(recordingTime)}`
    : showSuccess
    ? '已保存'
    : '';

  const steps = [
    { key: 'record', label: '录音' },
    { key: 'transcribe', label: '整理' },
    { key: 'save', label: '保存' },
  ] as const;

  const getStepState = (index: number) => {
    if (hasDraft) {
      return index < 2 ? 'complete' : 'current';
    }
    if (recordingState === 'processing') {
      return index === 0 ? 'complete' : index === 1 ? 'current' : 'upcoming';
    }
    return index === 0 ? 'current' : 'upcoming';
  };

  return (
    <>
      <div className="space-y-2">
        <button
          onClick={handleLaunchFlow}
          disabled={recordingState === 'processing'}
          className={`${PANEL_TRIGGER_BUTTON_BASE} ${
            recordingState === 'processing'
              ? 'cursor-wait border-deep-trust/20 bg-deep-trust/[0.08] text-deep-trust'
              : 'border-deep-trust bg-deep-trust text-white hover:border-warm-navy hover:bg-warm-navy hover:shadow-sm'
          }`}
          title="线下会面纪要录音"
        >
          <span className="inline-flex items-center justify-center gap-2">
            {recordingState === 'processing' ? (
              <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.75} />
            ) : showSuccess ? (
              <Check className="w-4 h-4" strokeWidth={2} />
            ) : (
              <Mic className="w-4 h-4" strokeWidth={1.75} />
            )}
            {launcherButtonLabel}
          </span>
        </button>

        {launcherStatus && (
          <p className={`text-xs leading-5 ${
            draftError || error ? 'text-safety-red' : 'text-medium-gray'
          }`}>
            {launcherStatus}
          </p>
        )}
      </div>

      {isModalOpen && typeof document !== 'undefined' && createPortal(
        <div className="fixed inset-0 z-[90] flex items-center justify-center px-4 py-6">
          <div
            className={`absolute inset-0 bg-deep-slate/35 backdrop-blur-[3px] transition-opacity ${canDismissModal ? 'cursor-pointer' : 'cursor-default'}`}
            onClick={handleCloseModal}
          />

          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="offline-meeting-recording-title"
            className="relative flex max-h-[min(88vh,760px)] w-full max-w-2xl flex-col overflow-hidden rounded-[28px] border border-white/80 bg-white shadow-[0_32px_90px_-24px_rgba(15,23,42,0.45)] animate-fade-in"
          >
            <div className="border-b border-border-light bg-gradient-to-br from-white via-white to-surface-warm px-6 py-5">
              <div className="flex items-center justify-between gap-4">
                <h3 id="offline-meeting-recording-title" className="text-lg font-bold tracking-tight text-deep-slate">
                  线下会面纪要录音
                </h3>
                <button
                  onClick={handleCloseModal}
                  disabled={!canDismissModal}
                  aria-label="关闭录音弹窗"
                  className={`rounded-xl p-2 transition-colors ${
                    canDismissModal
                      ? 'text-medium-gray hover:bg-surface hover:text-deep-slate'
                      : 'cursor-not-allowed text-medium-gray/40'
                  }`}
                >
                  <X className="w-5 h-5" strokeWidth={1.75} />
                </button>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {steps.map((step, index) => {
                  const state = getStepState(index);
                  return (
                    <div
                      key={step.key}
                      className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-semibold transition-colors ${
                        state === 'current'
                          ? 'border-deep-trust/20 bg-deep-trust/[0.05]'
                          : state === 'complete'
                          ? 'border-success/20 bg-success/[0.05]'
                          : 'border-border-subtle bg-surface-warm/70'
                      }`}
                    >
                      <span
                        className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold ${
                          state === 'current'
                            ? 'bg-deep-trust text-white'
                            : state === 'complete'
                            ? 'bg-success text-white'
                            : 'border border-border-subtle bg-white text-medium-gray'
                        }`}
                      >
                        {state === 'complete' ? <Check className="w-3 h-3" strokeWidth={2.2} /> : index + 1}
                      </span>
                      <span className="text-deep-slate">{step.label}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto bg-gradient-to-b from-surface-warm/60 via-white to-white px-6 py-6">
              {hasDraft ? (
                <div className="mx-auto flex h-full w-full max-w-2xl flex-col">
                  <div className="mb-4">
                    <label
                      htmlFor="offline-meeting-note-name"
                      className="mb-2 block text-xs font-semibold uppercase tracking-[0.16em] text-medium-gray"
                    >
                      笔记名称
                    </label>
                    <input
                      id="offline-meeting-note-name"
                      type="text"
                      value={normalizedDraftNoteName}
                      onChange={(event) => onDraftNoteNameChange(event.target.value)}
                      disabled={isSavingDraft}
                      className="h-11 w-full rounded-2xl border border-border-light bg-white px-4 text-sm text-deep-slate outline-none transition-colors placeholder:text-medium-gray focus:border-deep-trust/40 focus:ring-4 focus:ring-deep-trust/10 disabled:cursor-not-allowed disabled:bg-surface-warm"
                      placeholder="请输入笔记名称"
                    />
                  </div>

                  <p className="mb-3 text-sm font-semibold text-deep-slate">转写草稿</p>

                  <textarea
                    ref={textareaRef}
                    value={normalizedDraft}
                    onChange={(event) => onDraftChange(event.target.value)}
                    rows={14}
                    className="min-h-[320px] flex-1 resize-none rounded-[24px] border border-border-light bg-white px-5 py-4 text-sm leading-7 text-deep-slate outline-none transition-colors focus:border-deep-trust/40 focus:ring-4 focus:ring-deep-trust/10"
                    placeholder="请确认并编辑整理内容"
                  />

                  {(draftError || error) && (
                    <div className="mt-4 rounded-2xl border border-safety-red/15 bg-safety-red/[0.05] px-4 py-3 text-sm text-safety-red">
                      {draftError || error}
                    </div>
                  )}
                </div>
              ) : (
                <div className="mx-auto flex max-w-2xl flex-col items-center text-center">
                  <div className={`relative flex h-28 w-28 items-center justify-center rounded-full ${
                    recordingState === 'recording'
                      ? 'bg-safety-red/10 text-safety-red'
                      : recordingState === 'processing'
                      ? 'bg-deep-trust/[0.08] text-deep-trust'
                      : showSuccess
                      ? 'bg-success/10 text-success'
                      : 'bg-deep-trust/[0.08] text-deep-trust'
                  }`}>
                    {recordingState === 'recording' && (
                      <div className="absolute inset-0 rounded-full border border-safety-red/20 animate-pulse-ring" />
                    )}
                    {recordingState === 'processing' ? (
                      <Loader2 className="w-10 h-10 animate-spin" strokeWidth={1.8} />
                    ) : showSuccess ? (
                      <Check className="w-10 h-10" strokeWidth={2.4} />
                    ) : recordingState === 'recording' ? (
                      <Square className="w-9 h-9 fill-current" strokeWidth={1.8} />
                    ) : (
                      <Mic className="w-10 h-10" strokeWidth={1.8} />
                    )}
                  </div>

                  <div className="mt-6">
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-medium-gray">
                      {recordingState === 'processing'
                        ? '转写处理中'
                        : recordingState === 'recording'
                        ? '录音进行中'
                        : showSuccess
                        ? '已保存'
                        : '准备录音'}
                    </p>
                    <h4 className="mt-3 text-3xl font-bold tracking-tight text-deep-slate">
                      {recordingState === 'recording'
                        ? formatTime(recordingTime)
                        : recordingState === 'processing'
                        ? '正在整理录音'
                        : showSuccess
                        ? '保存成功'
                        : '准备录音'}
                    </h4>
                  </div>

                  {recordingState === 'recording' && (
                    <div className="mt-8 flex h-14 items-end justify-center gap-1.5">
                      {waveHeights.map((height, index) => (
                        <span
                          key={`${height}-${index}`}
                          className="w-1.5 rounded-full bg-safety-red/70 animate-pulse-soft"
                          style={{
                            height: `${height}px`,
                            animationDelay: `${index * 70}ms`,
                          }}
                        />
                      ))}
                    </div>
                  )}

                  {recordingState === 'processing' && (
                    <div className="mt-8 w-full max-w-lg">
                      <div className="h-2 overflow-hidden rounded-full bg-border-light">
                        <div className="h-full w-2/3 rounded-full bg-gradient-to-r from-deep-trust to-warm-navy animate-pulse-soft" />
                      </div>
                    </div>
                  )}

                  {error && (
                    <div className="mt-6 w-full max-w-lg rounded-2xl border border-safety-red/15 bg-safety-red/[0.05] px-4 py-3 text-sm text-safety-red">
                      {error}
                    </div>
                  )}

                  {recordingState !== 'processing' && !showSuccess && (
                    <div className="mt-8 flex flex-col items-center gap-3">
                      <button
                        onClick={handlePrimaryAction}
                        className={`inline-flex min-w-[180px] items-center justify-center gap-2 rounded-2xl px-6 py-3 text-sm font-semibold text-white shadow-sm transition-all btn-press ${
                          recordingState === 'recording'
                            ? 'bg-safety-red hover:bg-safety-red/90'
                            : 'bg-gradient-to-r from-deep-trust to-warm-navy hover:shadow-md'
                        }`}
                      >
                        {recordingState === 'recording' ? (
                          <>
                            <Square className="w-4 h-4 fill-current" strokeWidth={1.8} />
                            停止并整理
                          </>
                        ) : (
                          <>
                            <Mic className="w-4 h-4" strokeWidth={1.8} />
                            开始录音
                          </>
                        )}
                      </button>
                      {recordingState === 'recording' && (
                        <p className="text-xs text-medium-gray">最多 60 秒</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            {hasDraft && (
              <div className="border-t border-border-light bg-white px-6 py-4">
                <div className="flex items-center justify-end gap-3">
                  <button
                    onClick={handleCancelDraft}
                    disabled={isSavingDraft}
                    className={`rounded-xl border px-4 py-2.5 text-sm font-semibold transition-colors ${
                      isSavingDraft
                        ? 'cursor-not-allowed border-border-light text-medium-gray/60'
                        : 'border-border-subtle text-deep-slate hover:bg-surface-warm'
                    }`}
                  >
                    取消
                  </button>
                  <button
                    onClick={() => void onSaveDraft()}
                    disabled={!canSaveDraft}
                    className={`min-w-[108px] rounded-xl px-5 py-2.5 text-sm font-semibold transition-all btn-press ${
                      canSaveDraft
                        ? 'bg-gradient-to-r from-deep-trust to-warm-navy text-white shadow-sm hover:shadow-md'
                        : 'cursor-not-allowed bg-border-light text-medium-gray'
                    }`}
                  >
                    {isSavingDraft ? '保存中...' : '保存'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
};

export default VoiceRecorder;
