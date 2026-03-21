import { spawn as spawnProcess, type ChildProcess, type SpawnOptions } from 'child_process';
import { existsSync } from 'fs';
import { homedir } from 'os';
import { join } from 'path';
import { chromium } from 'playwright';

export interface ChatTarget {
  chatId: string;
  phone?: string;
  searchTerms: string[];
}

type BrowserMode = 'cdp' | 'launch';

export interface DraftPrepareResult {
  status: 'draft_prepared' | 'chat_not_found' | 'compose_box_busy' | 'not_ready';
  detail?: string;
}

export interface ScrapedHistoryMessage {
  id: string;
  content: string;
  timestamp: string;
  fromMe: boolean;
  pushName?: string;
}

export interface HistoryScrapeResult {
  status: 'history_scraped' | 'chat_not_found' | 'not_ready';
  detail?: string;
  messages?: ScrapedHistoryMessage[];
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
  url?(): string;
  locator(selector: string): LocatorDriver;
  evaluate<R, Arg = unknown>(
    pageFunction: (arg: Arg) => R | Promise<R>,
    arg?: Arg,
  ): Promise<R>;
  keyboard: KeyboardDriver;
}

interface BrowserContextDriver {
  pages(): PageDriver[];
  newPage(): Promise<PageDriver>;
  close(): Promise<void>;
  on?(event: 'close', listener: () => void): void;
}

interface BrowserDriver {
  contexts(): BrowserContextDriver[];
  close(options?: Record<string, unknown>): Promise<void>;
  on?(event: 'disconnected', listener: () => void): void;
}

interface BrowserConnector {
  launchPersistentContext(
    userDataDir: string,
    options: Record<string, unknown>,
  ): Promise<BrowserContextDriver>;
  connectOverCDP(
    endpointURL: string,
    options?: Record<string, unknown>,
  ): Promise<BrowserDriver>;
}

type SpawnedBrowserProcess = Pick<ChildProcess, 'once' | 'unref'> & {
  exitCode: number | null;
  killed: boolean;
};

type BrowserLauncher = (
  command: string,
  args: string[],
  options: SpawnOptions,
) => SpawnedBrowserProcess;

