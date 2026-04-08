import test from 'node:test';
import assert from 'node:assert/strict';

import { DraftComposer } from '../dist/draft.js';
import { HistoryParser } from '../dist/history.js';

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
    searchText = '',
    searchResetWorks = true,
    searchClickOpensChat = true,
    composeText = '',
    historySnapshots = [],
    readyAfterWaits = 0,
    initialUrl = 'about:blank',
    sidebarPages = [],
    openedChatHeaderText = '',
    pageHeaderText = '',
    composeLabel = '',
    composePlaceholder = '',
    pageTitle = '',
  } = {}) {
    this.openByPhoneSuccess = openByPhoneSuccess;
    this.searchVisible = searchVisible;
    this.searchMatch = searchMatch;
    this.searchResetWorks = searchResetWorks;
    this.searchClickOpensChat = searchClickOpensChat;
    this.composeText = composeText;
    this.composeVisible = composeText.length > 0;
    this.focus = 'search';
    this.gotoUrls = [];
    this.insertedTexts = [];
    this.pressedKeys = [];
    this.searchText = searchText;
    this.historySnapshots = historySnapshots;
    this.historyIndex = 0;
    this.readyAfterWaits = readyAfterWaits;
    this.waitCalls = 0;
    this.currentUrl = initialUrl;
    this.sidebarPages = sidebarPages;
    this.sidebarIndex = 0;
    this.openedChatHeaderText = openedChatHeaderText;
    this.pageHeaderText = pageHeaderText;
    this.composeLabel = composeLabel;
    this.composePlaceholder = composePlaceholder;
    this.pageTitle = pageTitle;
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
    if (arg.action === 'opened_chat_snapshot') {
      const headerText = this.openedChatHeaderText || this.searchText;
      return {
        url: this.currentUrl,
        title: this.pageTitle || this.pageHeaderText || this.searchText,
        headerText,
        composeLabel: this.composeLabel || headerText,
        composePlaceholder: this.composePlaceholder,
      };
    }
    if (arg.action === 'search_box_state') {
      return {
        found: this.searchVisible,
        value: this.searchVisible ? this.searchText.trim() : '',
      };
    }
    if (arg.action === 'reset_search_box') {
      if (!this.searchVisible || !this.searchResetWorks) {
        return false;
      }
      this.focus = 'search';
      this.searchText = '';
      return true;
    }
    if (arg.action === 'focus_exact_search_box') {
      if (!this.searchVisible) {
        return false;
      }
      this.focus = 'search';
      return true;
    }
    if (arg.action === 'search_result_row_visible') {
      return this.searchMatch;
    }
    if (arg.action === 'click_search_result_row') {
      if (!this.searchMatch) {
        return false;
      }
      this.focus = 'result';
      if (this.searchClickOpensChat) {
        this.composeVisible = true;
        this.currentUrl = 'https://web.whatsapp.com/';
        if (!this.openedChatHeaderText) {
          this.openedChatHeaderText = this.searchText;
        }
      }
      return true;
    }
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
    if (arg.action === 'find_sidebar_targets') {
      const visible = this.sidebarPages[this.sidebarIndex] || [];
      return (arg.targets || [])
        .map((target) => target.chatId)
        .filter((chatId) => visible.includes(chatId));
    }
    if (arg.action === 'open_sidebar_target') {
      const visible = this.sidebarPages[this.sidebarIndex] || [];
      if (visible.includes(arg.target?.chatId)) {
        this.composeVisible = true;
        this.currentUrl = 'https://web.whatsapp.com/';
        this.openedChatHeaderText = arg.target?.phone || arg.target?.searchTerms?.[0] || arg.target?.chatId || '';
        return true;
      }
      return false;
    }
    if (arg.action === 'scroll_sidebar_down') {
      if (this.sidebarIndex < this.sidebarPages.length - 1) {
        this.sidebarIndex += 1;
        return true;
      }
      return false;
    }
    if (arg.action === 'sidebar_ready') {
      return this.currentUrl === 'https://web.whatsapp.com/';
    }
    if (Array.isArray(arg.selectors)) {
      const selectors = arg.selectors.join(' ');
      if ((selectors.includes('Search input textbox') || selectors.includes('data-tab="3"')) && this.searchVisible) {
        this.focus = 'search';
        return true;
      }
      if (
        (selectors.includes('title=') || selectors.includes('title*=') || selectors.includes('aria-label*=') || selectors.includes('data-testid*='))
        && this.searchMatch
      ) {
        this.focus = 'result';
        this.composeVisible = true;
        return true;
      }
      return false;
    }
    if (Object.keys(arg).length === 0) {
      return {
        url: this.currentUrl,
        title: this.pageTitle || this.searchText,
        headerText: this.pageHeaderText || this.searchText,
      };
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

test('prepareDraft clears stale search text before chat search when phone open is unavailable', async () => {
  const page = new FakePage({
    openByPhoneSuccess: false,
    searchMatch: true,
    searchText: 'Stale search',
  });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.prepareDraft(
    { chatId: 'alice@s.whatsapp.net', searchTerms: ['Alice'] },
    'Draft reply',
  );

  assert.equal(result.status, 'draft_prepared');
  assert.deepEqual(page.insertedTexts, ['Alice', 'Draft reply']);
  assert.equal(page.searchText, 'Alice');
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
    searchMatch: true,
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
  const composer = new HistoryParser('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

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
    searchMatch: true,
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
  const composer = new HistoryParser('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.ok(page.waitCalls >= 1);
  assert.equal(result.messages[0].content, 'Hello after login');
});

test('scrapeHistory verifies the open chat from the main chat header instead of the first page header', async () => {
  const page = new FakePage({
    searchMatch: true,
    pageHeaderText: '2',
    openedChatHeaderText: '1234567890',
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello from main header',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const composer = new HistoryParser('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.messages[0].content, 'Hello from main header');
});

test('scrapeReplyTargets scans visible sidebar chats without phone navigation', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchText: 'Stale sidebar filter',
    sidebarPages: [['123@s.whatsapp.net']],
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello from sidebar',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const connector = new FakeBrowserConnector(page);
  const composer = new HistoryParser('/tmp/wa-web', connector, 'cdp', 'http://127.0.0.1:9222');

  const result = await composer.scrapeReplyTargets([
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  ]);

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.results[0].status, 'history_scraped');
  assert.equal(result.results[0].messages[0].content, 'Hello from sidebar');
  assert.ok(!page.gotoUrls.some((url) => url.includes('/send?phone=')));
  assert.equal(page.searchText, '');
});

test('prepareDraft in cdp mode is disabled before any CDP attach happens', async () => {
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

  assert.equal(result.status, 'not_ready');
  assert.equal(
    result.detail,
    'WhatsApp Web draft placement is disabled in CDP mode; CDP is reserved for history parsing.',
  );
  assert.deepEqual(connector.connectCalls, []);
  assert.equal(connector.launchCalls, 0);
  assert.ok(!page.gotoUrls.includes('https://web.whatsapp.com/'));
});

test('scrapeHistory reuses an attached CDP page when it is already usable', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchMatch: true,
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello from reused session',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const connector = new FakeBrowserConnector(page);
  const spawnCalls = [];
  const composer = new HistoryParser(
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

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.messages[0].content, 'Hello from reused session');
  assert.equal(spawnCalls.length, 0);
  assert.deepEqual(connector.connectCalls, ['http://127.0.0.1:9222']);
});

test('scrapeHistory does not reopen a CDP window for true chat_not_found results', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: true,
    searchMatch: false,
    searchText: 'Poisoned query',
  });
  const connector = new FakeBrowserConnector(page);
  const spawnCalls = [];
  const composer = new HistoryParser(
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

  const result = await composer.scrapeHistory(
    { chatId: 'missing@s.whatsapp.net', phone: '9999999999', searchTerms: ['Missing Client'] },
  );

  assert.equal(result.status, 'chat_not_found');
  assert.equal(spawnCalls.length, 0);
  assert.equal(page.searchText, 'Missing Client');
});

