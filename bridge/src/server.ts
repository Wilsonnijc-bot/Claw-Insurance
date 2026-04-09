/**
 * WebSocket server for Python-Node.js bridge communication.
 * Security: binds to 127.0.0.1 only; optional BRIDGE_TOKEN auth.
 */

import { WebSocketServer, WebSocket } from 'ws';
import { DraftComposer } from './draft.js';
import { HistoryParser, type ScrapedHistoryMessage } from './history.js';
import { WhatsAppClient, type ChatTarget, type HistoryBatch } from './whatsapp.js';

interface SendCommand {
  type: 'send';
  to: string;
  text: string;
}

interface PrepareDraftCommand {
  type: 'prepare_draft';
  to: string;
  text: string;
  target?: ChatTarget;
}

interface ScrapeDirectHistoryCommand {
  type: 'scrape_direct_history';
  target: ChatTarget;
  requestId?: string;
}

interface ScrapeReplyTargetsHistoryCommand {
  type: 'scrape_reply_targets_history';
  targets: ChatTarget[];
  requestId?: string;
}

interface BridgeMessage {
  type: 'message' | 'history' | 'deleted' | 'status' | 'qr' | 'error' | 'ack';
  [key: string]: unknown;
}

type BridgeCommand = SendCommand | PrepareDraftCommand | ScrapeDirectHistoryCommand | ScrapeReplyTargetsHistoryCommand;

export class BridgeServer {
  private wss: WebSocketServer | null = null;
  private wa: WhatsAppClient | null = null;
  private clients: Set<WebSocket> = new Set();
  private draftComposer: DraftComposer | null = null;
  private historyParser: HistoryParser | null = null;

  constructor(
    private port: number,
    private authDir: string,
    private webProfileDir: string,
    private token?: string,
    private webBrowserMode: 'cdp' | 'launch' = 'cdp',
    private webCdpUrl: string = 'http://127.0.0.1:9222',
    private webCdpChromePath: string = '',
  ) {}

  async start(): Promise<void> {
    // Bind to localhost only — never expose to external network
    this.wss = new WebSocketServer({ host: '127.0.0.1', port: this.port });
    console.log(`🌉 Bridge server listening on ws://127.0.0.1:${this.port}`);
    if (this.token) console.log('🔒 Token authentication enabled');

    // Initialize WhatsApp client
    this.wa = new WhatsAppClient({
      authDir: this.authDir,
      onMessage: (msg) => this.broadcast({ type: 'message', ...msg }),
      onHistory: (batch: HistoryBatch) => this.broadcast({ type: 'history', ...batch }),
      onDelete: (msg) => this.broadcast({ type: 'deleted', ...msg }),
      onQR: (qr) => this.broadcast({ type: 'qr', qr }),
      onStatus: (status) => this.broadcast({ type: 'status', status }),
    });
    this.draftComposer = new DraftComposer(
      this.webProfileDir,
      undefined,
      this.webBrowserMode,
      this.webCdpUrl,
      this.webCdpChromePath,
    );
    this.historyParser = new HistoryParser(
      this.webProfileDir,
      undefined,
      this.webBrowserMode,
      this.webCdpUrl,
      this.webCdpChromePath,
    );

    // Handle WebSocket connections
    this.wss.on('connection', (ws) => {
      if (this.token) {
        // Require auth handshake as first message
        const timeout = setTimeout(() => ws.close(4001, 'Auth timeout'), 5000);
        ws.once('message', (data) => {
          clearTimeout(timeout);
          try {
            const msg = JSON.parse(data.toString());
            if (msg.type === 'auth' && msg.token === this.token) {
              console.log('🔗 Python client authenticated');
              this.setupClient(ws);
            } else {
              ws.close(4003, 'Invalid token');
            }
          } catch {
            ws.close(4003, 'Invalid auth message');
          }
        });
      } else {
        console.log('🔗 Python client connected');
        this.setupClient(ws);
      }
    });

    // Connect to WhatsApp
    await this.wa.connect();
  }

