import { chromium } from 'playwright';

export interface ChatTarget {
  chatId: string;
  phone?: string;
  searchTerms: string[];
}

export interface DraftPrepareResult {
  status: 'draft_prepared' | 'chat_not_found' | 'compose_box_busy' | 'not_ready';
  detail?: string;
}

interface KeyboardDriver {
  insertText(text: string): Promise<unknown>;
  press(key: string): Promise<unknown>;
}

interface LocatorDriver {
  first(): LocatorDriver;
  waitFor(options?: Record<string, unknown>): Promise<unknown>;
  click(options?: Record<string, unknown>): Promise<unknown>;
  count(): Promise<number>;
  innerText(): Promise<string>;
}

interface PageDriver {
  goto(url: string, options?: Record<string, unknown>): Promise<unknown>;
  bringToFront?(): Promise<unknown>;
  waitForTimeout?(timeoutMs: number): Promise<unknown>;
  locator(selector: string): LocatorDriver;
  keyboard: KeyboardDriver;
}

interface BrowserContextDriver {
  pages(): PageDriver[];
  newPage(): Promise<PageDriver>;
  close(): Promise<void>;
  on?(event: 'close', listener: () => void): void;
}

interface BrowserLauncher {
  launchPersistentContext(
    userDataDir: string,
    options: Record<string, unknown>,
  ): Promise<BrowserContextDriver>;
}

const WHATSAPP_WEB_URL = 'https://web.whatsapp.com/';
const READY_TIMEOUT_MS = 15000;
const SEARCH_TIMEOUT_MS = 4000;

const SEARCH_BOX_SELECTORS = [
  'div[aria-label="Search input textbox"][contenteditable="true"]',
  'div[title="Search input textbox"][contenteditable="true"]',
  'div[contenteditable="true"][data-tab="3"]',
  'div[contenteditable="true"][role="textbox"]',
];

const COMPOSE_BOX_SELECTORS = [
  'footer div[aria-label="Type a message"][contenteditable="true"]',
  'footer div[contenteditable="true"][data-tab="10"]',
  'footer div[contenteditable="true"][role="textbox"]',
  'footer div[contenteditable="true"]',
];

export class DraftComposer {
  private context: BrowserContextDriver | null = null;
  private page: PageDriver | null = null;
  private queue: Promise<void> = Promise.resolve();

  constructor(
    private readonly userDataDir: string,
    private readonly browserLauncher: BrowserLauncher = chromium,
  ) {}

  async prepareDraft(target: ChatTarget, text: string): Promise<DraftPrepareResult> {
    return this._serialize(async () => this._prepareDraft(target, text));
  }

  async stop(): Promise<void> {
    if (this.context) {
      await this.context.close();
      this.context = null;
      this.page = null;
    }
  }

  private async _prepareDraft(target: ChatTarget, text: string): Promise<DraftPrepareResult> {
    const page = await this._ensurePage();
    await page.goto(WHATSAPP_WEB_URL, { waitUntil: 'domcontentloaded' });
    if (page.bringToFront) {
      await page.bringToFront();
    }

    const opened =
      (target.phone ? await this._openChatByPhone(page, target.phone) : false)
      || await this._searchAndOpenChat(page, target.searchTerms);

    if (!opened) {
      return {
        status: 'chat_not_found',
        detail: `Chat ${target.chatId} is not available in WhatsApp Web search.`,
      };
    }

    const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, READY_TIMEOUT_MS);
    if (!composeBox) {
      return {
        status: 'not_ready',
        detail: 'WhatsApp Web compose box is not ready. Log in to the browser session first.',
      };
    }

    const existing = (await composeBox.innerText()).trim();
    if (existing && existing !== text) {
      return {
        status: 'compose_box_busy',
        detail: 'Compose box already contains unsent text.',
      };
    }

    if (!existing) {
      await composeBox.click();
      await page.keyboard.insertText(text);
    }

    return { status: 'draft_prepared' };
  }

  private async _ensurePage(): Promise<PageDriver> {
    if (this.page) {
      return this.page;
    }

    if (!this.context) {
      try {
        this.context = await this.browserLauncher.launchPersistentContext(this.userDataDir, {
          headless: false,
          viewport: { width: 1440, height: 960 },
        });
      } catch (error) {
        const message = String(error);
        if (message.includes('Executable doesn\'t exist') || message.includes('browserType.launch')) {
          throw new Error(
            'Playwright browser is not installed. Run `npx playwright install chromium` in bridge/.',
          );
        }
        throw error;
      }
      this.context.on?.('close', () => {
        this.context = null;
        this.page = null;
      });
    }

    const existingPages = this.context.pages();
    this.page = existingPages[0] ?? await this.context.newPage();
    return this.page;
  }

  private async _openChatByPhone(page: PageDriver, phone: string): Promise<boolean> {
    await page.goto(
      `${WHATSAPP_WEB_URL}send?phone=${encodeURIComponent(phone)}&app_absent=0`,
      { waitUntil: 'domcontentloaded' },
    );
    const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, SEARCH_TIMEOUT_MS);
    return composeBox !== null;
  }

  private async _searchAndOpenChat(page: PageDriver, terms: string[]): Promise<boolean> {
    const searchBox = await this._findVisibleLocator(page, SEARCH_BOX_SELECTORS, READY_TIMEOUT_MS);
    if (!searchBox) {
      return false;
    }

    for (const rawTerm of terms) {
      const term = rawTerm.trim();
      if (!term) {
        continue;
      }

      await searchBox.click();
      await this._clearFocusedTextbox(page);
      await page.keyboard.insertText(term);
      await page.waitForTimeout?.(250);

      const result = await this._findVisibleLocator(page, this._chatResultSelectors(term), SEARCH_TIMEOUT_MS);
      if (!result) {
        continue;
      }

      await result.click();
      const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, SEARCH_TIMEOUT_MS);
      if (composeBox) {
        return true;
      }
    }

    return false;
  }

  private _chatResultSelectors(term: string): string[] {
    const escaped = cssEscape(term);
    return [
      `span[title="${escaped}"]`,
      `div[title="${escaped}"]`,
      `span[title*="${escaped}"]`,
      `div[title*="${escaped}"]`,
      `[aria-label*="${escaped}"]`,
      `[data-testid*="${escaped}"]`,
    ];
  }

  private async _findVisibleLocator(
    page: PageDriver,
    selectors: string[],
    timeoutMs: number,
  ): Promise<LocatorDriver | null> {
    for (const selector of selectors) {
      const locator = page.locator(selector).first();
      try {
        await locator.waitFor({ state: 'visible', timeout: timeoutMs });
        if ((await locator.count()) > 0) {
          return locator;
        }
      } catch {
        continue;
      }
    }
    return null;
  }

  private async _clearFocusedTextbox(page: PageDriver): Promise<void> {
    await this._tryPress(page.keyboard, 'Meta+A');
    await this._tryPress(page.keyboard, 'Control+A');
    await this._tryPress(page.keyboard, 'Backspace');
  }

  private async _tryPress(keyboard: KeyboardDriver, key: string): Promise<void> {
    try {
      await keyboard.press(key);
    } catch {
      // Ignore platform-specific keyboard shortcuts that are not available.
    }
  }

  private async _serialize<T>(operation: () => Promise<T>): Promise<T> {
    const pending = this.queue;
    let release: () => void = () => undefined;
    this.queue = new Promise<void>((resolve) => {
      release = resolve;
    });
    await pending.catch(() => undefined);
    try {
      return await operation();
    } finally {
      release();
    }
  }
}

function cssEscape(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}