test('scrapeHistory retries in a fresh CDP window when attached page search cannot be reset cleanly', async () => {
  const firstPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: true,
    searchMatch: true,
    searchText: 'Stale query',
    searchResetWorks: false,
  });
  const secondPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchMatch: true,
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello after search reset retry',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const browsers = [
    new FakeAttachedBrowser(new FakeContext([firstPage])),
    new FakeAttachedBrowser(new FakeContext([secondPage])),
  ];
  const connector = {
    launchCalls: 0,
    connectCalls: [],
    async launchPersistentContext() {
      this.launchCalls += 1;
      throw new Error('launch mode not expected');
    },
    async connectOverCDP(endpointURL) {
      this.connectCalls.push(endpointURL);
      return browsers[Math.min(this.connectCalls.length - 1, browsers.length - 1)];
    },
  };
  const spawnCalls = [];

  const composer = new HistoryParser(
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

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.messages[0].content, 'Hello after search reset retry');
  assert.equal(spawnCalls.length, 1);
});

test('scrapeHistory prefers the newest WhatsApp page after a fresh-window retry', async () => {
  const firstPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: true,
    searchMatch: true,
    searchText: 'Stale query',
    searchResetWorks: false,
  });
  const staleRetryPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: true,
    searchMatch: true,
    searchText: 'Older retry tab',
    searchResetWorks: false,
  });
  const freshRetryPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchMatch: true,
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello from newest page',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const browsers = [
    new FakeAttachedBrowser(new FakeContext([firstPage])),
    new FakeAttachedBrowser(new FakeContext([staleRetryPage, freshRetryPage])),
  ];
  const connector = {
    launchCalls: 0,
    connectCalls: [],
    async launchPersistentContext() {
      this.launchCalls += 1;
      throw new Error('launch mode not expected');
    },
    async connectOverCDP(endpointURL) {
      this.connectCalls.push(endpointURL);
      return browsers[Math.min(this.connectCalls.length - 1, browsers.length - 1)];
    },
  };
  const spawnCalls = [];

  const composer = new HistoryParser(
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

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.messages[0].content, 'Hello from newest page');
  assert.equal(spawnCalls.length, 1);
  assert.deepEqual(staleRetryPage.insertedTexts, []);
  assert.deepEqual(freshRetryPage.insertedTexts, ['Alice']);
});