  private setupClient(ws: WebSocket): void {
    this.clients.add(ws);

    ws.on('message', async (data) => {
      try {
        const cmd = JSON.parse(data.toString()) as BridgeCommand;
        const ack = await this.handleCommand(cmd);
        ws.send(JSON.stringify(ack));
      } catch (error) {
        console.error('Error handling command:', error);
        ws.send(JSON.stringify({ type: 'error', error: String(error) }));
      }
    });

    ws.on('close', () => {
      console.log('🔌 Python client disconnected');
      this.clients.delete(ws);
    });

    ws.on('error', (error) => {
      console.error('WebSocket error:', error);
      this.clients.delete(ws);
    });
  }

  private async handleCommand(cmd: BridgeCommand): Promise<BridgeMessage> {
    if (cmd.type === 'send' && this.wa) {
      await this.wa.sendMessage(cmd.to, cmd.text);
      return { type: 'ack', action: cmd.type, to: cmd.to, status: 'sent' };
    }

    if (cmd.type === 'prepare_draft') {
      if (!this.wa || !this.draftComposer) {
        return {
          type: 'ack',
          action: cmd.type,
          to: cmd.to,
          status: 'not_ready',
          detail: 'Bridge is not ready yet.',
        };
      }

      const target = this.normalizeDraftTarget(cmd.target, cmd.to) ?? this.wa.getChatTarget(cmd.to);
      if (!target) {
        return {
          type: 'ack',
          action: cmd.type,
          to: cmd.to,
          status: 'chat_not_found',
          detail: 'No known direct-message target for this chat yet.',
        };
      }

      try {
        const result = await this.draftComposer.prepareDraft(target, cmd.text);
        return { type: 'ack', action: cmd.type, to: cmd.to, ...result };
      } catch (error) {
        return {
          type: 'ack',
          action: cmd.type,
          to: cmd.to,
          status: 'not_ready',
          detail: String(error),
        };
      }
    }

    if (cmd.type === 'scrape_direct_history') {
      if (!this.historyParser) {
        return {
          type: 'ack',
          action: cmd.type,
          to: '',
          status: 'not_ready',
          detail: 'Bridge is not ready yet.',
        };
      }

      const target = this.normalizeHistoryTarget(cmd.target, '');
      if (!target) {
        return {
          type: 'ack',
          action: cmd.type,
          to: '',
          status: 'chat_not_found',
          detail: 'No valid direct-message target with a normalized phone was provided.',
        };
      }

      try {
        const result = await this.historyParser.scrapeHistory(target);
        if (result.status !== 'history_scraped') {
          return {
            type: 'ack',
            action: cmd.type,
            to: target.chatId,
            ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
            status: result.status,
            detail: result.detail,
          };
        }

        const messages = this.normalizeScrapedHistory(target, result.messages || []);
        if (messages.length > 0) {
          this.broadcast({
            type: 'history',
            source: 'web_scrape',
            ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
            messages,
            target: target.chatId,
          });
        }
        if (cmd.requestId) {
          this.broadcast({
            type: 'history',
            source: 'web_scrape',
            requestId: cmd.requestId,
            isLatest: true,
            messages: [],
            target: target.chatId,
          });
        }

        return {
          type: 'ack',
          action: cmd.type,
          to: target.chatId,
          ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
          status: 'history_scraped',
          scrapedTargets: 1,
          scrapedMessages: messages.length,
          missedTargets: 0,
          importPhones: messages.length > 0 && target.phone ? [target.phone] : [],
        };
      } catch (error) {
        return {
          type: 'ack',
          action: cmd.type,
          to: target.chatId,
          ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
          status: 'window_launch_failed',
          detail: String(error),
        };
      }
    }

    if (cmd.type === 'scrape_reply_targets_history') {
      if (!this.historyParser) {
        return {
          type: 'ack',
          action: cmd.type,
          to: '',
          status: 'bridge_unreachable',
          detail: 'Bridge is not ready yet.',
        };
      }

      const targets = (Array.isArray(cmd.targets) ? cmd.targets : [])
        .map((target) => this.normalizeHistoryTarget(target, ''))
        .filter((target): target is ChatTarget => target !== null);
      if (targets.length === 0) {
        return {
          type: 'ack',
          action: cmd.type,
          to: '',
          status: 'chat_not_found',
          detail: 'No valid direct-message targets with normalized phones were provided.',
        };
      }

      try {
        const result = await this.historyParser.scrapeReplyTargets(targets);
        if (result.status !== 'history_scraped') {
          return {
            type: 'ack',
            action: cmd.type,
            to: '',
            ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
            status: result.status,
            detail: result.detail,
          };
        }

        let scrapedTargets = 0;
        let scrapedMessages = 0;
        let missedTargets = 0;
        const importPhones = new Set<string>();

        for (const item of result.results) {
          if (item.status === 'chat_not_found') {
            missedTargets += 1;
            continue;
          }
          const messages = this.normalizeScrapedHistory(item.target, item.messages || []);
          scrapedTargets += 1;
          scrapedMessages += messages.length;
          if (messages.length > 0) {
            if (item.target.phone) {
              importPhones.add(item.target.phone);
            }
            this.broadcast({
              type: 'history',
              source: 'web_scrape',
              ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
              messages,
              target: item.target.chatId,
            });
          }
        }
        if (cmd.requestId) {
          this.broadcast({
            type: 'history',
            source: 'web_scrape',
            requestId: cmd.requestId,
            isLatest: true,
            messages: [],
          });
        }

        return {
          type: 'ack',
          action: cmd.type,
          to: '',
          ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
          status: 'history_scraped',
          scrapedTargets,
          scrapedMessages,
          missedTargets,
          importPhones: [...importPhones],
        };
      } catch (error) {
        return {
          type: 'ack',
          action: cmd.type,
          to: '',
          ...(cmd.requestId ? { requestId: cmd.requestId } : {}),
          status: 'window_launch_failed',
          detail: String(error),
        };
      }
    }

    return {
      type: 'ack',
      action: cmd.type,
      to: cmd.to,
      status: 'not_ready',
      detail: 'Unsupported bridge command.',
    };
  }

