import test from 'node:test';
import assert from 'node:assert/strict';

import { BridgeServer } from '../dist/server.js';

test('prepare_draft prefers explicit target over remembered chat target', async () => {
  const server = new BridgeServer(3001, '/tmp/auth', '/tmp/profile');
  let receivedTarget = null;

  server.wa = {
    getChatTarget() {
      return { chatId: 'fallback@lid', phone: '99999999', searchTerms: ['Fallback'] };
    },
  };
  server.draftComposer = {
    async prepareDraft(target, text) {
      receivedTarget = { target, text };
      return { status: 'draft_prepared' };
    },
  };

  const ack = await server.handleCommand({
    type: 'prepare_draft',
    to: '123@s.whatsapp.net',
    text: 'Hello Alice',
    target: {
      chatId: '123@s.whatsapp.net',
      phone: '1234567890',
      searchTerms: ['Alice'],
    },
  });

  assert.equal(ack.status, 'draft_prepared');
  assert.deepEqual(receivedTarget, {
    target: {
      chatId: '123@s.whatsapp.net',
      phone: '1234567890',
      searchTerms: ['Alice'],
    },
    text: 'Hello Alice',
  });
});

test('prepare_draft falls back to remembered chat target when explicit target is absent', async () => {
  const server = new BridgeServer(3001, '/tmp/auth', '/tmp/profile');
  let receivedTarget = null;

  server.wa = {
    getChatTarget() {
      return { chatId: 'alice@lid', phone: '1234567890', searchTerms: ['Alice'] };
    },
  };
  server.draftComposer = {
    async prepareDraft(target, text) {
      receivedTarget = { target, text };
      return { status: 'draft_prepared' };
    },
  };

  const ack = await server.handleCommand({
    type: 'prepare_draft',
    to: 'alice@lid',
    text: 'Draft reply',
  });

  assert.equal(ack.status, 'draft_prepared');
  assert.deepEqual(receivedTarget, {
    target: { chatId: 'alice@lid', phone: '1234567890', searchTerms: ['Alice'] },
    text: 'Draft reply',
  });
});

test('prepare_draft returns chat_not_found when neither explicit nor remembered target exists', async () => {
  const server = new BridgeServer(3001, '/tmp/auth', '/tmp/profile');

  server.wa = {
    getChatTarget() {
      return null;
    },
  };
  server.draftComposer = {
    async prepareDraft() {
      throw new Error('should not run');
    },
  };

  const ack = await server.handleCommand({
    type: 'prepare_draft',
    to: 'missing@lid',
    text: 'Draft reply',
  });

  assert.equal(ack.status, 'chat_not_found');
});

test('scrape_reply_targets_history broadcasts web scrape history for each scraped target', async () => {
  const server = new BridgeServer(3001, '/tmp/auth', '/tmp/profile');
  const broadcasts = [];
  server.broadcast = (msg) => broadcasts.push(msg);
  server.historyParser = {
    async scrapeReplyTargets(targets) {
      return {
        status: 'history_scraped',
        results: [
          {
            target: targets[0],
            status: 'history_scraped',
            messages: [
              {
                id: `hist-${targets[0].chatId}`,
                content: 'Hello from web',
                timestamp: '2026-03-09T10:11:37.000Z',
                fromMe: false,
                pushName: 'Alice',
              },
            ],
          },
          {
            target: targets[1],
            status: 'chat_not_found',
          },
        ],
      };
    },
  };

  const ack = await server.handleCommand({
    type: 'scrape_reply_targets_history',
    targets: [
      {
        chatId: '123@s.whatsapp.net',
        phone: '1234567890',
        searchTerms: ['Alice'],
      },
      {
        chatId: 'missing',
        phone: '99999999',
        searchTerms: ['Missing'],
      },
    ],
  });

  assert.equal(ack.status, 'history_scraped');
  assert.equal(ack.scrapedTargets, 1);
  assert.equal(ack.scrapedMessages, 1);
  assert.equal(ack.missedTargets, 1);
  assert.deepEqual(ack.importPhones, ['1234567890']);
  assert.deepEqual(broadcasts, [
    {
      type: 'history',
      source: 'web_scrape',
      target: '123@s.whatsapp.net',
      messages: [
        {
          id: 'hist-123@s.whatsapp.net',
          sender: '123@s.whatsapp.net',
          pn: '1234567890',
          content: 'Hello from web',
          timestamp: 1773051097,
          fromMe: false,
          isGroup: false,
          pushName: 'Alice',
        },
      ],
    },
  ]);
});

test('scrape_direct_history returns login_required when browser scrape cannot start', async () => {
  const server = new BridgeServer(3001, '/tmp/auth', '/tmp/profile');
  server.historyParser = {
    async scrapeHistory() {
      return { status: 'login_required', detail: 'Log in first' };
    },
  };

  const ack = await server.handleCommand({
    type: 'scrape_direct_history',
    target: {
      chatId: '123@s.whatsapp.net',
      phone: '1234567890',
      searchTerms: ['Alice'],
    },
  });

  assert.equal(ack.status, 'login_required');
  assert.equal(ack.detail, 'Log in first');
});

test('scrape_direct_history forwards simplified parse failures', async () => {
  const server = new BridgeServer(3001, '/tmp/auth', '/tmp/profile');
  server.historyParser = {
    async scrapeHistory() {
      return { status: 'window_launch_failed', detail: 'Could not open a fresh WhatsApp Web window' };
    },
  };

  const ack = await server.handleCommand({
    type: 'scrape_direct_history',
    requestId: 'req-1',
    target: {
      chatId: '123@s.whatsapp.net',
      phone: '1234567890',
      searchTerms: ['Alice'],
    },
  });

  assert.equal(ack.status, 'window_launch_failed');
  assert.equal(ack.detail, 'Could not open a fresh WhatsApp Web window');
  assert.equal(ack.requestId, 'req-1');
});