test('scrapeHistory retries in a fresh CDP window when the attached page is not ready', async () => {
  const firstPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: false,
  });
  const secondPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchMatch: true,
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello after fresh window',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const browsers = [
    new FakeAttachedBrowser(new FakeContext([firstPage])),
    new FakeAttachedBrowser(new FakeContext([secondPage])),
  ];
  const connector = {
    launchCalls: 0,
    connectCalls: [],
    async launchPersistentContext() {
      this.launchCalls += 1;
      throw new Error('launch mode not expected');
    },
    async connectOverCDP(endpointURL) {
      this.connectCalls.push(endpointURL);
      return browsers[Math.min(this.connectCalls.length - 1, browsers.length - 1)];
    },
  };
  const spawnCalls = [];
  const originalDateNow = Date.now;
  const nowValues = [0, 200000, 200000, 200000];
  let currentNow = 0;
  Date.now = () => {
    if (nowValues.length > 0) {
      currentNow = nowValues.shift();
    }
    return currentNow;
  };

  const composer = new HistoryParser(
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

  try {
    const result = await composer.scrapeHistory(
      { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
    );

    assert.equal(result.status, 'history_scraped');
    assert.equal(result.messages[0].content, 'Hello after fresh window');
    assert.equal(spawnCalls.length, 1);
    assert.ok(connector.connectCalls.length >= 2);
    assert.ok(connector.connectCalls.every((value) => value === 'http://127.0.0.1:9222'));
  } finally {
    Date.now = originalDateNow;
  }
});

test('scrapeHistory returns login_required after a fresh-window retry when WhatsApp Web is still not ready', async () => {
  const firstPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: false,
    readyAfterWaits: Number.POSITIVE_INFINITY,
  });
  const secondPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: false,
    readyAfterWaits: Number.POSITIVE_INFINITY,
  });
  const browsers = [
    new FakeAttachedBrowser(new FakeContext([firstPage])),
    new FakeAttachedBrowser(new FakeContext([secondPage])),
  ];
  const connector = {
    launchCalls: 0,
    connectCalls: [],
    async launchPersistentContext() {
      this.launchCalls += 1;
      throw new Error('launch mode not expected');
    },
    async connectOverCDP(endpointURL) {
      this.connectCalls.push(endpointURL);
      return browsers[Math.min(this.connectCalls.length - 1, browsers.length - 1)];
    },
  };
  const spawnCalls = [];
  const originalDateNow = Date.now;
  const nowValues = [0, 200000, 200000, 200000, 200000, 400000];
  let currentNow = 0;
  Date.now = () => {
    if (nowValues.length > 0) {
      currentNow = nowValues.shift();
    }
    return currentNow;
  };

  const composer = new HistoryParser(
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

  try {
    const result = await composer.scrapeHistory(
      { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
    );

    assert.equal(result.status, 'login_required');
    assert.equal(spawnCalls.length, 1);
    assert.ok(connector.connectCalls.length >= 2);
  } finally {
    Date.now = originalDateNow;
  }
});
