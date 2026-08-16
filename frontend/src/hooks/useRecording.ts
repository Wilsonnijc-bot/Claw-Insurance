import { useCallback, useEffect, useRef, useState } from 'react';
import { transcribeOfflineMeetingNote, type OfflineMeetingNoteDraftResponse } from '../services/api';

const MAX_RECORDING_MS = 60_000;

interface UseRecordingOptions {
  clientId: string | null;
  enabled: boolean;
  onTranscriptDraft?: (clientId: string, draft: OfflineMeetingNoteDraftResponse) => void;
}

const PREFERRED_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
];

function pickSupportedMimeType(): string {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return '';
  }
  return PREFERRED_MIME_TYPES.find((mimeType) => MediaRecorder.isTypeSupported(mimeType)) || '';
}

export const useRecording = ({
  clientId,
  enabled,
  onTranscriptDraft,
}: UseRecordingOptions) => {
  const [recordingState, setRecordingState] = useState<'idle' | 'recording' | 'processing'>('idle');
  const [recordingTime, setRecordingTime] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const autoStopRef = useRef<number | null>(null);
  const stoppingRef = useRef(false);
  const clientIdRef = useRef<string | null>(clientId);
  const previousClientIdRef = useRef<string | null>(clientId);
  const stopRecordingRef = useRef<() => Promise<void>>(async () => {});

  const clearTimers = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (autoStopRef.current !== null) {
      window.clearTimeout(autoStopRef.current);
      autoStopRef.current = null;
    }
  }, []);

  const stopTracks = useCallback(() => {
    const stream = mediaStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
    }
    mediaStreamRef.current = null;
  }, []);

  const resetRecorder = useCallback(() => {
    clearTimers();
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      try {
        recorder.stop();
      } catch {
        // Ignore stop races during teardown.
      }
    }
    stopTracks();
    mediaRecorderRef.current = null;
    chunksRef.current = [];
    startedAtRef.current = null;
    stoppingRef.current = false;
    setRecordingTime(0);
  }, [clearTimers, stopTracks]);

  useEffect(() => {
    const previousClientId = previousClientIdRef.current;
    if (
      previousClientId &&
      clientId &&
      previousClientId !== clientId &&
      recordingState !== 'processing'
    ) {
      resetRecorder();
      setRecordingState('idle');
      setError(null);
    }
    clientIdRef.current = clientId;
    previousClientIdRef.current = clientId;
  }, [clientId, recordingState, resetRecorder]);

  useEffect(() => {
    if (!enabled) {
      if (recordingState !== 'processing') {
        resetRecorder();
        setRecordingState('idle');
      }
      setError(null);
    }
  }, [enabled, recordingState, resetRecorder]);

  useEffect(() => {
    return () => {
      resetRecorder();
    };
  }, [resetRecorder]);

  useEffect(() => {
    if (!clientId) {
      if (recordingState !== 'processing') {
        resetRecorder();
        setRecordingState('idle');
      }
      setError(null);
    }
  }, [clientId, recordingState, resetRecorder]);

  const startRecording = useCallback(async () => {
    if (!enabled || !clientIdRef.current || recordingState !== 'idle') {
      return;
    }
    if (
      typeof navigator === 'undefined' ||
      !navigator.mediaDevices ||
      typeof navigator.mediaDevices.getUserMedia !== 'function' ||
      typeof MediaRecorder === 'undefined'
    ) {
      setError('当前浏览器不支持录音功能。');
      return;
    }

    try {
      setError(null);
      chunksRef.current = [];
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = pickSupportedMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      startedAtRef.current = window.performance.now();

      recorder.addEventListener('dataavailable', (event) => {
        if (event.data && event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      });

      recorder.addEventListener('error', () => {
        setError('录音失败，请重新尝试。');
        resetRecorder();
        setRecordingState('idle');
      });

      recorder.start();
      setRecordingState('recording');
      setRecordingTime(0);

      timerRef.current = window.setInterval(() => {
        const startedAt = startedAtRef.current;
        if (startedAt === null) {
          return;
        }
        const elapsedMs = Math.min(MAX_RECORDING_MS, window.performance.now() - startedAt);
        setRecordingTime(Math.floor(elapsedMs / 1000));
      }, 250);

      autoStopRef.current = window.setTimeout(() => {
        void stopRecordingRef.current();
      }, MAX_RECORDING_MS);
    } catch (err) {
      stopTracks();
      setRecordingState('idle');
      setError(err instanceof Error ? err.message : '无法开始录音，请检查麦克风权限。');
    }
  }, [enabled, recordingState, resetRecorder, stopTracks]);

  const stopRecording = useCallback(async () => {
    if (recordingState !== 'recording' || stoppingRef.current) {
      return;
    }

    const recorder = mediaRecorderRef.current;
    const activeClientId = clientIdRef.current;
    if (!recorder) {
      resetRecorder();
      setRecordingState('idle');
      return;
    }

    stoppingRef.current = true;
    clearTimers();
    setRecordingState('processing');

    try {
      if (recorder.state !== 'inactive') {
        await new Promise<void>((resolve, reject) => {
          const handleStop = () => resolve();
          const handleError = () => reject(new Error('录音停止失败。'));
          recorder.addEventListener('stop', handleStop, { once: true });
          recorder.addEventListener('error', handleError, { once: true });
          recorder.stop();
        });
      }

      stopTracks();

      const startedAt = startedAtRef.current ?? window.performance.now();
      const durationMs = Math.min(
        MAX_RECORDING_MS,
        Math.max(1, Math.round(window.performance.now() - startedAt)),
      );
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || 'audio/webm',
      });
      chunksRef.current = [];

      if (!activeClientId) {
        throw new Error('未选择客户，无法上传录音。');
      }

      const result = await transcribeOfflineMeetingNote(activeClientId, blob, durationMs);
      onTranscriptDraft?.(activeClientId, result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '录音上传失败，请重试。');
    } finally {
      resetRecorder();
      setRecordingState('idle');
    }
  }, [clearTimers, onTranscriptDraft, recordingState, resetRecorder, stopTracks]);

  const resetRecording = useCallback(() => {
    resetRecorder();
    setRecordingState('idle');
    setError(null);
  }, [resetRecorder]);

  useEffect(() => {
    stopRecordingRef.current = stopRecording;
  }, [stopRecording]);

  return {
    recordingState,
    recordingTime,
    error,
    startRecording,
    stopRecording,
    resetRecording,
  };
};

export default useRecording;
