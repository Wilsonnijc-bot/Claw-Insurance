import test from 'node:test';
import assert from 'node:assert/strict';

import { HistoryParser } from '../dist/history.js';

class FakeLocator {
  constructor(page, role, index = null) {
    this.page = page;
    this.role = role;
    this.index = index;
  }

  first() {
    return this.nth(0);
  }

  nth(index) {
    return new FakeLocator(this.page, this.role, index);
  }

  async waitFor() {
    if (this.role === 'search-result-row') {
      if (!this.page._locatorSearchRow(this.index ?? 0)) {
        throw new Error(`locator ${this.role} not visible`);
      }
      return;
    }
    if (!(await this.count())) {
      throw new Error(`locator ${this.role} not visible`);
    }
  }

  async click() {
    this.page.focus = this.role;
    if (this.role === 'search-result-row') {
      const row = this.page._locatorSearchRow(this.index ?? 0);
      if (!row) {
        throw new Error('no first search result row');
      }
      this.page._openSearchResultRow(row);
    }
  }

  async count() {
    switch (this.role) {
      case 'compose':
        return this.page.composeVisible ? 1 : 0;
      case 'search':
        return this.page.searchVisible ? 1 : 0;
      case 'search-result-row':
        if (this.index === null) {
          return this.page._allRealSearchRows().length;
        }
        return this.page._locatorSearchRow(this.index) ? 1 : 0;
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
    searchVisible = true,
    searchMatch = false,
    searchRows = null,
    searchRowPhases = null,
    searchRefreshAfterWaits = 0,
    searchText = '',
    searchResetWorks = true,
    searchClickOpensChat = true,
    composeText = '',
    historySnapshots = [],
    readyAfterWaits = 0,
    initialUrl = 'about:blank',
    openedChatHeaderText = '',
    pageHeaderText = '',
    composeLabel = '',
    composePlaceholder = '',
    pageTitle = '',
    openedChatHeaderFound = true,
  } = {}) {
    this.searchVisible = searchVisible;
    this.searchMatch = searchMatch;
    this.searchRows = Array.isArray(searchRows) ? this._normalizeSearchRows(searchRows) : null;
    this.searchRowPhases = Array.isArray(searchRowPhases)
      ? searchRowPhases.map((rows) => this._normalizeSearchRows(rows))
      : null;
    this.searchRowPhaseIndex = 0;
    this.searchRefreshAfterWaits = typeof searchRefreshAfterWaits === 'number' ? searchRefreshAfterWaits : null;
    this.searchRefreshStartWait = null;
    this.searchResultsMutationCount = 0;
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
    this.waitTimeouts = [];
    this.elapsedWaitMs = 0;
    this.currentUrl = initialUrl;
    this.openedChatHeaderText = openedChatHeaderText;
    this.openedChatHeaderFound = openedChatHeaderFound;
    this.pageHeaderText = pageHeaderText;
    this.composeLabel = composeLabel;
    this.composePlaceholder = composePlaceholder;
    this.pageTitle = pageTitle;
    this.clickedSearchRowIndex = null;
    this.clickedSearchRowWaitCall = null;
    this.clickedSearchRowElapsedSinceType = null;
    this.clickedSearchText = '';
    this.searchTypedElapsedWaitMs = null;
    this.keyboard = {
      insertText: async (text) => {
        this.insertedTexts.push(text);
        if (this.focus === 'compose') {
          this.composeText += text;
          this.composeVisible = true;
        } else {
          this.searchText += text;
          this.searchTypedElapsedWaitMs = this.elapsedWaitMs;
          this._onSearchQueryTyped();
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

  async waitForTimeout(timeoutMs = 0) {
    this.waitCalls += 1;
    this.waitTimeouts.push(timeoutMs);
    this.elapsedWaitMs += timeoutMs;
    if (this.waitCalls >= this.readyAfterWaits) {
      this.searchVisible = true;
    }
    this._maybeAdvanceSearchRows();
  }

  _normalizeSearchRows(rows = []) {
    return rows.map((row, index) => ({
      index,
      top: typeof row?.top === 'number' ? row.top : index,
      left: typeof row?.left === 'number' ? row.left : 0,
      role: typeof row?.role === 'string' ? row.role : 'gridcell',
      tabIndex: typeof row?.tabIndex === 'string' ? row.tabIndex : '0',
      width: typeof row?.width === 'number' ? row.width : 479,
      height: typeof row?.height === 'number' ? row.height : 72,
      opensChat: row?.opensChat !== false,
      headerText: typeof row?.headerText === 'string' ? row.headerText : '',
      headerFound: row?.headerFound !== false,
      visible: row?.visible !== false,
    }));
  }

  _currentSearchRows() {
    if (Array.isArray(this.searchRowPhases)) {
      return this.searchRowPhases[this.searchRowPhaseIndex] || [];
    }
    return Array.isArray(this.searchRows) ? this.searchRows : null;
  }

  _allRealSearchRows() {
    if (!this.searchVisible) {
      return [];
    }

    const rows = this._currentSearchRows();
    if (Array.isArray(rows)) {
      return rows.filter((row) => row.role === 'gridcell' && row.tabIndex === '0');
    }

    if (!this.searchMatch) {
      return [];
    }

    return [{
      index: 0,
      top: 0,
      left: 0,
      role: 'gridcell',
      tabIndex: '0',
      width: 479,
      height: 72,
      opensChat: this.searchClickOpensChat,
      headerText: this.openedChatHeaderText,
      headerFound: this.openedChatHeaderFound,
      visible: true,
    }];
  }

  _visibleSearchRows() {
    return this._allRealSearchRows()
      .filter((row) => row.visible !== false
        && row.width >= 80
        && row.height >= 24)
      .sort((left, right) => left.top - right.top || left.left - right.left);
  }

  _locatorSearchRow(index) {
    return this._allRealSearchRows()[index] || null;
  }

  _onSearchQueryTyped() {
    if (!this.searchVisible) {
      return;
    }

    if (Array.isArray(this.searchRowPhases)) {
      if (this.searchRowPhases.length > 1) {
        this.searchRefreshStartWait = this.waitCalls;
      }
      return;
    }

    this.searchResultsMutationCount += 1;
  }

  _maybeAdvanceSearchRows() {
    if (!Array.isArray(this.searchRowPhases) || this.searchRowPhases.length <= 1) {
      return;
    }
    if (this.searchRefreshStartWait === null) {
      return;
    }
    if (typeof this.searchRefreshAfterWaits !== 'number') {
      return;
    }
    if ((this.waitCalls - this.searchRefreshStartWait) < this.searchRefreshAfterWaits) {
      return;
    }

    this.searchRowPhaseIndex = Math.min(this.searchRowPhaseIndex + 1, this.searchRowPhases.length - 1);
    this.searchRefreshStartWait = null;
    this.searchResultsMutationCount += 1;
  }

  _openSearchResultRow(row) {
    this.focus = 'result';
    this.clickedSearchRowIndex = row.index;
    this.clickedSearchRowWaitCall = this.waitCalls;
    this.clickedSearchRowElapsedSinceType = this.searchTypedElapsedWaitMs === null
      ? null
      : this.elapsedWaitMs - this.searchTypedElapsedWaitMs;
    this.clickedSearchText = this.searchText;
    if (!row.opensChat) {
      return true;
    }
    this.composeVisible = true;
    this.currentUrl = 'https://web.whatsapp.com/';
    this.openedChatHeaderFound = row.headerFound;
    if (row.headerText) {
      this.openedChatHeaderText = row.headerText;
    } else if (!this.openedChatHeaderText) {
      this.openedChatHeaderText = this.searchText;
    }
    return true;
  }

  async evaluate(_fn, arg = {}) {
    if (arg.action === 'opened_chat_snapshot') {
      const headerText = this.openedChatHeaderText || this.searchText;
      return {
        url: this.currentUrl,
        title: this.pageTitle || this.pageHeaderText || this.searchText,
        headerFound: this.openedChatHeaderFound,
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
    if (arg.action === 'arm_search_results_refresh_observer') {
      return this.searchVisible ? this.searchResultsMutationCount : null;
    }
    if (arg.action === 'search_results_refresh_state') {
      const expectedValue = String(arg.expectedValue || '').trim();
      return {
        queryMatches: this.searchVisible && this.searchText.trim() === expectedValue,
        refreshed: this.searchResultsMutationCount > (arg.baseline || 0),
        hasRow: this._visibleSearchRows().length > 0,
      };
    }
    if (arg.action === 'first_search_result_row_index') {
      const [row] = this._visibleSearchRows();
      if (!row) {
        return null;
      }
      return this._allRealSearchRows().findIndex((candidate) => candidate.index === row.index);
    }
    if (arg.action === 'click_first_search_result_row') {
      const [row] = this._visibleSearchRows();
      if (!row) {
        return false;
      }
      return this._openSearchResultRow(row);
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
    if (selector.includes('role="gridcell"') && selector.includes('tabindex="0"')) {
      return new FakeLocator(this, 'search-result-row');
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

class FakeErrorSpawnedProcess {
  constructor(message) {
    this.exitCode = null;
    this.killed = false;
    this.listeners = new Map();
    queueMicrotask(() => {
      this.emit('error', new Error(message));
    });
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

test('scrapeHistory clicks the top refreshed search result and opens named contacts after typing the phone number', async () => {
  const page = new FakePage({
    searchRowPhases: [
      [
        { top: 0, headerText: 'Stale Existing Chat' },
        { top: 60, headerText: 'Stale Lower Chat' },
      ],
      [
        { top: 0, role: '', tabIndex: '-1', width: 479, height: 4564, headerText: 'Wrapper Container' },
        { top: 5, role: 'gridcell', tabIndex: '', width: 367, height: 24, headerText: 'Nested Fragment' },
        { top: 80, headerText: 'Lower Result' },
        { top: 10, headerText: 'Saved Contact' },
      ],
    ],
    searchRefreshAfterWaits: 2,
    pageHeaderText: '2',
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello from named contact',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Saved Contact:',
        },
      ],
    ],
  });
  const composer = new HistoryParser('/tmp/wa-web', new FakeBrowserConnector(page), 'launch');

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Unrelated Label'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.messages[0].content, 'Hello from named contact');
  assert.deepEqual(page.insertedTexts, ['1234567890']);
  assert.equal(page.searchText, '1234567890');
  assert.ok(page.waitTimeouts.includes(3000));
  assert.equal(page.clickedSearchText, '1234567890');
  assert.ok(page.clickedSearchRowElapsedSinceType >= 3000);
  assert.ok(page.clickedSearchRowWaitCall >= 2);
  assert.equal(page.clickedSearchRowIndex, 3);
  assert.equal(page.openedChatHeaderText, 'Saved Contact');
});

test('scrapeReplyTargets uses the same phone-first direct parse routine for each target', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchText: 'Stale direct parse query',
    searchMatch: true,
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello from direct parse',
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
    { chatId: '888@s.whatsapp.net', phone: '8888888888', searchTerms: ['Bob'] },
  ]);

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.results[0].status, 'history_scraped');
  assert.equal(result.results[1].status, 'history_scraped');
  assert.deepEqual(
    result.results.map((item) => item.target.phone),
    ['1234567890', '8888888888'],
  );
  assert.deepEqual(page.insertedTexts, ['1234567890', '8888888888']);
  assert.ok(!page.gotoUrls.some((url) => url.includes('/send?phone=')));
  assert.equal(page.searchText, '8888888888');
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

test('scrapeHistory asks the macOS helper to launch or adopt a host CDP browser before falling back to local spawn', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchMatch: true,
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello from helper-launched browser',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Alice:',
        },
      ],
    ],
  });
  const connector = new FakeBrowserConnector(page, { failConnectCalls: 1 });
  const spawnCalls = [];
  const helperCalls = [];
  const resolvedHosts = [];
  const composer = new HistoryParser(
    '/tmp/wa-web',
    connector,
    'cdp',
    'http://host.docker.internal:9222',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    (command, args, options) => {
      spawnCalls.push({ command, args, options });
      return new FakeSpawnedProcess();
    },
    'http://127.0.0.1:9230',
    '/host/wa-profile',
    {
      async ensureBrowser(request) {
        helperCalls.push(request);
        return {
          status: 'launched',
          detail: 'Chrome window opened on the host.',
          endpointUrl: request.endpointUrl,
        };
      },
    },
    async (hostname) => {
      resolvedHosts.push(hostname);
      return '192.168.5.2';
    },
  );

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'history_scraped');
  assert.equal(result.messages[0].content, 'Hello from helper-launched browser');
  assert.equal(spawnCalls.length, 0);
  assert.deepEqual(helperCalls, [
    {
      helperUrl: 'http://127.0.0.1:9230',
      endpointUrl: 'http://host.docker.internal:9222',
      profileDir: '/host/wa-profile',
      startUrl: 'https://web.whatsapp.com/',
      chromePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      forceNewWindow: false,
    },
  ]);
  assert.deepEqual(resolvedHosts, ['host.docker.internal']);
  assert.ok(connector.connectCalls.length >= 2);
  assert.ok(connector.connectCalls.every((value) => value.startsWith('http://192.168.5.2:9222')));
});

