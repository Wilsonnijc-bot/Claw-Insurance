/**
 * WhatsApp client wrapper using Baileys.
 * Based on OpenClaw's working implementation.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import makeWASocket, {
  Browsers,
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  downloadMediaMessage,
  extractMessageContent as baileysExtractMessageContent,
} from '@whiskeysockets/baileys';

import { Boom } from '@hapi/boom';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import { constants as fsConstants } from 'fs';
import { access, writeFile, mkdir, rm } from 'fs/promises';
import { join } from 'path';
import { homedir } from 'os';
import { randomBytes } from 'crypto';

const VERSION = '0.1.0';
const INVALID_AUTH_RECONNECT_DELAY_MS = 5000;

export interface InboundMessage {
  id: string;
  sender: string;
  pn: string;
  content: string;
  timestamp: number;
  isGroup: boolean;
  isSelfChat?: boolean;
  participant?: string;
  participantPn?: string;
  groupId?: string;
  groupName?: string;
  pushName?: string;
  media?: string[];
}

export interface HistoricalMessage {
  id: string;
  sender: string;
  pn: string;
  content: string;
  timestamp: number;
  fromMe: boolean;
  isGroup: boolean;
  isSelfChat?: boolean;
  participant?: string;
  participantPn?: string;
  groupId?: string;
  groupName?: string;
  pushName?: string;
}

export interface HistoryBatch {
  messages: HistoricalMessage[];
  source: 'history_sync' | 'upsert';
  isLatest?: boolean;
  progress?: number | null;
  syncType?: string | number | null;
}

export interface DeletedMessage {
  deletedMessageId: string;
  sender: string;
  pn: string;
  timestamp: number;
  isGroup: boolean;
  participant?: string;
  participantPn?: string;
  groupId?: string;
  groupName?: string;
  pushName?: string;
  deletedBySender: boolean;
}

export interface ChatTarget {
  chatId: string;
  phone?: string;
  searchTerms: string[];
}

export interface WhatsAppClientOptions {
  authDir: string;
  onMessage: (msg: InboundMessage) => void;
  onHistory: (batch: HistoryBatch) => void;
  onDelete: (msg: DeletedMessage) => void;
  onQR: (qr: string) => void;
  onStatus: (status: string) => void;
}

export function extractPhoneFromJid(value: string): string {
  const text = value.trim();
  if (!text) {
    return '';
  }

  const match = text.match(/^(\+?\d+)@(s\.whatsapp\.net|c\.us)$/);
  return match ? match[1] : '';
}

export function resolvePhoneIdentifier(pn: string, sender: string): string {
  const direct = pn.trim();
  if (direct) {
    if (direct.includes('@')) {
      return extractPhoneFromJid(direct);
    }
    const cleaned = direct.replace(/[^\d+]/g, '');
    return /\d/.test(cleaned) ? cleaned : '';
  }

  return extractPhoneFromJid(sender);
}

function normalizeJidLocal(value: string): string {
  const text = value.trim().toLowerCase();
  if (!text) {
    return '';
  }
  const localWithDevice = text.includes('@') ? text.split('@', 1)[0] : text;
  const local = localWithDevice.includes(':') ? localWithDevice.split(':', 1)[0] : localWithDevice;
  const digits = local.replace(/\D/g, '');
  return digits || local;
}

export function isSelfDirectChat(remoteJid: string, selfJid: string): boolean {
  const remote = remoteJid.trim().toLowerCase();
  const self = selfJid.trim().toLowerCase();
  if (!remote || !self) {
    return false;
  }
  if (remote.endsWith('@g.us')) {
    return false;
  }
  return normalizeJidLocal(remote) === normalizeJidLocal(self);
}

export class WhatsAppClient {
  private sock: any = null;
  private options: WhatsAppClientOptions;
  private reconnecting = false;
  private authResetInProgress = false;
  private chatTargets: Map<string, ChatTarget> = new Map();
  private groupNames: Map<string, string> = new Map();

  constructor(options: WhatsAppClientOptions) {
    this.options = options;
  }

  async connect(): Promise<void> {
    const logger = pino({ level: 'silent' });
    const credsFile = join(this.options.authDir, 'creds.json');
    await this.ensureAuthDir();
    const hasExistingAuth = await access(credsFile, fsConstants.F_OK)
      .then(() => true)
      .catch(() => false);
    if (hasExistingAuth) {
      console.log(`🔐 Reusing existing Baileys auth session from ${credsFile}`);
      console.log('   QR appears only if that session is missing, expired, or logged out.');
    } else {
      console.log(`📱 No Baileys auth session found at ${credsFile}`);
      console.log('   A QR code will appear when WhatsApp requests login.');
    }
    const { state, saveCreds } = await useMultiFileAuthState(this.options.authDir);
    const { version } = await fetchLatestBaileysVersion();

    console.log(`Using Baileys version: ${version.join('.')}`);

    // Create socket following OpenClaw's pattern
    this.sock = makeWASocket({
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      version,
      logger,
      printQRInTerminal: false,
      browser: Browsers.macOS('Desktop'),
      syncFullHistory: true,
      markOnlineOnConnect: false,
    });

    // Handle WebSocket errors
    if (this.sock.ws && typeof this.sock.ws.on === 'function') {
      this.sock.ws.on('error', (err: Error) => {
        console.error('WebSocket error:', err.message);
      });
    }

    // Handle connection updates
    this.sock.ev.on('connection.update', async (update: any) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        // Display QR code in terminal
        console.log('\n📱 Scan this QR code with WhatsApp (Linked Devices):\n');
        qrcode.generate(qr, { small: true });
        this.options.onQR(qr);
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const authInvalid = statusCode === DisconnectReason.loggedOut || statusCode === 401;
        const shouldReconnect = !authInvalid;

        console.log(`Connection closed. Status: ${statusCode}, Will reconnect: ${shouldReconnect}`);
        this.options.onStatus('disconnected');

        if (authInvalid) {
          await this.resetAuthAndReconnect();
        } else if (shouldReconnect) {
          this.scheduleReconnect(5000, 'Reconnecting in 5 seconds...');
        }
      } else if (connection === 'open') {
        console.log('✅ Connected to WhatsApp');
        this.authResetInProgress = false;
        this.options.onStatus('connected');
      }
    });

    // Save credentials on update
    this.sock.ev.on('creds.update', saveCreds);

    // Handle incoming messages
    this.sock.ev.on('messages.upsert', async (payload: { messages: any[]; type: string }) => {
      await this.handleMessagesUpsert(payload);
    });

    this.sock.ev.on('messaging-history.set', async (payload: {
      messages: any[];
      isLatest?: boolean;
      progress?: number | null;
      syncType?: string | number | null;
    }) => {
      await this.handleHistorySet(payload);
    });

    this.sock.ev.on('messages.delete', async ({ keys }: { keys: any[] }) => {
      for (const key of keys || []) {
        const deleted = await this.toDeletedMessage(key);
        if (deleted) {
          this.options.onDelete(deleted);
        }
      }
    });

    this.sock.ev.on('messages.update', async (updates: any[]) => {
      for (const update of updates || []) {
        const deleted = await this.deletedMessageFromUpdate(update);
        if (deleted) {
          this.options.onDelete(deleted);
        }
      }
    });
  }

  async handleMessagesUpsert({ messages, type }: { messages: any[]; type: string }): Promise<void> {
    console.log(`📩 messages.upsert type=${type} count=${messages.length}`);
    if (type !== 'notify') {
      await this.emitHistoryBatch(messages, { source: 'upsert', syncType: type });
      return;
    }

    for (const msg of messages) {
      const normalized = await this.normalizeMessage(msg, {
        downloadMedia: true,
        includeFromMe: false,
        loadGroupName: true,
      });
      if (!normalized) {
        continue;
      }

      console.log(
        `📨 inbound ${normalized.isGroup ? 'group' : 'direct'} chat=${normalized.sender || '<unknown>'} participant=${normalized.participant || '-'} pn=${normalized.participantPn || normalized.pn || '-'} text=${(normalized.content || '[media]').slice(0, 80)}`,
      );

      if (!normalized.isGroup && normalized.sender) {
        this.rememberChatTarget(normalized.sender, normalized.pn, normalized.pushName);
      }

      this.options.onMessage(normalized);
    }
  }

  async handleHistorySet(payload: {
    messages: any[];
    isLatest?: boolean;
    progress?: number | null;
    syncType?: string | number | null;
  }): Promise<void> {
    const { messages, isLatest, progress, syncType } = payload;
    console.log(`🕘 messaging-history.set count=${messages.length} progress=${progress ?? '-'} latest=${isLatest ?? '-'}`);
    await this.emitHistoryBatch(messages, {
      source: 'history_sync',
      isLatest,
      progress,
      syncType,
    });
  }

  private async emitHistoryBatch(messages: any[], meta: Omit<HistoryBatch, 'messages'>): Promise<void> {
    const normalizedMessages: HistoricalMessage[] = [];

    for (const msg of messages || []) {
      const normalized = await this.normalizeMessage(msg, {
        downloadMedia: false,
        includeFromMe: true,
        loadGroupName: false,
      });
      if (!normalized || normalized.isGroup) {
        continue;
      }

      if (normalized.sender) {
        this.rememberChatTarget(normalized.sender, normalized.pn, normalized.pushName);
      }

      normalizedMessages.push({
        ...normalized,
        fromMe: Boolean(normalized.fromMe),
      });
    }

    if (normalizedMessages.length === 0) {
      return;
    }

    this.options.onHistory({
      ...meta,
      messages: normalizedMessages,
    });
  }

  private async normalizeMessage(
    msg: any,
    options: {
      downloadMedia: boolean;
      includeFromMe: boolean;
      loadGroupName: boolean;
    },
  ): Promise<(InboundMessage & { fromMe?: boolean }) | null> {
    const remoteJid = String(msg?.key?.remoteJid || '');
    const isSelfChat = this.isSelfChatMessage(msg);
    if (msg?.key?.fromMe && !options.includeFromMe && !isSelfChat) {
      console.log(`↪️ Ignoring outbound/self message ${msg?.key?.id || ''}`);
      return null;
    }
    if (msg?.key?.fromMe && isSelfChat && !options.includeFromMe) {
      console.log(`🗂️ Capturing self-chat message ${msg?.key?.id || ''}`);
    }
    if (remoteJid === 'status@broadcast') {
      console.log(`↪️ Ignoring status broadcast ${msg?.key?.id || ''}`);
      return null;
    }

    const unwrapped = baileysExtractMessageContent(msg?.message);
    if (!unwrapped) {
      if (this.shouldLogUnsupportedMessage(remoteJid, options)) {
        console.log(`↪️ Ignoring unsupported message ${msg?.key?.id || ''} from ${remoteJid || '<unknown>'}`);
      }
      return null;
    }

    const parsed = await this.extractNormalizedContent(msg, unwrapped, options.downloadMedia);
    if (!parsed) {
      return null;
    }

    const isGroup = remoteJid.endsWith('@g.us');
    const sender = remoteJid;
    const pn = resolvePhoneIdentifier(String(msg?.key?.remoteJidAlt || ''), sender);
    const participant = isGroup ? String(msg?.key?.participant || '') : '';
    const participantPn = isGroup ? resolvePhoneIdentifier(String(msg?.key?.participantAlt || ''), participant) : '';
    const groupName = isGroup && sender && options.loadGroupName ? await this.getGroupName(sender) : '';

    return {
      id: String(msg?.key?.id || ''),
      sender,
      pn,
      content: parsed.content,
      timestamp: this.normalizeTimestamp(msg?.messageTimestamp),
      isGroup,
      ...(isSelfChat ? { isSelfChat: true } : {}),
      ...(participant ? { participant } : {}),
      ...(participantPn ? { participantPn } : {}),
      ...(sender && isGroup ? { groupId: sender } : {}),
      ...(groupName ? { groupName } : {}),
      ...(msg?.pushName ? { pushName: String(msg.pushName) } : {}),
      ...(parsed.mediaPaths.length > 0 ? { media: parsed.mediaPaths } : {}),
      ...(options.includeFromMe ? { fromMe: Boolean(msg?.key?.fromMe) } : {}),
    };
  }

  private async extractNormalizedContent(
    msg: any,
    message: any,
    downloadMedia: boolean,
  ): Promise<{ content: string; mediaPaths: string[] } | null> {
    const content = this.getTextContent(message);
    let fallbackContent: string | null = null;
    const mediaPaths: string[] = [];

    if (message.imageMessage) {
      fallbackContent = '[Image]';
      if (downloadMedia) {
        const path = await this.downloadMedia(msg, message.imageMessage.mimetype ?? undefined);
        if (path) {
          mediaPaths.push(path);
        }
      }
    } else if (message.documentMessage) {
      fallbackContent = '[Document]';
      if (downloadMedia) {
        const path = await this.downloadMedia(
          msg,
          message.documentMessage.mimetype ?? undefined,
          message.documentMessage.fileName ?? undefined,
        );
        if (path) {
          mediaPaths.push(path);
        }
      }
    } else if (message.videoMessage) {
      fallbackContent = '[Video]';
      if (downloadMedia) {
        const path = await this.downloadMedia(msg, message.videoMessage.mimetype ?? undefined);
        if (path) {
          mediaPaths.push(path);
        }
      }
    }

    const finalContent = content || (mediaPaths.length === 0 ? fallbackContent : '') || '';
    if (!finalContent && mediaPaths.length === 0) {
      return null;
    }

    return { content: finalContent, mediaPaths };
  }

  private async downloadMedia(msg: any, mimetype?: string, fileName?: string): Promise<string | null> {
    try {
      const mediaDir = join(homedir(), '.nanobot', 'media');
      await mkdir(mediaDir, { recursive: true });

      const buffer = await downloadMediaMessage(msg, 'buffer', {}) as Buffer;

      let outFilename: string;
      if (fileName) {
        // Documents have a filename — use it with a unique prefix to avoid collisions
        const prefix = `wa_${Date.now()}_${randomBytes(4).toString('hex')}_`;
        outFilename = prefix + fileName;
      } else {
        const mime = mimetype || 'application/octet-stream';
        // Derive extension from mimetype subtype (e.g. "image/png" → ".png", "application/pdf" → ".pdf")
        const ext = '.' + (mime.split('/').pop()?.split(';')[0] || 'bin');
        outFilename = `wa_${Date.now()}_${randomBytes(4).toString('hex')}${ext}`;
      }

      const filepath = join(mediaDir, outFilename);
      await writeFile(filepath, buffer);

      return filepath;
    } catch (err) {
      console.error('Failed to download media:', err);
      return null;
    }
  }

  private getTextContent(message: any): string | null {
    // Text message
    if (message.conversation) {
      return message.conversation;
    }

    // Extended text (reply, link preview)
    if (message.extendedTextMessage?.text) {
      return message.extendedTextMessage.text;
    }

    // Image with optional caption
    if (message.imageMessage) {
      return message.imageMessage.caption || '';
    }

    // Video with optional caption
    if (message.videoMessage) {
      return message.videoMessage.caption || '';
    }

    // Document with optional caption
    if (message.documentMessage) {
      return message.documentMessage.caption || '';
    }

    // Voice/Audio message
    if (message.audioMessage) {
      return `[Voice Message]`;
    }

    return null;
  }

  async sendMessage(to: string, text: string): Promise<void> {
    if (!this.sock) {
      throw new Error('Not connected');
    }

    await this.sock.sendMessage(to, { text });
  }

  getChatTarget(chatId: string): ChatTarget | null {
    return this.chatTargets.get(chatId) || null;
  }

  async disconnect(): Promise<void> {
    if (this.sock) {
      this.sock.end(undefined);
      this.sock = null;
    }
  }

  private scheduleReconnect(delayMs: number, message: string): void {
    if (this.reconnecting) {
      return;
    }

    this.reconnecting = true;
    console.log(message);
    setTimeout(() => {
      this.reconnecting = false;
      void this.connect().catch((error) => {
        console.error('Failed to reconnect to WhatsApp:', error);
      });
    }, delayMs);
  }

  private async resetAuthAndReconnect(): Promise<void> {
    if (this.authResetInProgress || this.reconnecting) {
      return;
    }

    this.authResetInProgress = true;
    console.log('⚠️ Existing Baileys auth session is invalid or logged out.');
    console.log(`🧹 Clearing saved auth state at ${this.options.authDir} so WhatsApp can issue a new QR code...`);

    try {
      await this.disconnect();
      await this.clearAuthState();
      await this.ensureAuthDir();
    } catch (error) {
      console.warn('Failed to reset Baileys auth state cleanly:', error);
    }

    this.scheduleReconnect(
      INVALID_AUTH_RECONNECT_DELAY_MS,
      '🔄 Reconnecting in 5 seconds. A new QR code should appear shortly and may take a little longer than usual...'
    );
  }

  private async ensureAuthDir(): Promise<void> {
    await mkdir(this.options.authDir, { recursive: true });
  }

  private async clearAuthState(): Promise<void> {
    await rm(this.options.authDir, { recursive: true, force: true });
  }

  private rememberChatTarget(chatId: string, pn: string, pushName?: string): void {
    const existing = this.chatTargets.get(chatId);
    const searchTerms = new Set(existing?.searchTerms || []);
    const bareChatId = chatId.includes('@') ? chatId.split('@')[0] : chatId;
    const phone = this.normalizePhone(pn) || this.normalizePhone(bareChatId) || existing?.phone;

    for (const candidate of [pushName, pn, bareChatId, phone]) {
      const normalized = typeof candidate === 'string' ? candidate.trim() : '';
      if (normalized) {
        searchTerms.add(normalized);
      }
    }

    this.chatTargets.set(chatId, {
      chatId,
      ...(phone ? { phone } : {}),
      searchTerms: Array.from(searchTerms),
    });
  }

  private normalizePhone(value: string): string | undefined {
    const digits = value.replace(/\D/g, '');
    return digits ? digits : undefined;
  }

  private shouldLogUnsupportedMessage(
    remoteJid: string,
    options: {
      downloadMedia: boolean;
      includeFromMe: boolean;
      loadGroupName: boolean;
    },
  ): boolean {
    if (options.includeFromMe) {
      return false;
    }
    if (remoteJid.endsWith('@g.us')) {
      return false;
    }
    return true;
  }

  private normalizeTimestamp(value: unknown): number {
    const numeric = Number(value || 0);
    return Number.isFinite(numeric) ? numeric : 0;
  }

  private async getGroupName(groupId: string): Promise<string> {
    const cached = this.groupNames.get(groupId);
    if (cached) {
      return cached;
    }

    if (!this.sock) {
      return '';
    }

    try {
      const metadata = await this.sock.groupMetadata(groupId);
      const subject = typeof metadata?.subject === 'string' ? metadata.subject.trim() : '';
      if (subject) {
        this.groupNames.set(groupId, subject);
      }
      return subject;
    } catch (error) {
      console.warn(`Failed to load WhatsApp group metadata for ${groupId}:`, error);
      return '';
    }
  }

  private isSelfChatMessage(msg: any): boolean {
    const remoteJid = String(msg?.key?.remoteJid || '');
    if (!remoteJid || remoteJid.endsWith('@g.us') || !this.sock?.user) {
      return false;
    }

    const candidates = [
      String(this.sock.user.id || ''),
      String(this.sock.user.lid || ''),
      String(this.sock.user.pn || ''),
      String(this.sock.user.phone || ''),
    ].filter(Boolean);

    return candidates.some((candidate) => isSelfDirectChat(remoteJid, candidate));
  }

  private async deletedMessageFromUpdate(update: any): Promise<DeletedMessage | null> {
    const protocolMessage = update?.update?.message?.protocolMessage;
    const deletedKey = protocolMessage?.key;
    if (!deletedKey?.id) {
      return null;
    }

    const type = protocolMessage?.type;
    if (type !== undefined && type !== 0 && type !== 'REVOKE') {
      return null;
    }

    return this.toDeletedMessage({
      ...deletedKey,
      remoteJid: deletedKey.remoteJid || update?.key?.remoteJid,
      participant: deletedKey.participant || update?.key?.participant,
      participantAlt: deletedKey.participantAlt || update?.key?.participantAlt,
      remoteJidAlt: deletedKey.remoteJidAlt || update?.key?.remoteJidAlt,
    });
  }

  private async toDeletedMessage(key: any): Promise<DeletedMessage | null> {
    const deletedMessageId = String(key?.id || '').trim();
    const sender = String(key?.remoteJid || '').trim();
    if (!deletedMessageId || !sender || sender === 'status@broadcast') {
      return null;
    }

    const isGroup = sender.endsWith('@g.us');
    const pn = resolvePhoneIdentifier(String(key?.remoteJidAlt || ''), sender);
    const participant = isGroup ? String(key?.participant || '') : '';
    const participantPn = isGroup ? resolvePhoneIdentifier(String(key?.participantAlt || ''), participant) : '';
    const groupName = isGroup ? await this.getGroupName(sender) : '';

    return {
      deletedMessageId,
      sender,
      pn,
      timestamp: Math.floor(Date.now() / 1000),
      isGroup,
      ...(participant ? { participant } : {}),
      ...(participantPn ? { participantPn } : {}),
      ...(isGroup ? { groupId: sender } : {}),
      ...(groupName ? { groupName } : {}),
      deletedBySender: true,
    };
  }
}