  private normalizeDraftTarget(target: ChatTarget | undefined, fallbackChatId: string): ChatTarget | null {
    if (!target || typeof target !== 'object') {
      return null;
    }

    const chatId = typeof target.chatId === 'string' && target.chatId.trim()
      ? target.chatId.trim()
      : fallbackChatId.trim();
    if (!chatId) {
      return null;
    }

    const phone = typeof target.phone === 'string' && target.phone.trim()
      ? target.phone.trim()
      : undefined;
    const rawSearchTerms = Array.isArray(target.searchTerms) ? target.searchTerms : [];
    const searchTerms = rawSearchTerms
      .filter((item): item is string => typeof item === 'string')
      .map((item) => item.trim())
      .filter(Boolean);

    if (!phone && searchTerms.length === 0) {
      return null;
    }

    return {
      chatId,
      ...(phone ? { phone } : {}),
      searchTerms,
    };
  }

  private normalizeHistoryTarget(target: ChatTarget | undefined, fallbackChatId: string): ChatTarget | null {
    const normalized = this.normalizeDraftTarget(target, fallbackChatId);
    if (!normalized?.phone) {
      return null;
    }
    return normalized;
  }

  private broadcast(msg: BridgeMessage): void {
    const data = JSON.stringify(msg);
    for (const client of this.clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(data);
      }
    }
  }

  private normalizeScrapedHistory(target: ChatTarget, messages: ScrapedHistoryMessage[]): HistoryBatch['messages'] {
    return messages.map((message) => ({
      id: message.id,
      sender: target.chatId,
      pn: target.phone || '',
      content: message.content,
      timestamp: Number.isNaN(Date.parse(message.timestamp))
        ? Math.floor(Date.now() / 1000)
        : Math.floor(Date.parse(message.timestamp) / 1000),
      fromMe: message.fromMe,
      isGroup: false,
      ...(message.pushName ? { pushName: message.pushName } : {}),
    }));
  }

  async stop(): Promise<void> {
    // Close all client connections
    for (const client of this.clients) {
      client.close();
    }
    this.clients.clear();

    // Close WebSocket server
    if (this.wss) {
      this.wss.close();
      this.wss = null;
    }

    // Disconnect WhatsApp
    if (this.wa) {
      await this.wa.disconnect();
      this.wa = null;
    }

    if (this.draftComposer) {
      await this.draftComposer.stop();
      this.draftComposer = null;
    }

    if (this.historyParser) {
      await this.historyParser.stop();
      this.historyParser = null;
    }
  }
}