test('scrapeHistory waits for refreshed search results and does not click stale pre-search rows', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchText: 'Poisoned query',
    searchRowPhases: [
      [
        { top: 0, headerText: 'Stale Existing Chat' },
        { top: 50, headerText: 'Another Old Chat' },
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
    { chatId: 'missing@s.whatsapp.net', phone: '9999999999', searchTerms: ['Missing Client'] },
  );

  assert.equal(result.status, 'chat_not_found');
  assert.equal(spawnCalls.length, 0);
  assert.equal(page.searchText, '9999999999');
  assert.equal(page.clickedSearchRowIndex, null);
  assert.equal(page.openedChatHeaderText, '');
});

test('scrapeHistory does not reopen a CDP window for true chat_not_found results', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: true,
    searchRows: [
      { top: 0, role: '', tabIndex: '-1', width: 479, height: 4564, headerText: 'Wrapper Container' },
      { top: 8, role: 'gridcell', tabIndex: '', width: 367, height: 24, headerText: 'Nested Fragment' },
    ],
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
  assert.equal(page.searchText, '9999999999');
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

test('scrapeHistory retries in a fresh CDP window when the clicked chat does not become ready', async () => {
  const firstPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchRows: [{ top: 0, headerText: 'Saved Contact', headerFound: false }],
    openedChatHeaderFound: false,
  });
  const secondPage = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchRows: [{ top: 0, headerText: 'Saved Contact' }],
    historySnapshots: [
      [
        {
          id: 'false_msg-1',
          content: 'Hello after chat ready retry',
          fromMe: false,
          metaText: '[10:11, 9/3/2026] Saved Contact:',
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
  assert.equal(result.messages[0].content, 'Hello after chat ready retry');
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
  assert.deepEqual(freshRetryPage.insertedTexts, ['1234567890']);
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

test('scrapeHistory reports window_launch_failed when the macOS helper cannot launch or reuse host Chrome', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: false,
  });
  const connector = new FakeBrowserConnector(page, { failConnectCalls: Number.POSITIVE_INFINITY });
  const helperCalls = [];
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
    'http://127.0.0.1:9230',
    '/host/wa-profile',
    {
      async ensureBrowser(request) {
        helperCalls.push(request);
        throw new Error('Mac CDP helper is not installed/running at http://127.0.0.1:9230.');
      },
    },
  );

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'window_launch_failed');
  assert.match(result.detail, /Mac CDP helper is not installed\/running/);
  assert.equal(spawnCalls.length, 0);
  assert.equal(helperCalls.length, 2);
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
    assert.match(result.detail, /Chrome window opened/);
    assert.equal(spawnCalls.length, 1);
    assert.ok(connector.connectCalls.length >= 2);
  } finally {
    Date.now = originalDateNow;
  }
});

test('scrapeHistory maps local spawn ENOENT failures to window_launch_failed instead of login_required', async () => {
  const page = new FakePage({
    initialUrl: 'https://web.whatsapp.com/',
    searchVisible: false,
  });
  const connector = new FakeBrowserConnector(page, { failConnectCalls: Number.POSITIVE_INFINITY });
  const spawnCalls = [];
  const composer = new HistoryParser(
    '/tmp/wa-web',
    connector,
    'cdp',
    'http://127.0.0.1:9222',
    'google-chrome',
    (command, args, options) => {
      spawnCalls.push({ command, args, options });
      return new FakeErrorSpawnedProcess('spawn google-chrome ENOENT');
    },
  );

  const result = await composer.scrapeHistory(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
  );

  assert.equal(result.status, 'window_launch_failed');
  assert.match(result.detail, /No Chrome\/Chromium executable was found for CDP launch/);
  assert.equal(spawnCalls.length, 2);
});
