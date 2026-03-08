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
  constructor({ openByPhoneSuccess = true, searchVisible = true, searchMatch = false, composeText = '' } = {}) {
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
    this.gotoUrls.push(url);
    if (url.includes('/send?phone=')) {
      this.composeVisible = this.openByPhoneSuccess;
    }
    if (url === 'https://web.whatsapp.com/') {
      this.searchVisible = true;
      if (!this.composeText) {
        this.composeVisible = false;
      }
    }
  }

  async bringToFront() {}

  async waitForTimeout() {}

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
  constructor(page) {
    this.page = page;
  }

  pages() {
    return [this.page];
  }

  async newPage() {
    return this.page;
  }

  async close() {}

  on() {}
}

class FakeBrowserLauncher {
  constructor(page) {
    this.page = page;
  }

  async launchPersistentContext() {
    return new FakeContext(this.page);
  }
}

test('prepareDraft fills compose box without sending when phone lookup succeeds', async () => {
  const page = new FakePage({ openByPhoneSuccess: true });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserLauncher(page));

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
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserLauncher(page));

  const result = await composer.prepareDraft(
    { chatId: 'alice@s.whatsapp.net', searchTerms: ['Alice'] },
    'Draft reply',
  );

  assert.equal(result.status, 'draft_prepared');
  assert.deepEqual(page.insertedTexts, ['Alice', 'Draft reply']);
});

test('prepareDraft reports busy compose box instead of overwriting text', async () => {
  const page = new FakePage({ openByPhoneSuccess: true, composeText: 'Existing unsent draft' });
  const composer = new DraftComposer('/tmp/wa-web', new FakeBrowserLauncher(page));

  const result = await composer.prepareDraft(
    { chatId: '123@s.whatsapp.net', phone: '1234567890', searchTerms: ['Alice'] },
    'Fresh reply',
  );

  assert.equal(result.status, 'compose_box_busy');
  assert.equal(page.composeText, 'Existing unsent draft');
});
