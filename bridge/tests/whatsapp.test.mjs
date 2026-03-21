import test from 'node:test';
import assert from 'node:assert/strict';

import { WhatsAppClient, extractPhoneFromJid, isSelfDirectChat, resolvePhoneIdentifier } from '../dist/whatsapp.js';

test('extractPhoneFromJid returns phone digits for legacy direct chat JIDs', () => {
  assert.equal(extractPhoneFromJid('85212345678@s.whatsapp.net'), '85212345678');
  assert.equal(extractPhoneFromJid('+85212345678@c.us'), '+85212345678');
  assert.equal(extractPhoneFromJid('1203630@g.us'), '');
  assert.equal(extractPhoneFromJid('user@lid'), '');
});

test('resolvePhoneIdentifier prefers pn and falls back to phone-like sender JID', () => {
  assert.equal(resolvePhoneIdentifier('+852 1234 5678', 'ignored@lid'), '+85212345678');
  assert.equal(resolvePhoneIdentifier('', '85212345678@s.whatsapp.net'), '85212345678');
  assert.equal(resolvePhoneIdentifier('', 'user@lid'), '');
});

test('isSelfDirectChat matches own direct JID and rejects groups', () => {
  assert.equal(isSelfDirectChat('85212345678@s.whatsapp.net', '85212345678:12@s.whatsapp.net'), true);
  assert.equal(isSelfDirectChat('85212345678@s.whatsapp.net', '85212345679@s.whatsapp.net'), false);
  assert.equal(isSelfDirectChat('1203630@g.us', '85212345678@s.whatsapp.net'), false);
});

test('non-notify upserts are emitted as direct history batches', async () => {
  const historyBatches = [];
  const liveMessages = [];
  const client = new WhatsAppClient({
    authDir: '/tmp/auth',
    onMessage: (msg) => liveMessages.push(msg),
    onHistory: (batch) => historyBatches.push(batch),
    onDelete() {},
    onQR() {},
    onStatus() {},
  });

  await client.handleMessagesUpsert({
    type: 'append',
    messages: [
      {
        key: {
          id: 'hist-1',
          remoteJid: '85212345678@s.whatsapp.net',
          fromMe: true,
        },
        message: {
          conversation: 'sent earlier',
        },
        messageTimestamp: 1700000000,
      },
      {
        key: {
          id: 'group-ignored',
          remoteJid: '120363000000@g.us',
          participant: 'alice@lid',
          fromMe: false,
        },
        message: {
          conversation: 'group history',
        },
        messageTimestamp: 1700000001,
      },
    ],
  });

  assert.equal(liveMessages.length, 0);
  assert.equal(historyBatches.length, 1);
  assert.equal(historyBatches[0].source, 'upsert');
  assert.equal(historyBatches[0].messages.length, 1);
  assert.deepEqual(historyBatches[0].messages[0], {
    id: 'hist-1',
    sender: '85212345678@s.whatsapp.net',
    pn: '85212345678',
    content: 'sent earlier',
    timestamp: 1700000000,
    fromMe: true,
    isGroup: false,
  });
});

test('messaging-history.set emits normalized direct history with media placeholders', async () => {
  const historyBatches = [];
  const client = new WhatsAppClient({
    authDir: '/tmp/auth',
    onMessage() {},
    onHistory: (batch) => historyBatches.push(batch),
    onDelete() {},
    onQR() {},
    onStatus() {},
  });

  await client.handleHistorySet({
    isLatest: true,
    progress: 100,
    syncType: 'FULL',
    messages: [
      {
        key: {
          id: 'hist-2',
          remoteJid: 'alice@lid',
          remoteJidAlt: '+852 1234 5678',
          fromMe: false,
        },
        pushName: 'Alice',
        message: {
          imageMessage: {},
        },
        messageTimestamp: 1700000002,
      },
    ],
  });

  assert.equal(historyBatches.length, 1);
  assert.equal(historyBatches[0].source, 'history_sync');
  assert.equal(historyBatches[0].isLatest, true);
  assert.equal(historyBatches[0].progress, 100);
  assert.equal(historyBatches[0].syncType, 'FULL');
  assert.deepEqual(historyBatches[0].messages[0], {
    id: 'hist-2',
    sender: 'alice@lid',
    pn: '+85212345678',
    content: '[Image]',
    timestamp: 1700000002,
    fromMe: false,
    isGroup: false,
    pushName: 'Alice',
  });
});

test('logged-out auth is cleared and reconnect is scheduled with a longer wait for a fresh QR', async () => {
  const client = new WhatsAppClient({
    authDir: '/tmp/auth',
    onMessage() {},
    onHistory() {},
    onDelete() {},
    onQR() {},
    onStatus() {},
  });

  let disconnected = 0;
  let clearedAuth = 0;
  let ensuredAuthDir = 0;
  let reconnectDelay = null;
  let reconnectMessage = null;

  client.disconnect = async () => {
    disconnected += 1;
  };
  client.clearAuthState = async () => {
    clearedAuth += 1;
  };
  client.ensureAuthDir = async () => {
    ensuredAuthDir += 1;
  };
  client.scheduleReconnect = (delayMs, message) => {
    reconnectDelay = delayMs;
    reconnectMessage = message;
  };

  await client.resetAuthAndReconnect();

  assert.equal(disconnected, 1);
  assert.equal(clearedAuth, 1);
  assert.equal(ensuredAuthDir, 1);
  assert.equal(reconnectDelay, 5000);
  assert.match(reconnectMessage, /QR code should appear shortly/);
});
