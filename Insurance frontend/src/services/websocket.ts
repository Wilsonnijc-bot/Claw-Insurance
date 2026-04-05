/**
 * WebSocket client for real-time updates from the Nanobot gateway.
 *
 * Events received:
 * - new_message: A new inbound/outbound message
 * - ai_generating: AI generation started/completed/error
 * - ai_draft: AI draft completed (manual AI button)
 * - auto_draft: Auto-generated draft arrived
 * - auto_draft_changed: Auto-draft toggled for a client
 */

export type WSEventType =
  | 'new_message'
  | 'journal_entry'
  | 'journal_cleared'
  | 'ai_generating'
  | 'ai_draft'
  | 'auto_draft'
  | 'auto_draft_changed'
  | 'reply_target_added'
  | 'gateway_status'
  | 'whatsapp_browser_status'
  | 'whatsapp_auth_status'
  | 'pong';

export interface WSEvent {
  type: WSEventType;
  phone?: string;
  channel?: string;
  chat_id?: string;
  content?: string;
  sender?: string;
  timestamp?: string;
  status?: string;
  enabled?: boolean;
  reusable?: boolean | null;
  message?: string | null;
  mode?: string | null;
  severity?: string | null;
  qr?: string | null;
  required?: boolean | null;
  gateway_ready?: boolean;
  gateway_starting?: boolean;
  error?: string | null;
  metadata?: Record<string, unknown>;
  entry?: Record<string, unknown>;
}

type EventHandler = (event: WSEvent) => void;

const WS_URL = import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:3456/ws`;

class NanobotWebSocket {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<EventHandler>> = new Map();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 2000;
  private maxReconnectDelay = 30000;
  private pingInterval: ReturnType<typeof setInterval> | null = null;
  private _connected = false;

  get connected(): boolean {
    return this._connected;
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    try {
      this.ws = new WebSocket(WS_URL);

      this.ws.onopen = () => {
        this._connected = true;
        this.reconnectDelay = 2000;
        console.log('[WS] Connected to Nanobot gateway');
        this._emit({ type: 'pong' } as WSEvent); // notify listeners of connection

        // Start ping keepalive
        this.pingInterval = setInterval(() => {
          if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);
      };

      this.ws.onmessage = (event) => {
        try {
          const data: WSEvent = JSON.parse(event.data);
          this._emit(data);
        } catch {
          console.warn('[WS] Failed to parse message:', event.data);
        }
      };

      this.ws.onclose = () => {
        this._connected = false;
        this._cleanup();
        this._scheduleReconnect();
      };

      this.ws.onerror = (err) => {
        console.warn('[WS] Error:', err);
      };
    } catch (err) {
      console.warn('[WS] Connection failed:', err);
      this._scheduleReconnect();
    }
  }

  disconnect(): void {
    this._cleanup();
    if (this.ws) {
      this.ws.onclose = null; // prevent auto-reconnect
      this.ws.close();
      this.ws = null;
    }
    this._connected = false;
  }

  on(eventType: WSEventType | '*', handler: EventHandler): () => void {
    const key = eventType;
    if (!this.handlers.has(key)) {
      this.handlers.set(key, new Set());
    }
    this.handlers.get(key)!.add(handler);

    // Return unsubscribe function
    return () => {
      this.handlers.get(key)?.delete(handler);
    };
  }

  off(eventType: WSEventType | '*', handler: EventHandler): void {
    this.handlers.get(eventType)?.delete(handler);
  }

  private _emit(event: WSEvent): void {
    // Emit to specific type handlers
    this.handlers.get(event.type)?.forEach((h) => {
      try {
        h(event);
      } catch (err) {
        console.error('[WS] Handler error:', err);
      }
    });
    // Emit to wildcard handlers
    this.handlers.get('*')?.forEach((h) => {
      try {
        h(event);
      } catch (err) {
        console.error('[WS] Handler error:', err);
      }
    });
  }

  private _cleanup(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private _scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    console.log(`[WS] Reconnecting in ${this.reconnectDelay / 1000}s...`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, this.maxReconnectDelay);
      this.connect();
    }, this.reconnectDelay);
  }
}

// Singleton instance
export const nanobotWS = new NanobotWebSocket();
