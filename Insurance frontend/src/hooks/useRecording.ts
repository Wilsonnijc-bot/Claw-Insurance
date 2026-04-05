import { useState, useCallback } from 'react';

export const useRecording = () => {
  const [recordingState, setRecordingState] = useState<'idle' | 'recording' | 'processing'>('idle');

  const startRecording = useCallback(() => {
    setRecordingState('recording');
  }, []);

  const stopRecording = useCallback(() => {
    setRecordingState('processing');
    
    // Simulate processing delay
    setTimeout(() => {
      setRecordingState('idle');
    }, 2000);
  }, []);

  const resetRecording = useCallback(() => {
    setRecordingState('idle');
  }, []);

  return {
    recordingState,
    startRecording,
    stopRecording,
    resetRecording,
  };
};

export default useRecording;
