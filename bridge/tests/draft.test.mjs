import test from 'node:test';
import assert from 'node:assert/strict';

import { CDP_DRAFT_DISABLED_DETAIL, DraftComposer } from '../dist/draft.js';

class FakeLocator {
  constructor(page, role) {
    this.page = page;
    this.role = role;
  }

  first() {
    return this;
  }

  nth() {
    return this;
  }

  async waitFor() {
    if (!(await this.count())) {
      throw new Error(`locator ${this.role} not visible`);
    }
  }

  async click() {
    this.page.focus = this.role;
    if (this.role === 'compose') {
      this.page.composeVisible = true;
    }
  }

  async count() {
    switch (this.role) {
      case 'compose':
        return this.page.composeVisible ? 1 : 0;
      case 'search':
        return this.page.searchVisible ? 1 : 0;
      default:
        return 0;
    }
  }

  async innerText() {
    return this.role === 'compose' ? this.page.composeText : '';
  }
}

class FakePage {
  constructor({
    openByPhoneSuccess = true,
    searchVisible = true,
    searchMatch = false,
    searchText = '',
    composeText = '',
    openedChatHeaderText = '',
    openedChatHeaderFound = true,
    initialUrl = 'about:blank',
  } = {}) {
    this.openByPhoneSuccess = openByPhoneSuccess;
    this.searchVisible = searchVisible;
    this.searchMatch = searchMatch;
    this.searchText = searchText;
    this.composeText = composeText;
    this.composeVisible = composeText.length > 0;
    this.openedChatHeaderText = openedChatHeaderText;
    this.openedChatHeaderFound = openedChatHeaderFound;
    this.currentUrl = initialUrl;
    this.focus = 'search';
    this.gotoUrls = [];
    this.insertedTexts = [];
    this.pressedKeys = [];
    this.keyboard = {
      insertText: async (text) => {
        this.insertedTexts.push(text);
        if (this.focus === 'compose') {
          this.composeText += text;
          this.composeVisible = true;
          return;
        }
        this.searchText += text;
      },
      press: async (key) => {
        this.pressedKeys.push(key);
      },
    };
  }

  async goto(url) {
    this.currentUrl = url;
    this.gotoUrls.push(url);
    if (url.includes('/send?phone=')) {
      this.composeVisible = this.openByPhoneSuccess;
    }
  }

  async bringToFront() {}

  url() {
    return this.currentUrl;
  }

  async waitForTimeout() {}

  async evaluate(_fn, arg = {}) {
    if (arg.action === 'opened_chat_snapshot') {
      const headerText = this.openedChatHeaderText || this.searchText;
      return {
        url: this.currentUrl,
        title: headerText,
        headerFound: this.openedChatHeaderFound,
        headerText,
        composeLabel: headerText,
        composePlaceholder: '',
      };
    }
    if (arg.action === 'search_box_state') {
      return {
        found: this.searchVisible,
        value: this.searchVisible ? this.searchText.trim() : '',
      };
    }
    if (arg.action === 'focus_exact_search_box') {
      if (!this.searchVisible) {
        return false;
      }
      this.focus = 'search';
      return true;
    }
    if (arg.action === 'reset_search_box') {
      if (!this.searchVisible) {
        return false;
      }
      this.focus = 'search';
      this.searchText = '';
      return true;
    }
    if (arg.action === 'search_result_row_visible') {
      return this.searchMatch;
    }
    if (arg.action === 'click_search_result_row') {
      if (!this.searchMatch) {
        return false;
      }
      this.composeVisible = true;
      this.currentUrl = 'https://web.whatsapp.com/';
      if (!this.openedChatHeaderText) {
        this.openedChatHeaderText = this.searchText;
      }
      return true;
    }
    return null;
  }

  locator(selector) {
    if (selector.includes('footer')) {
      return new FakeLocator(this, 'compose');
    }
    if (
      selector.includes('Search input textbox')
      || selector.includes('data-tab="3"')
      || selector.includes('role="textbox"')
    ) {
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
  constructor(page) {
    this.context = new FakeContext([page]);
    this.browser = new FakeAttachedBrowser(this.context);
    this.launchCalls = 0;
    this.connectCalls = [];
  }

  async launchPersistentContext() {
    this.launchCalls += 1;
    return this.context;
  }

  async connectOverCDP(endpointURL) {
    this.connectCalls.push(endpointURL);
    return this.browser;
  }
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
  assert.equal(result.detail, CDP_DRAFT_DISABLED_DETAIL);
  assert.deepEqual(connector.connectCalls, []);
  assert.equal(connector.launchCalls, 0);
  assert.ok(!page.gotoUrls.includes('https://web.whatsapp.com/'));
});
