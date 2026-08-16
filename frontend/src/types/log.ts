export type KnownLogAction =
  | 'LOGIN'
  | 'LOGOUT'
  | 'LOGIN_HISTORY_PARSE'
  | 'INBOUND_MESSAGE'
  | 'SEND_MESSAGE'
  | 'AI_GENERATE'
  | 'AI_DRAFT_READY'
  | 'AUTO_DRAFT_READY'
  | 'AI_SEND_DRAFT'
  | 'AI_SEND_EDITED'
  | 'AI_EDIT_DRAFT'
  | 'AI_DISCARD_DRAFT'
  | 'TOGGLE_AUTO_DRAFT'
  | 'SYNC_WHATSAPP'
  | 'ADD_REPLY_TARGET'
  | 'START_RECORDING'
  | 'STOP_RECORDING'
  | 'SELECT_CLIENT'
  | 'BROADCAST'
  | 'VIEW_LOGS';

export type LogAction = KnownLogAction | (string & {});

export interface LogEntry {
  id: string;
  timestamp: string;
  action: LogAction;
  description: string;
  clientId?: string;
  clientName?: string;
  details?: Record<string, unknown>;
  userId?: string | null;
  userName?: string | null;
  source?: 'backend' | 'frontend' | string;
}

export interface LogFilter {
  startDate?: string;
  endDate?: string;
  action?: KnownLogAction;
  clientId?: string;
}