const WHATSAPP_WEB_URL = 'https://web.whatsapp.com/';
const READY_TIMEOUT_MS = 15000;
const LOGIN_WAIT_TIMEOUT_MS = 180000;
const READY_CHECK_TIMEOUT_MS = 250;
const SEARCH_TIMEOUT_MS = 4000;
const HISTORY_SCROLL_WAIT_MS = 400;
const HISTORY_SCROLL_LIMIT = 60;
const READY_POLL_WAIT_MS = 1000;
const CDP_CONNECT_TIMEOUT_MS = 15000;
const CDP_CONNECT_RETRY_WAIT_MS = 500;

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
  private browser: BrowserDriver | null = null;
  private context: BrowserContextDriver | null = null;
  private page: PageDriver | null = null;
  private queue: Promise<void> = Promise.resolve();
  private launchedCdpProcess: SpawnedBrowserProcess | null = null;

  constructor(
    private readonly userDataDir: string,
    private readonly browserConnector: BrowserConnector = chromium,
    private readonly browserMode: BrowserMode = 'cdp',
    private readonly cdpEndpoint: string = 'http://127.0.0.1:9222',
    private readonly cdpChromePath: string = process.env.WEB_CDP_CHROME_PATH || '',
    private readonly browserLauncher: BrowserLauncher = spawnProcess as BrowserLauncher,
  ) {}

  async prepareDraft(target: ChatTarget, text: string): Promise<DraftPrepareResult> {
    return this._serialize(async () => this._prepareDraft(target, text));
  }

  async scrapeHistory(target: ChatTarget): Promise<HistoryScrapeResult> {
    return this._serialize(async () => this._scrapeHistory(target));
  }

  async stop(): Promise<void> {
    if (this.browserMode === 'launch') {
      if (this.browser) {
        await this.browser.close();
      } else if (this.context) {
        await this.context.close();
      }
    } else if (this.context && !this.browser) {
      await this.context.close();
    }
    this.browser = null;
    this.context = null;
    this.page = null;
  }

  private async _prepareDraft(target: ChatTarget, text: string): Promise<DraftPrepareResult> {
    const page = await this._ensurePage();
    if (page.bringToFront) {
      await page.bringToFront();
    }
    if (!await this._ensureWhatsAppReady(page)) {
      return {
        status: 'not_ready',
        detail: 'WhatsApp Web is not ready. Log in to the browser session first.',
      };
    }

    const opened = await this._openTarget(page, target);

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

  private async _scrapeHistory(target: ChatTarget): Promise<HistoryScrapeResult> {
    const page = await this._ensurePage();
    if (page.bringToFront) {
      await page.bringToFront();
    }
    if (!await this._ensureWhatsAppReady(page)) {
      return {
        status: 'not_ready',
        detail: 'WhatsApp Web is not ready. Log in to the browser session first.',
      };
    }

    const opened = await this._openTarget(page, target);
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
        detail: 'WhatsApp Web chat is not ready. Log in to the browser session first.',
      };
    }

    const collected = new Map<string, RawScrapedHistoryMessage>();
    let stagnantRounds = 0;

    for (let attempt = 0; attempt < HISTORY_SCROLL_LIMIT; attempt += 1) {
      const snapshot = await this._extractHistorySnapshot(page);
      let newCount = 0;
      for (const item of snapshot.messages) {
        if (!item.id || collected.has(item.id)) {
          continue;
        }
        collected.set(item.id, item);
        newCount += 1;
      }

      const scrolled = await this._scrollHistoryUp(page);
      if (!scrolled) {
        stagnantRounds += 1;
      } else {
        stagnantRounds = 0;
      }

      if ((snapshot.atTop && newCount === 0) || stagnantRounds >= 2) {
        break;
      }

      await page.waitForTimeout?.(HISTORY_SCROLL_WAIT_MS);
    }

    const messages = this._normalizeScrapedMessages([...collected.values()]);
    return {
      status: 'history_scraped',
      messages,
    };
  }

  private async _ensurePage(): Promise<PageDriver> {
    if (this.page) {
      return this.page;
    }

    const context = await this._ensureContext();
    this.page = await this._ensureWhatsAppPage(context);
    return this.page;
  }

  private async _ensureContext(): Promise<BrowserContextDriver> {
    if (this.context) {
      return this.context;
    }

    if (this.browserMode === 'cdp') {
      this.browser = await this._connectOrLaunchCdpBrowser();
      this.browser.on?.('disconnected', () => {
        this.browser = null;
        this.context = null;
        this.page = null;
      });
      this.context = this._pickAttachedContext(this.browser);
      if (!this.context) {
        throw new Error(
          'CDP browser is connected, but no reusable browser context is available. Keep the manual Chrome window open.',
        );
      }
      return this.context;
    }

    try {
      this.context = await this.browserConnector.launchPersistentContext(this.userDataDir, {
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
    return this.context;
  }

  private async _connectOrLaunchCdpBrowser(): Promise<BrowserDriver> {
    const attached = await this._tryConnectOverCDP();
    if (attached) {
      return attached;
    }

    await this._launchCdpBrowser();
    const launched = await this._waitForCDPBrowser();
    if (launched) {
      return launched;
    }

    throw new Error(
      `CDP browser is not available at ${this.cdpEndpoint}. Start Chrome with --remote-debugging-port or set webCdpChromePath so nanobot can launch it.`,
    );
  }

  private async _tryConnectOverCDP(): Promise<BrowserDriver | null> {
    try {
      return await this.browserConnector.connectOverCDP(this.cdpEndpoint);
    } catch {
      return null;
    }
  }

  private async _waitForCDPBrowser(): Promise<BrowserDriver | null> {
    const deadline = Date.now() + CDP_CONNECT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const browser = await this._tryConnectOverCDP();
      if (browser) {
        return browser;
      }
      await sleep(CDP_CONNECT_RETRY_WAIT_MS);
    }
    return null;
  }

  private async _launchCdpBrowser(): Promise<void> {
    if (this.launchedCdpProcess && this.launchedCdpProcess.exitCode === null && !this.launchedCdpProcess.killed) {
      return;
    }

    const executable = this._resolveChromeExecutablePath();
    const { hostname, port } = this._parseCdpEndpoint();
    const profileDir = this._expandHome(this.userDataDir);
    const args = [
      `--remote-debugging-port=${port}`,
      ...(hostname ? [`--remote-debugging-address=${hostname}`] : []),
      `--user-data-dir=${profileDir}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--new-window',
      WHATSAPP_WEB_URL,
    ];

    const child = this.browserLauncher(executable, args, {
      detached: true,
      stdio: 'ignore',
      env: process.env,
    });
    this.launchedCdpProcess = child;
    child.once?.('exit', () => {
      if (this.launchedCdpProcess === child) {
        this.launchedCdpProcess = null;
      }
    });

    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const done = (error?: unknown) => {
        if (settled) {
          return;
        }
        settled = true;
        if (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
          return;
        }
        resolve();
      };

      child.once?.('error', done);
      child.once?.('spawn', () => done());
      setTimeout(() => done(), 250);
    });

    child.unref();
  }

  private _parseCdpEndpoint(): { hostname: string; port: string } {
    try {
      const parsed = new URL(this.cdpEndpoint);
      return {
        hostname: parsed.hostname || '127.0.0.1',
        port: parsed.port || '9222',
      };
    } catch {
      return { hostname: '127.0.0.1', port: '9222' };
    }
  }

  private _resolveChromeExecutablePath(): string {
    const configured = this._expandHome(this.cdpChromePath.trim());
    if (configured) {
      if (this._looksLikePath(configured) && !existsSync(configured)) {
        throw new Error(`Configured Chrome executable does not exist: ${configured}`);
      }
      return configured;
    }

    for (const candidate of this._candidateChromePaths()) {
      const expanded = this._expandHome(candidate);
      if (!this._looksLikePath(expanded) || existsSync(expanded)) {
        return expanded;
      }
    }

    throw new Error(
      'No Chrome/Chromium executable was found for CDP launch. Set webCdpChromePath in the WhatsApp config.',
    );
  }

  private _candidateChromePaths(): string[] {
    const home = homedir();
    const windowsProgramFiles = process.env.ProgramFiles || 'C:\\Program Files';
    const windowsProgramFilesX86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)';

    return [
      process.env.CHROME_PATH || '',
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
      '/Applications/Chromium.app/Contents/MacOS/Chromium',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
      join(home, 'Applications', 'Google Chrome.app', 'Contents', 'MacOS', 'Google Chrome'),
      join(home, 'Applications', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'),
      join(windowsProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      join(windowsProgramFilesX86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      join(windowsProgramFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      'google-chrome',
      'google-chrome-stable',
      'chromium',
      'chromium-browser',
      'microsoft-edge',
      'msedge',
    ].filter(Boolean);
  }

  private _expandHome(pathValue: string): string {
    if (!pathValue.startsWith('~/')) {
      return pathValue;
    }
    return join(homedir(), pathValue.slice(2));
  }

  private _looksLikePath(value: string): boolean {
    return value.includes('/') || value.includes('\\');
  }

  private _pickAttachedContext(browser: BrowserDriver): BrowserContextDriver | null {
    const contexts = browser.contexts();
    if (contexts.length === 0) {
      return null;
    }

    return contexts.find((context) => context.pages().some((page) => this._isWhatsAppUrl(this._pageUrl(page)))) || contexts[0];
  }

  private async _ensureWhatsAppPage(context: BrowserContextDriver): Promise<PageDriver> {
    const pages = context.pages();
    const existingWhatsAppPage = pages.find((page) => this._isWhatsAppUrl(this._pageUrl(page)));

    let page: PageDriver;
    if (existingWhatsAppPage) {
      page = existingWhatsAppPage;
    } else if (this.browserMode === 'cdp') {
      page = await context.newPage();
    } else {
      page = pages[0] ?? await context.newPage();
    }

    if (!this._isWhatsAppUrl(this._pageUrl(page))) {
      await page.goto(WHATSAPP_WEB_URL, { waitUntil: 'domcontentloaded' });
    }
    return page;
  }

  private _pageUrl(page: PageDriver): string {
    try {
      return typeof page.url === 'function' ? String(page.url() || '') : '';
    } catch {
      return '';
    }
  }

  private _isWhatsAppUrl(url: string): boolean {
    return String(url || '').startsWith(WHATSAPP_WEB_URL);
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

  private async _openTarget(page: PageDriver, target: ChatTarget): Promise<boolean> {
    return (
      (target.phone ? await this._openChatByPhone(page, target.phone) : false)
      || await this._searchAndOpenChat(page, target.searchTerms)
    );
  }

  private async _ensureWhatsAppReady(page: PageDriver): Promise<boolean> {
    const selectors = [...SEARCH_BOX_SELECTORS, ...COMPOSE_BOX_SELECTORS];
    if (await this._findVisibleLocator(page, selectors, READY_CHECK_TIMEOUT_MS)) {
      return true;
    }

    const deadline = Date.now() + LOGIN_WAIT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (await this._findVisibleLocator(page, selectors, READY_CHECK_TIMEOUT_MS)) {
        return true;
      }
      await page.waitForTimeout?.(READY_POLL_WAIT_MS);
    }

    return false;
  }

  private async _extractHistorySnapshot(page: PageDriver): Promise<HistorySnapshot> {
    return page.evaluate(({ action }) => {
      if (action !== 'extract_history') {
        return { messages: [], atTop: true };
      }

      const root = document.querySelector('main') || document.body;
      const nodes = Array.from(root.querySelectorAll('[data-id]'));
      const messages = [];

      const isTimeOnly = (value: unknown) => /^\d{1,2}:\d{2}(?:\s*[AP]M)?$/i.test(String(value || '').trim());

      for (const node of nodes) {
        const element = node;
        const id = String(element.getAttribute('data-id') || '').trim();
        if (!id) {
          continue;
        }

        const bubble = element.querySelector('[data-pre-plain-text]') as HTMLElement | null;
        const metaText = String(
          (bubble && bubble.getAttribute('data-pre-plain-text'))
          || element.getAttribute('data-pre-plain-text')
          || '',
        ).trim();
        const rawText = String(
          (bubble && bubble.innerText)
          || (element as HTMLElement).innerText
          || '',
        ).trim();
        const lines = rawText
          .split(/\n+/)
          .map((line) => String(line || '').trim())
          .filter(Boolean);
        while (lines.length > 1 && isTimeOnly(lines[lines.length - 1])) {
          lines.pop();
        }

        let content = lines.join('\n').trim();
        if (!content) {
          if (element.querySelector('img')) {
            content = '[Image]';
          } else if (element.querySelector('video')) {
            content = '[Video]';
          } else if (element.querySelector('audio')) {
            content = '[Audio]';
          } else if (element.querySelector('[data-icon="ptt"], [data-icon="audio-download"], a[download]')) {
            content = '[Document]';
          } else {
            continue;
          }
        }

        messages.push({
          id,
          content,
          fromMe: id.startsWith('true_'),
          metaText,
        });
      }

      const candidates = Array.from(root.querySelectorAll('*'))
        .filter((node) => node.scrollHeight > node.clientHeight + 200);
      const scroller = candidates
        .sort((left, right) => right.scrollHeight - left.scrollHeight)[0] || null;

      return {
        messages,
        atTop: scroller ? scroller.scrollTop <= 0 : true,
      };
    }, { action: 'extract_history' });
  }

  private async _scrollHistoryUp(page: PageDriver): Promise<boolean> {
    return page.evaluate(({ action }) => {
      if (action !== 'scroll_history_up') {
        return false;
      }

      const root = document.querySelector('main') || document.body;
      const candidates = Array.from(root.querySelectorAll('*'))
        .filter((node) => node.scrollHeight > node.clientHeight + 200);
      const scroller = candidates
        .sort((left, right) => right.scrollHeight - left.scrollHeight)[0] || null;
      if (!scroller) {
        return false;
      }

      const before = scroller.scrollTop;
      scroller.scrollTop = 0;
      return before > 0;
    }, { action: 'scroll_history_up' });
  }

  private _normalizeScrapedMessages(messages: RawScrapedHistoryMessage[]): ScrapedHistoryMessage[] {
    return messages
      .map((item, index) => {
        const parsed = this._parseMetaText(item.metaText, index);
        return {
          id: item.id,
          content: item.content,
          timestamp: parsed.timestamp,
          fromMe: item.fromMe,
          ...(parsed.pushName ? { pushName: parsed.pushName } : {}),
        };
      })
      .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  }

  private _parseMetaText(metaText: string, fallbackIndex: number): { timestamp: string; pushName?: string } {
    const pushName = this._extractPushName(metaText);
    const bracket = (metaText.match(/\[([^\]]+)\]/) || [])[1] || metaText || '';
    const stamp = String(bracket || '').trim();

    for (const pattern of [
      /^(?<time>\d{1,2}:\d{2}(?:\s*[AP]M)?)\s*,\s*(?<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4})$/i,
      /^(?<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4})\s*,\s*(?<time>\d{1,2}:\d{2}(?:\s*[AP]M)?)$/i,
    ]) {
      const match = stamp.match(pattern);
      if (!match?.groups) {
        continue;
      }
      const timestamp = this._parseDateTime(match.groups.date, match.groups.time, fallbackIndex);
      if (timestamp) {
        return pushName ? { timestamp, pushName } : { timestamp };
      }
    }

    const direct = Date.parse(stamp);
    if (!Number.isNaN(direct)) {
      const timestamp = new Date(direct + fallbackIndex).toISOString();
      return pushName ? { timestamp, pushName } : { timestamp };
    }

    const timestamp = new Date(Date.now() + fallbackIndex).toISOString();
    return pushName ? { timestamp, pushName } : { timestamp };
  }

  private _extractPushName(metaText: string): string {
    const match = metaText.match(/\]\s*([^:\]]+):\s*$/);
    return match ? match[1].trim() : '';
  }

  private _parseDateTime(dateText: string, timeText: string, fallbackIndex: number): string | null {
    const dateParts = String(dateText || '')
      .split(/[./-]/)
      .map((item) => parseInt(item, 10))
      .filter((item) => Number.isFinite(item));
    if (dateParts.length !== 3) {
      return null;
    }

    let year = 0;
    let month = 0;
    let day = 0;
    const [first, second, third] = dateParts;

    if (String(dateText).split(/[./-]/)[0].length === 4) {
      year = first;
      month = second;
      day = third;
    } else {
      year = third < 100 ? 2000 + third : third;
      if (first > 12 && second <= 12) {
        day = first;
        month = second;
      } else if (second > 12 && first <= 12) {
        month = first;
        day = second;
      } else {
        day = first;
        month = second;
      }
    }

    const timeMatch = String(timeText || '').trim().match(/^(?<hour>\d{1,2}):(?<minute>\d{2})(?:\s*(?<ampm>[AP]M))?$/i);
    if (!timeMatch?.groups) {
      return null;
    }

    let hour = parseInt(timeMatch.groups.hour, 10);
    const minute = parseInt(timeMatch.groups.minute, 10);
    const ampm = String(timeMatch.groups.ampm || '').toUpperCase();
    if (ampm === 'PM' && hour < 12) {
      hour += 12;
    } else if (ampm === 'AM' && hour === 12) {
      hour = 0;
    }

    const parsed = new Date(year, month - 1, day, hour, minute, 0, fallbackIndex);
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }
    return parsed.toISOString();
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

interface RawScrapedHistoryMessage {
  id: string;
  content: string;
  fromMe: boolean;
  metaText: string;
}

interface HistorySnapshot {
  messages: RawScrapedHistoryMessage[];
  atTop: boolean;
}

function cssEscape(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function sleep(timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, timeoutMs);
  });
}
