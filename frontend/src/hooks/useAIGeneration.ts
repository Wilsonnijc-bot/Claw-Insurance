import { useState, useCallback, useRef, useEffect } from 'react';
import type { LoadingState } from '../types';
import { thinkingStates } from '../types';

export const useAIGeneration = () => {
  const [aiLoading, setAiLoading] = useState<LoadingState | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const startGeneration = useCallback(() => {
    // Clear any existing timers
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);

    setAiLoading({
      isGenerating: true,
      currentPhase: 0,
      progressText: thinkingStates[0].text,
      subText: thinkingStates[0].subtext,
    });

    let phase = 0;

    // Cycle through phases every 2 seconds
    intervalRef.current = setInterval(() => {
      phase = (phase + 1) % 4;
      setAiLoading({
        isGenerating: true,
        currentPhase: phase as 0 | 1 | 2 | 3,
        progressText: thinkingStates[phase].text,
        subText: thinkingStates[phase].subtext,
      });
    }, 2000);

    // Safety-net timeout: auto-stop after 120s in case the backend never
    // sends a completion event.  Normal stop is triggered by the backend
    // ai_generating / ai_draft / auto_draft events arriving first.
    timeoutRef.current = setTimeout(() => {
      stopGeneration();
    }, 120_000);
  }, []);

  const stopGeneration = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    setAiLoading(null);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return {
    aiLoading,
    startGeneration,
    stopGeneration,
  };
};

export default useAIGeneration;
