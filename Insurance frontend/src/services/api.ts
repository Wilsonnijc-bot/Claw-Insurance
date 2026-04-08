/**
 * Nanobot API client — connects the frontend to the Nanobot gateway's REST API.
 *
 * Default base URL: http://localhost:3456
 * In production (proxied through Vite), use relative paths.
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `API error ${res.status}`);
  }
  return res.json();
}

// ─── Types ──────────────────────────────────────────────────────────

export interface ApiClient {
  id: string;
  phone: string;
  name: string;
  status: 'online' | 'offline';
  lastMessage: string;
  lastMessageTime: string;
  autoDraftEnabled: boolean;
  tags: string[];
  chatId: string;
  senderId: string;
  pushName: string;
  label?: string;
  sessionFile?: string;
  sessionReadableDir?: string;
  sessionMetaFile?: string;
  sessionHistoryFile?: string;
  sessionReadableFile?: string;
  messageCount?: number;
  createdAt?: string;
  updatedAt?: string;
  clientDisplayName?: string;
  clientPhone?: string;
  clientChatId?: string;
}

export interface GatewayStatus {
  status: string;
  sessions: number;
  direct_targets: number;
  group_targets: number;
  ws_clients: number;
  channels: string[];
  gateway_ready?: boolean;
  gateway_starting?: boolean;
  gateway_error?: string | null;
  whatsapp_bridge_error?: boolean | null;
  whatsapp_bridge_message?: string | null;
  whatsapp_auth_required?: boolean | null;
  whatsapp_auth_qr?: string | null;
  whatsapp_auth_message?: string | null;
}

export interface ApiJournalEntry {
  id: string;
  timestamp: string;
  action: string;
  description: string;
  clientId?: string;
  clientName?: string;
  details?: Record<string, unknown>;
  userId?: string | null;
  userName?: string | null;
  source?: string;
}

// ─── Client endpoints ───────────────────────────────────────────────

/** List all WhatsApp clients (reply targets). */
export async function fetchClients(): Promise<ApiClient[]> {
  const data = await request<{ clients: ApiClient[] }>('/clients');
  return data.clients;
}

/** Get a single client by phone. */
export async function fetchClient(phone: string): Promise<ApiClient> {
  return request<ApiClient>(`/clients/${phone}`);
}

/** Delete a client session and remove it from reply targets. */
export async function deleteClient(phone: string): Promise<{ status: string; phone: string }> {
  return request<{ status: string; phone: string }>(`/clients/${phone}`, {
    method: 'DELETE',
  });
}

// ─── Message endpoints ──────────────────────────────────────────────

/** Build the backend-rendered transcript document URL for a client. */
export function getMessagesViewUrl(phone: string, reloadToken = 0): string {
  const search = new URLSearchParams({ format: 'html' });
  if (reloadToken > 0) {
    search.set('v', String(reloadToken));
  }
  return `${API_BASE}/messages/${encodeURIComponent(phone)}?${search.toString()}`;
}

/** Send a message as the human agent. */
export async function sendMessage(phone: string, content: string): Promise<void> {
  await request(`/messages/${phone}`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

// ─── AI endpoints ───────────────────────────────────────────────────

/** Request AI to generate a draft reply for the latest client message. */
export async function requestAIDraft(phone: string): Promise<{ draft: string }> {
  return request<{ draft: string }>(`/ai-draft/${phone}`, { method: 'POST' });
}

/** Approve and send an AI draft to the client via WhatsApp. */
export async function sendAIDraft(phone: string, content: string): Promise<void> {
  await request(`/ai-send/${phone}`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

// ─── Auto-draft ─────────────────────────────────────────────────────

/** Toggle auto-draft for a client. */
export async function toggleAutoDraft(phone: string, enabled: boolean): Promise<void> {
  await request(`/auto-draft/${phone}`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
}

// ─── Broadcast ──────────────────────────────────────────────────────

/** Send a broadcast message to multiple clients. */
export async function sendBroadcast(phones: string[], content: string): Promise<void> {
  await request('/broadcast', {
    method: 'POST',
    body: JSON.stringify({ phones, content }),
  });
}

// ─── Sync ───────────────────────────────────────────────────────────

/** Trigger WhatsApp history sync for a client. */
export async function triggerSync(phone: string): Promise<void> {
  await request(`/sync/${phone}`, { method: 'POST' });
}

// ─── Bridge Health ──────────────────────────────────────────────────

export interface BridgeRestartResult {
  status: string;
  message: string;
}

/** Kill, rebuild, and restart the bridge process. */
export async function restartBridge(): Promise<BridgeRestartResult> {
  return request<BridgeRestartResult>('/bridge/restart', { method: 'POST' });
}

// ─── Journal ────────────────────────────────────────────────────────

export async function fetchJournal(limit: number = 200): Promise<ApiJournalEntry[]> {
  const data = await request<{ entries: ApiJournalEntry[] }>(`/journal?limit=${limit}`);
  return data.entries;
}

export async function addJournalEntry(entry: {
  action: string;
  description: string;
  clientId?: string;
  clientName?: string;
  details?: Record<string, unknown>;
  userId?: string | null;
  userName?: string | null;
}): Promise<ApiJournalEntry> {
  const data = await request<{ entry: ApiJournalEntry }>('/journal', {
    method: 'POST',
    body: JSON.stringify(entry),
  });
  return data.entry;
}

export async function clearJournal(): Promise<void> {
  await request('/journal', { method: 'DELETE' });
}

// ─── Status ─────────────────────────────────────────────────────────

/** Get gateway status. */
export async function fetchStatus(): Promise<GatewayStatus> {
  return request<GatewayStatus>('/status');
}

// ─── Reply Targets ──────────────────────────────────────────────────

export interface AddReplyTargetResponse {
  status: string;
  phone: string;
  label: string;
}

/** Add a new direct reply target. */
export async function addReplyTarget(
  phone: string,
  label?: string,
  autoDraft?: boolean,
): Promise<AddReplyTargetResponse> {
  return request<AddReplyTargetResponse>('/reply-targets', {
    method: 'POST',
    body: JSON.stringify({ phone, label: label || '', autoDraft: autoDraft ?? true }),
  });
}

// ─── Login / Launch ─────────────────────────────────────────────────

export interface LoginResponse {
  status: string;          // 'ok' | 'starting' | 'error'
  message: string;
  gateway_ready: boolean;
  username?: string;
}

/** POST /api/login — verify backend is running. */
export async function loginAndLaunchGateway(
  username: string,
  password: string,
): Promise<LoginResponse> {
  return request<LoginResponse>('/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}
