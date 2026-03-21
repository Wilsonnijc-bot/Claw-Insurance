import test from 'node:test';
import assert from 'node:assert/strict';

import { DraftComposer } from '../dist/draft.js';

class FakeLocator {
  constructor(page, role) {
    this.page = page;
    this.role = role;
  }

  first() {
    return this;
  }

  async waitFor() {
    if (!(await this.count())) {
      throw new Error(`locator ${this.role} not visible`);
    }
  }

  async click() {
    this.page.focus = this.role;
    if (this.role === 'result') {
      this.page.composeVisible = true;
    }
  }

  async count() {
    switch (this.role) {
      case 'compose':
        return this.page.composeVisible ? 1 : 0;
      case 'search':
        return this.page.searchVisible ? 1 : 0;
      case 'result':
        return this.page.searchMatch ? 1 : 0;
      default:
        return 0;
    }
  }

  async innerText() {
    if (this.role === 'compose') {
      return this.page.composeText;
    }
    return '';
  }
}

class FakePage {
  constructor({
    openByPhoneSuccess = true,
    searchVisible = true,
    searchMatch = false,
    composeText = '',
    historySnapshots = [],
    readyAfterWaits = 0,
    initialUrl = 'about:blank',
  } = {}) {
    this.openByPhoneSuccess = openByPhoneSuccess;
    this.searchVisible = searchVisible;
    this.searchMatch = searchMatch;
    this.composeText = composeText;
    this.composeVisible = composeText.length > 0;
    this.focus = 'search';
    this.gotoUrls = [];
    this.insertedTexts = [];
    this.pressedKeys = [];
    this.searchText = '';
    this.historySnapshots = historySnapshots;
    this.historyIndex = 0;
    this.readyAfterWaits = readyAfterWaits;
    this.waitCalls = 0;
    this.currentUrl = initialUrl;
    this.keyboard = {
      insertText: async (text) => {
        this.insertedTexts.push(text);
        if (this.focus === 'compose') {
          this.composeText += text;
          this.composeVisible = true;
        } else {
          this.searchText += text;
        }
      },
      press: async (key) => {
        this.pressedKeys.push(key);
        if (key === 'Backspace') {
          if (this.focus === 'compose') {
            this.composeText = '';
          } else {
            this.searchText = '';
          }
        }
      },
    };
  }

  async goto(url) {
    this.currentUrl = url;
    this.gotoUrls.push(url);
    if (url.includes('/send?phone=')) {
      this.composeVisible = this.openByPhoneSuccess;
    }
    if (url === 'https://web.whatsapp.com/') {
      if (this.readyAfterWaits <= 0) {
        this.searchVisible = true;
        if (!this.composeText) {
          this.composeVisible = false;
        }
      } else {
        this.searchVisible = false;
        this.composeVisible = false;
      }
    }
  }

  async bringToFront() {}

  url() {
    return this.currentUrl;
  }

  async waitForTimeout() {
    this.waitCalls += 1;
    if (this.waitCalls >= this.readyAfterWaits) {
      this.searchVisible = true;
    }
  }

  async evaluate(_fn, arg = {}) {
    if (arg.action === 'extract_history') {
      return {
        messages: this.historySnapshots[this.historyIndex] || [],
        atTop: this.historyIndex >= this.historySnapshots.length - 1,
      };
    }
    if (arg.action === 'scroll_history_up') {
      if (this.historyIndex < this.historySnapshots.length - 1) {
        this.historyIndex += 1;
        return true;
      }
      return false;
    }
    return null;
  }

  locator(selector) {
    if (selector.includes('footer')) {
      return new FakeLocator(this, 'compose');
    }
    if (selector.includes('Search input textbox') || selector.includes('data-tab="3"')) {
      return new FakeLocator(this, 'search');
    }
    if (selector.includes('title=') || selector.includes('title*=')
      || selector.includes('aria-label*=') || selector.includes('data-testid*=')) {
      return new FakeLocator(this, 'result');
    }
    if (selector.includes('role="textbox"')) {
      return new FakeLocator(this, this.composeVisible ? 'compose' : 'search');
    }
    return new FakeLocator(this, 'missing');
  }
}

class FakeContext {
  constructor(pages) {
    this._pages = pages;
  }

  pages() {
    return this._pages;
  }

  async newPage() {
    const page = new FakePage();
    this._pages.push(page);
    return page;
  }

  async close() {}

  on() {}
}

class FakeAttachedBrowser {
  constructor(context) {
    this.context = context;
  }

  contexts() {
    return [this.context];
  }

  async close() {}

  on() {}
}

class FakeBrowserConnector {
  constructor(page, { failConnectCalls = 0 } = {}) {
    this.context = new FakeContext([page]);
    this.browser = new FakeAttachedBrowser(this.context);
    this.launchCalls = 0;
    this.connectCalls = [];
    this.failConnectCalls = failConnectCalls;
  }

  async launchPersistentContext() {
    this.launchCalls += 1;
    return this.context;
  }

  async connectOverCDP(endpointURL) {
    this.connectCalls.push(endpointURL);
    if (this.failConnectCalls > 0) {
      this.failConnectCalls -= 1;
      throw new Error('cdp unavailable');
    }
    return this.browser;
  }
}

