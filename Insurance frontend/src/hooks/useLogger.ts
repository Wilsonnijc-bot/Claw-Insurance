import { useState, useCallback, useEffect } from 'react';
import { addJournalEntry, clearJournal as apiClearJournal, fetchJournal } from '../services/api';
import { nanobotWS, type WSEvent } from '../services/websocket';
import type { LogEntry, LogAction, LogFilter } from '../types/log';

export const useLogger = (enabled = true) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [currentUser, setCurrentUser] = useState<{ id: string; name: string } | null>(() => {
    if (typeof window !== 'undefined') {
      const saved = sessionStorage.getItem('insureai_user');
      return saved ? JSON.parse(saved) : null;
    }
    return null;
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (currentUser) {
      sessionStorage.setItem('insureai_user', JSON.stringify(currentUser));
    } else {
      sessionStorage.removeItem('insureai_user');
    }
  }, [currentUser]);

  useEffect(() => {
    if (!enabled) {
      setLogs([]);
      return;
    }

    let mounted = true;

    void fetchJournal().then((entries) => {
      if (!mounted) return;
      setLogs(entries as LogEntry[]);
    }).catch(() => undefined);

    const unsubEntry = nanobotWS.on('journal_entry', (event: WSEvent) => {
      const entry = event.entry as LogEntry | undefined;
      if (!entry?.id) return;
      setLogs((prev) => (prev.some((item) => item.id === entry.id) ? prev : [entry, ...prev]));
    });

    const unsubCleared = nanobotWS.on('journal_cleared', () => {
      setLogs([]);
    });

    return () => {
      mounted = false;
      unsubEntry();
      unsubCleared();
    };
  }, [enabled]);

  const addLog = useCallback((
    action: LogAction,
    description: string,
    options?: {
      clientId?: string;
      clientName?: string;
      details?: Record<string, unknown>;
    }
  ) => {
    if (!enabled) return;
    void addJournalEntry({
      action,
      description,
      clientId: options?.clientId,
      clientName: options?.clientName,
      details: options?.details,
      userId: currentUser?.id,
      userName: currentUser?.name,
    }).then((entry) => {
      setLogs((prev) => (prev.some((item) => item.id === entry.id) ? prev : [entry as LogEntry, ...prev]));
    }).catch((err) => {
      console.error('Failed to persist journal entry:', err);
    });
  }, [currentUser, enabled]);

  const setUser = useCallback((user: { id: string; name: string } | null) => {
    setCurrentUser(user);
  }, []);

  const filterLogs = useCallback((filter: LogFilter): LogEntry[] => {
    return logs.filter((log) => {
      if (filter.action && log.action !== filter.action) return false;
      if (filter.clientId && log.clientId !== filter.clientId) return false;
      if (filter.startDate && new Date(log.timestamp) < new Date(filter.startDate)) return false;
      if (filter.endDate && new Date(log.timestamp) > new Date(filter.endDate)) return false;
      return true;
    });
  }, [logs]);

  const getClientLogs = useCallback((clientId: string): LogEntry[] => {
    return logs.filter((log) => log.clientId === clientId);
  }, [logs]);

  const getRecentLogs = useCallback((count: number = 50): LogEntry[] => {
    return logs.slice(0, count);
  }, [logs]);

  const clearLogs = useCallback(() => {
    if (!enabled) {
      setLogs([]);
      return;
    }
    void apiClearJournal()
      .then(() => setLogs([]))
      .catch((err) => {
        console.error('Failed to clear journal:', err);
      });
  }, [enabled]);

  const exportLogs = useCallback((): string => {
    const dataStr = JSON.stringify(logs, null, 2);
    return dataStr;
  }, [logs]);

  const downloadLogs = useCallback(() => {
    const dataStr = exportLogs();
    const blob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `insureai_logs_${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }, [exportLogs]);

  return {
    logs,
    currentUser,
    addLog,
    setUser,
    filterLogs,
    getClientLogs,
    getRecentLogs,
    clearLogs,
    exportLogs,
    downloadLogs,
  };
};

export default useLogger;
