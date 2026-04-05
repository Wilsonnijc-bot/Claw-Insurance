import React, { useState, useEffect, useRef } from 'react';
import { Mic, Square, Loader2, Check } from 'lucide-react';

interface VoiceRecorderProps {
  recordingState: 'idle' | 'recording' | 'processing';
  onStartRecording: () => void;
  onStopRecording: () => void;
}

export const VoiceRecorder: React.FC<VoiceRecorderProps> = ({
  recordingState,
  onStartRecording,
  onStopRecording,
}) => {
  const [recordingTime, setRecordingTime] = useState(0);
  const [showSuccess, setShowSuccess] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Timer effect
  useEffect(() => {
    if (recordingState === 'recording') {
      intervalRef.current = setInterval(() => {
        setRecordingTime((prev) => prev + 1);
      }, 1000);
    } else {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      if (recordingState === 'idle' && recordingTime > 0) {
        // Show success animation
        setShowSuccess(true);
        setTimeout(() => {
          setShowSuccess(false);
          setRecordingTime(0);
        }, 2000);
      }
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [recordingState, recordingTime]);

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const handleClick = () => {
    if (recordingState === 'idle') {
      onStartRecording();
    } else if (recordingState === 'recording') {
      onStopRecording();
    }
  };

  return (
    <div className="bg-gradient-to-br from-surface-warm to-surface rounded-xl p-4 border border-border-subtle">
      {/* Section Header */}
      <div className="flex items-center gap-2 mb-2">
        <div className="w-6 h-6 rounded-lg bg-safety-red/8 flex items-center justify-center">
          <Mic className="w-3.5 h-3.5 text-safety-red" strokeWidth={1.5} />
        </div>
        <span className="text-sm font-bold text-deep-slate">线下会面录音</span>
      </div>
      <p className="text-[11px] text-medium-gray leading-relaxed mb-4">
        如您与客户线下见面后，可录音补充客户信息，AI助手将自动整理并同步至数据库。
      </p>

      {/* Recording UI */}
      <div className="flex items-center justify-center">
        <div className="relative">
          {/* Pulsing Ring (only when recording) */}
          {recordingState === 'recording' && (
            <div className="absolute inset-0 rounded-full bg-safety-red/20 animate-pulse-ring" />
          )}

          {/* Success Checkmark */}
          {showSuccess ? (
            <div className="relative w-12 h-12 rounded-full bg-success flex items-center justify-center animate-slide-in-right">
              <Check className="w-6 h-6 text-white" strokeWidth={2} />
            </div>
          ) : (
            <button
              onClick={handleClick}
              disabled={recordingState === 'processing'}
              className={`relative w-12 h-12 rounded-full flex items-center justify-center transition-all duration-200 shadow-sm ${
                recordingState === 'idle'
                  ? 'bg-gradient-to-br from-warm-navy to-deep-trust hover:shadow-lg hover:shadow-deep-trust/20 hover:scale-105 active:scale-95'
                  : recordingState === 'recording'
                  ? 'bg-safety-red animate-pulse-ring shadow-safety-red/30'
                  : 'bg-gray-300 cursor-not-allowed'
              }`}
              style={{
                transform: recordingState === 'recording' ? 'scale(1)' : undefined,
              }}
            >
              {recordingState === 'processing' ? (
                <Loader2 className="w-5 h-5 text-white animate-spin" strokeWidth={1.5} />
              ) : recordingState === 'recording' ? (
                <Square className="w-5 h-5 text-white fill-current" strokeWidth={1.5} />
              ) : (
                <Mic className="w-5 h-5 text-white" strokeWidth={1.5} />
              )}
            </button>
          )}
        </div>

        {/* Timer / Status */}
        <div className="ml-4">
          {recordingState === 'processing' ? (
            <span className="text-sm text-medium-gray">处理中...</span>
          ) : showSuccess ? (
            <span className="text-sm text-success font-medium">保存成功</span>
          ) : recordingState === 'recording' ? (
            <span className="font-mono text-2xl text-deep-slate tracking-tight">
              {formatTime(recordingTime)}
            </span>
          ) : (
            <span className="text-sm text-medium-gray">点击开始录音</span>
          )}
        </div>
      </div>

      {/* Waveform Visualization (only when recording) */}
      {recordingState === 'recording' && (
        <div className="flex items-center justify-center gap-0.5 mt-4 h-8">
          {[...Array(30)].map((_, i) => (
            <div
              key={i}
              className="w-1 bg-safety-red/60 rounded-full animate-pulse-soft"
              style={{
                height: `${Math.max(4, Math.random() * 28 + 4)}px`,
                animationDelay: `${i * 50}ms`,
              }}
            />
          ))}
        </div>
      )}

      {/* Processing Progress Bar */}
      {recordingState === 'processing' && (
        <div className="mt-4">
          <div className="h-1 bg-border-light rounded-full overflow-hidden">
            <div className="h-full bg-deep-trust animate-pulse-soft rounded-full" style={{ width: '60%' }} />
          </div>
        </div>
      )}
    </div>
  );
};

export default VoiceRecorder;