class FakeSpawnedProcess {
  constructor() {
    this.exitCode = null;
    this.killed = false;
    this.listeners = new Map();
    queueMicrotask(() => this.emit('spawn'));
  }

  once(event, listener) {
    this.listeners.set(event, listener);
    return this;
  }

  emit(event, value) {
    const listener = this.listeners.get(event);
    if (!listener) {
      return;
    }
    this.listeners.delete(event);
    listener(value);
  }

  unref() {}
}

test('prepareDraft fills compose box without sending when phone lookup succeeds', async () => {
  const page = new FakePage({ openByPhoneSuccess: true });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.prepareDraft(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
    'Hello Alice',
  );

  assert.equal(result.status, 'draft_prepared');
  assert.ok(page.gotoUrls.some((url) => url.includes('/send?phone=1234567890')));
  assert.equal(page.composeText, 'Hello Alice');
  assert.ok(!page.pressedKeys.includes('Enter'));
});

test('prepareDraft falls back to chat search when phone open is unavailable', async () => {
  const page = new FakePage({ openByPhoneSuccess: false, searchMatch: true });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.prepareDraft(
    { chatId: 'alice@s.whatsapp.net', searchTerms: ['Alice'] },
    'Draft reply',
  );

  assert.equal(result.status, 'draft_prepared');
  assert.deepEqual(page.insertedTexts, ['Alice', 'Draft reply']);
});

test('prepareDraft reports busy compose box instead of overwriting text', async () => {
  const page = new FakePage({ openByPhoneSuccess: true, composeText: 'Existing unsent draft' });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.prepareDraft(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
    'Fresh reply',
  );

  assert.equal(result.status, 'compose_box_busy');
  assert.equal(page.composeText, 'Existing unsent draft');
});

test('scrapeHistory collects visible and older messages without sending anything', async () => {
  const page = new FakePage({
    openByPhoneSuccess: true,
    historySnapshots: [
      [
        {
          id: 'true_msg-2',
          content: 'Recent reply',
          fromMe: true,
          metaText: '[10:12, 9/3/2026]',
        },
      ],
      [
        {
          id: 'false_msg-1',
          content: 'Older hello',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
        {
          id: 'true_msg-2',
          content: 'Recent reply',
          fromMe: true,
          metaText: '[10:12, 9/3/2026]',
        },
      ],
    ],
  });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.deepEqual(
    result.messages.map((item) => ({
      id: item.id,
      content: item.content,
      fromMe: item.fromMe,
      pushName: item.pushName || '',
    })),
    [
      { id: 'false_msg-1', content: 'Older hello', fromMe: false, pushName: 'Alice' },
      { id: 'true_msg-2', content: 'Recent reply', fromMe: true, pushName: '' },
    ],
  );
  assert.ok(result.messages.every((item) => item.timestamp));
  assert.ok(!page.pressedKeys.includes('Enter'));
});

test('scrapeHistory waits for WhatsApp Web login before scraping', async () => {
  const page = new FakePage({
    openByPhoneSuccess: true,
    readyAfterWaits: 1,
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello after login',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.ok(page.waitCalls >= 1);
  assert.equal(result.messages[0].content, 'Hello after login');
});

test('prepareDraft attaches to existing CDP WhatsApp tab without launching a separate profile', async () => {
  const page = new FakePage({
    openByPhoneSuccess: true,
    initialUrl: 'https://web.whatsapp.com/',
  });
  const connector = new FakeBrowserConnector(page);
  const composer = new DraftComposer('/tmp/wa-web', connector, 'cdp', 'http://127.0.0.1:9222');

  const result = await composer.prepareDraft(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
    'Hello Alice',
  );

  assert.equal(result.status, 'draft_prepared');
  assert.deepEqual(connector.connectCalls, ['http://127.0.0.1:9222']);
  assert.equal(connector.launchCalls, 0);
  assert.ok(!page.gotoUrls.includes('https://web.whatsapp.com/'));
});

test('prepareDraft launches a debuggable Chrome when CDP is missing and then attaches', async () => {
  const page = new FakePage({
    openByPhoneSuccess: true,
    initialUrl: 'https://web.whatsapp.com/',
  });
  const connector = new FakeBrowserConnector(page, { failConnectCalls: 1 });
  const spawnCalls = [];
  const composer = new DraftComposer(
    '/tmp/wa-web',
    connector,
    'cdp',
    'http://127.0.0.1:9222',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    (command, args, options) => {
      spawnCalls.push({ command, args, options });
      return new FakeSpawnedProcess();
    },
  );

  const result = await composer.prepareDraft(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
    'Hello Alice',
  );

  assert.equal(result.status, 'draft_prepared');
  assert.equal(connector.launchCalls, 0);
  assert.deepEqual(connector.connectCalls, ['http://127.0.0.1:9222', 'http://127.0.0.1:9222']);
  assert.equal(spawnCalls.length, 1);
  assert.equal(spawnCalls[0].command, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
  assert.ok(spawnCalls[0].args.includes('--remote-debugging-port=9222'));
  assert.ok(spawnCalls[0].args.includes('--user-data-dir=/tmp/wa-web'));
  assert.ok(spawnCalls[0].args.includes('https://web.whatsapp.com/'));
});
