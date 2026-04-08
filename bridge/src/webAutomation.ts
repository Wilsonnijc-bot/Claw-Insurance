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

export type BrowserMode = 'cdp' | 'launch';

export interface KeyboardDriver {
  insertText(text: string): Promise<unknown>;
  press(key: string): Promise<unknown>;
}

export interface LocatorDriver {
  first(): LocatorDriver;
  waitFor(options?: Record<string, unknown>): Promise<unknown>;
  click(options?: Record<string, unknown>): Promise<unknown>;
  count(): Promise<number>;
  innerText(): Promise<string>;
}

export interface PageDriver {
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

export interface BrowserContextDriver {
  pages(): PageDriver[];
  newPage(): Promise<PageDriver>;
  close(): Promise<void>;
  on?(event: 'close', listener: () => void): void;
}

export interface BrowserDriver {
  contexts(): BrowserContextDriver[];
  close(options?: Record<string, unknown>): Promise<void>;
  on?(event: 'disconnected', listener: () => void): void;
}

export interface BrowserConnector {
  launchPersistentContext(
    userDataDir: string,
    options: Record<string, unknown>,
  ): Promise<BrowserContextDriver>;
  connectOverCDP(
    endpointURL: string,
    options?: Record<string, unknown>,
  ): Promise<BrowserDriver>;
}

export type SpawnedBrowserProcess = Pick<ChildProcess, 'once' | 'unref'> & {
  exitCode: number | null;
  killed: boolean;
};

export type BrowserLauncher = (
  command: string,
  args: string[],
  options: SpawnOptions,
) => SpawnedBrowserProcess;

export const DEFAULT_BROWSER_CONNECTOR: BrowserConnector = chromium;
export const DEFAULT_BROWSER_LAUNCHER: BrowserLauncher = spawnProcess as BrowserLauncher;

export const WHATSAPP_WEB_URL = 'https://web.whatsapp.com/';
export const READY_TIMEOUT_MS = 15000;
export const LOGIN_WAIT_TIMEOUT_MS = 180000;
export const READY_CHECK_TIMEOUT_MS = 250;
export const SEARCH_TIMEOUT_MS = 4000;
export const HISTORY_SCROLL_WAIT_MS = 400;
export const HISTORY_SCROLL_LIMIT = 60;
export const READY_POLL_WAIT_MS = 1000;
export const CDP_CONNECT_TIMEOUT_MS = 15000;
export const CDP_CONNECT_RETRY_WAIT_MS = 500;
export const SIDEBAR_SCROLL_WAIT_MS = 250;
export const SIDEBAR_SCROLL_LIMIT = 80;

export const SEARCH_BOX_SELECTORS = [
  'input[aria-label="搜索或开始新聊天"]',
  'input[placeholder="搜索或开始新聊天"]',
  'input[aria-label="Search or start new chat"]',
  'input[placeholder="Search or start new chat"]',
  'input[role="textbox"][data-tab="3"]',
  'input[role="textbox"]',
  'div[aria-label="Search input textbox"][contenteditable="true"]',
  'div[title="Search input textbox"][contenteditable="true"]',
  'div[contenteditable="true"][data-tab="3"]',
  'div[contenteditable="true"][role="textbox"]',
] as const;

export const COMPOSE_BOX_SELECTORS = [
  'footer [data-testid="conversation-compose-box-input"]',
  'footer div[aria-label="输入消息"][contenteditable="true"]',
  'footer div[aria-placeholder="输入消息"][contenteditable="true"]',
  'footer div[aria-label="Type a message"][contenteditable="true"]',
  'footer div[aria-label="Type a message"][contenteditable="true"]',
  'footer div[contenteditable="true"][data-tab="10"]',
  'footer div[contenteditable="true"][role="textbox"]',
  'footer div[contenteditable="true"]',
] as const;

export class ParseSessionUnavailableError extends Error {}

interface OpenedTargetSnapshot {
  url: string;
  title: string;
  headerText: string;
  composeLabel: string;
  composePlaceholder: string;
}

interface SearchBoxState {
  found: boolean;
  value: string;
}

type SearchOpenState = 'search_reset' | 'search_typed' | 'result_found' | 'chat_opened';

interface OpenTargetResult {
  status: 'opened' | 'chat_not_found' | 'session_unusable';
  state?: SearchOpenState;
  matchedTerm?: string;
}

export class WhatsAppWebSession {
  protected browser: BrowserDriver | null = null;
  protected context: BrowserContextDriver | null = null;
  protected page: PageDriver | null = null;
  protected queue: Promise<void> = Promise.resolve();
  protected launchedCdpProcess: SpawnedBrowserProcess | null = null;
  protected preferNewestAttachedPage: boolean = false;

  constructor(
    protected readonly userDataDir: string,
    protected readonly browserConnector: BrowserConnector = DEFAULT_BROWSER_CONNECTOR,
    protected readonly browserMode: BrowserMode = 'cdp',
    protected readonly cdpEndpoint: string = 'http://127.0.0.1:9222',
    protected readonly cdpChromePath: string = process.env.WEB_CDP_CHROME_PATH || '',
    protected readonly browserLauncher: BrowserLauncher = DEFAULT_BROWSER_LAUNCHER,
  ) {}

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

  protected async _ensurePage(): Promise<PageDriver> {
    if (this.page) {
      if (await this._isPageResponsive(this.page)) {
        return this.page;
      }
      this.page = null;
    }

    if (this.context && !this._isContextResponsive(this.context)) {
      this.context = null;
    }

    if (this.browserMode === 'cdp' && this.browser && !this._isBrowserResponsive(this.browser)) {
      this.browser = null;
      this.context = null;
      this.page = null;
      this.preferNewestAttachedPage = false;
    }

    if (this.page) {
      return this.page;
    }

    const context = await this._ensureContext();
    if (this.page) {
      return this.page;
    }
    this.page = await this._ensureWhatsAppPage(context);
    return this.page;
  }

  protected async _ensureContext(): Promise<BrowserContextDriver> {
    if (this.context) {
      return this.context;
    }

    if (this.browserMode === 'cdp') {
      this.browser = await this._connectOrLaunchCdpBrowser();
      this.browser.on?.('disconnected', () => {
        this.browser = null;
        this.context = null;
        this.page = null;
        this.preferNewestAttachedPage = false;
      });
      const attached = await this._findAttachedWhatsAppPage(this.browser, false, this.preferNewestAttachedPage);
      if (attached) {
        this.context = attached.context;
        this.page = attached.page;
        this.preferNewestAttachedPage = false;
      } else {
        this.context = this._pickAttachedContext(this.browser, this.preferNewestAttachedPage);
        this.preferNewestAttachedPage = false;
      }
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

  protected async _connectOrLaunchCdpBrowser(): Promise<BrowserDriver> {
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

  protected async _tryConnectOverCDP(): Promise<BrowserDriver | null> {
    try {
      return await this.browserConnector.connectOverCDP(this.cdpEndpoint);
    } catch {
      return null;
    }
  }

  protected async _waitForCDPBrowser(): Promise<BrowserDriver | null> {
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

  protected async _launchCdpBrowser(forceNewWindow: boolean = false): Promise<void> {
    if (!forceNewWindow && this.launchedCdpProcess && this.launchedCdpProcess.exitCode === null && !this.launchedCdpProcess.killed) {
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
    if (!forceNewWindow || !this.launchedCdpProcess) {
      this.launchedCdpProcess = child;
      child.once?.('exit', () => {
        if (this.launchedCdpProcess === child) {
          this.launchedCdpProcess = null;
        }
      });
    }

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

  protected _parseCdpEndpoint(): { hostname: string; port: string } {
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

  protected _resolveChromeExecutablePath(): string {
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

  protected _candidateChromePaths(): string[] {
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

  protected _expandHome(pathValue: string): string {
    if (!pathValue.startsWith('~/')) {
      return pathValue;
    }
    return join(homedir(), pathValue.slice(2));
  }

  protected _looksLikePath(value: string): boolean {
    return value.includes('/') || value.includes('\\');
  }

  protected _pickAttachedContext(browser: BrowserDriver, preferNewest: boolean = false): BrowserContextDriver | null {
    const contexts = preferNewest ? [...browser.contexts()].reverse() : browser.contexts();
    if (contexts.length === 0) {
      return null;
    }

    return contexts.find((context) => context.pages().some((page) => this._isWhatsAppUrl(this._pageUrl(page)))) || contexts[0];
  }

  protected async _findAttachedWhatsAppPage(
    browser: BrowserDriver,
    requireReady: boolean,
    preferNewest: boolean = false,
  ): Promise<{ context: BrowserContextDriver; page: PageDriver } | null> {
    const contexts = preferNewest ? [...browser.contexts()].reverse() : browser.contexts();
    for (const context of contexts) {
      const pages = preferNewest ? [...context.pages()].reverse() : context.pages();
      for (const page of pages) {
        if (!this._isWhatsAppUrl(this._pageUrl(page))) {
          continue;
        }
        if (!requireReady) {
          return { context, page };
        }
        try {
          const ready = await this._findVisibleLocator(
            page,
            [...SEARCH_BOX_SELECTORS, ...COMPOSE_BOX_SELECTORS],
            READY_CHECK_TIMEOUT_MS,
          );
          if (ready) {
            return { context, page };
          }
        } catch {
          // Ignore detached/closed pages and keep scanning other attached tabs.
        }
      }
    }
    return null;
  }

  protected async _ensureWhatsAppPage(context: BrowserContextDriver): Promise<PageDriver> {
    if (this.browserMode === 'cdp' && this.browser) {
      const readyAttached = await this._findAttachedWhatsAppPage(this.browser, true, this.preferNewestAttachedPage);
      if (readyAttached) {
        this.context = readyAttached.context;
        this.page = readyAttached.page;
        this.preferNewestAttachedPage = false;
        return readyAttached.page;
      }

      const attached = await this._findAttachedWhatsAppPage(this.browser, false, this.preferNewestAttachedPage);
      if (attached) {
        this.context = attached.context;
        this.page = attached.page;
        this.preferNewestAttachedPage = false;
        return attached.page;
      }
    }

    const pages = context.pages();
    const orderedPages = this.preferNewestAttachedPage ? [...pages].reverse() : pages;
    const existingWhatsAppPage = orderedPages.find((page) => this._isWhatsAppUrl(this._pageUrl(page)));

    let page: PageDriver;
    if (existingWhatsAppPage) {
      page = existingWhatsAppPage;
    } else if (this.browserMode === 'cdp') {
      if (this.browser && !this.launchedCdpProcess) {
        throw new Error(
          'No reusable WhatsApp Web tab was found in the connected CDP browser. Keep the logged-in WhatsApp Web tab open and try again.',
        );
      }
      page = await context.newPage();
    } else {
      page = pages[0] ?? await context.newPage();
    }

    if (!this._isWhatsAppUrl(this._pageUrl(page))) {
      await page.goto(WHATSAPP_WEB_URL, { waitUntil: 'domcontentloaded' });
    }
    this.preferNewestAttachedPage = false;
    return page;
  }

  protected _pageUrl(page: PageDriver): string {
    try {
      return typeof page.url === 'function' ? String(page.url() || '') : '';
    } catch {
      return '';
    }
  }

  protected _isWhatsAppUrl(url: string): boolean {
    return String(url || '').startsWith(WHATSAPP_WEB_URL);
  }

  protected async _isPageResponsive(page: PageDriver): Promise<boolean> {
    try {
      await page.evaluate(() => String(location.href || ''));
      return true;
    } catch {
      return false;
    }
  }

  protected _isContextResponsive(context: BrowserContextDriver): boolean {
    try {
      context.pages();
      return true;
    } catch {
      return false;
    }
  }

  protected _isBrowserResponsive(browser: BrowserDriver): boolean {
    try {
      browser.contexts();
      return true;
    } catch {
      return false;
    }
  }

  protected async _openChatByPhone(page: PageDriver, phone: string): Promise<boolean> {
    await page.goto(
      `${WHATSAPP_WEB_URL}send?phone=${encodeURIComponent(phone)}&app_absent=0`,
      { waitUntil: 'domcontentloaded' },
    );
    const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, SEARCH_TIMEOUT_MS);
    return composeBox !== null;
  }

  protected async _searchAndOpenChat(page: PageDriver, target: ChatTarget): Promise<OpenTargetResult> {
    const searchBoxState = await this._getSearchBoxState(page);
    if (!searchBoxState.found) {
      return {
        status: 'session_unusable',
      };
    }

    for (const rawTerm of target.searchTerms) {
      const term = rawTerm.trim();
      if (!term) {
        continue;
      }

      if (!await this._resetSearchBox(page)) {
        return {
          status: 'session_unusable',
        };
      }

      if (!await this._typeSearchQuery(page, term)) {
        return {
          status: 'session_unusable',
          state: 'search_reset',
          matchedTerm: term,
        };
      }

      if (!await this._waitForMatchingSearchResultRow(page, target, term, SEARCH_TIMEOUT_MS)) {
        continue;
      }

      if (!await this._clickMatchingSearchResultRow(page, target, term)) {
        return {
          status: 'session_unusable',
          state: 'result_found',
          matchedTerm: term,
        };
      }

      if (await this._waitForOpenedChat(page, target, term, SEARCH_TIMEOUT_MS)) {
        return {
          status: 'opened',
          state: 'chat_opened',
          matchedTerm: term,
        };
      }

      return {
        status: 'session_unusable',
        state: 'result_found',
        matchedTerm: term,
      };
    }

    return {
      status: 'chat_not_found',
    };
  }

  protected async _openTarget(page: PageDriver, target: ChatTarget): Promise<boolean> {
    const result = await this._openTargetWithOptions(page, target, { allowPhoneNavigation: true });
    return result.status === 'opened';
  }

  protected async _openTargetWithOptions(
    page: PageDriver,
    target: ChatTarget,
    options: { allowPhoneNavigation: boolean },
  ): Promise<OpenTargetResult> {
    const searchResult = await this._searchAndOpenChat(page, target);
    if (searchResult.status === 'opened') {
      return searchResult;
    }

    if (options.allowPhoneNavigation && target.phone) {
      const openedByPhone = await this._openChatByPhone(page, target.phone);
      if (openedByPhone) {
        return {
          status: 'opened',
          state: 'chat_opened',
        };
      }
    }

    return searchResult;
  }

  protected async _openedChatMatchesTarget(
    page: PageDriver,
    target: ChatTarget,
    matchedTerm: string,
  ): Promise<boolean> {
    const snapshot = await page.evaluate(({ action }) => {
      if (action !== 'opened_chat_snapshot') {
        return {
          url: '',
          title: '',
          headerText: '',
          composeLabel: '',
          composePlaceholder: '',
        };
      }

      const header = document.querySelector('#main header')
        || document.querySelector('main header')
        || document.querySelector('[data-testid="conversation-header"]');
      const compose = document.querySelector('footer [data-testid="conversation-compose-box-input"]')
        || document.querySelector('footer div[aria-label][contenteditable="true"]')
        || document.querySelector('footer div[aria-placeholder][contenteditable="true"]')
        || document.querySelector('footer div[contenteditable="true"][role="textbox"]')
        || document.querySelector('footer div[contenteditable="true"]');
      return {
        url: String(location.href || ''),
        title: String(document.title || ''),
        headerText: String((header as HTMLElement | null)?.innerText || ''),
        composeLabel: String((compose as HTMLElement | null)?.getAttribute('aria-label') || ''),
        composePlaceholder: String((compose as HTMLElement | null)?.getAttribute('aria-placeholder') || ''),
      };
    }, { action: 'opened_chat_snapshot' }) as OpenedTargetSnapshot;

    const normalize = (value: string): string => String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
    const digits = (value: string): string => String(value || '').replace(/\D+/g, '');

    const combined = normalize(
      `${snapshot.title} ${snapshot.headerText} ${snapshot.composeLabel} ${snapshot.composePlaceholder}`,
    );
    const combinedDigits = digits(
      `${snapshot.title} ${snapshot.headerText} ${snapshot.composeLabel} ${snapshot.composePlaceholder}`,
    );
    const targetPhone = digits(target.phone || '');
    const matchedDigits = digits(matchedTerm);

    if (targetPhone) {
      if (combinedDigits.includes(targetPhone)) {
        return true;
      }
      if (snapshot.url.includes(`phone=${encodeURIComponent(target.phone || '')}`)) {
        return true;
      }
      if (matchedDigits && matchedDigits !== targetPhone) {
        return false;
      }
    }

    const strictTerms = [matchedTerm, ...(target.searchTerms || [])]
      .map((term) => normalize(term))
      .filter((term) => term.length >= 4);

    return strictTerms.some((term) => combined.includes(term));
  }

  protected async _waitForOpenedChat(
    page: PageDriver,
    target: ChatTarget,
    matchedTerm: string,
    timeoutMs: number,
  ): Promise<boolean> {
    const deadline = Date.now() + Math.max(timeoutMs, READY_CHECK_TIMEOUT_MS);
    while (Date.now() < deadline) {
      const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, READY_CHECK_TIMEOUT_MS);
      if (composeBox && await this._openedChatMatchesTarget(page, target, matchedTerm)) {
        return true;
      }
      await page.waitForTimeout?.(200);
    }
    return false;
  }

  protected async _ensureWhatsAppReady(page: PageDriver, timeoutMs: number = LOGIN_WAIT_TIMEOUT_MS): Promise<boolean> {
    const selectors = [...SEARCH_BOX_SELECTORS, ...COMPOSE_BOX_SELECTORS];
    if (await this._findVisibleLocator(page, selectors, READY_CHECK_TIMEOUT_MS)) {
      return true;
    }

    const deadline = Date.now() + Math.max(timeoutMs, READY_CHECK_TIMEOUT_MS);
    while (Date.now() < deadline) {
      if (await this._findVisibleLocator(page, selectors, READY_CHECK_TIMEOUT_MS)) {
        return true;
      }
      await page.waitForTimeout?.(READY_POLL_WAIT_MS);
    }

    return false;
  }

  protected async _getSearchBoxState(page: PageDriver): Promise<SearchBoxState> {
    return page.evaluate(({ action, selectors }) => {
      if (action !== 'search_box_state') {
        return { found: false, value: '' };
      }

      const isVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      const readValue = (node: HTMLElement): string => {
        if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
          return node.value;
        }
        return node.innerText || node.textContent || '';
      };

      const normalize = (value: string): string => String(value || '')
        .replace(/\u200B/g, '')
        .replace(/\s+/g, ' ')
        .trim();

      for (const selector of selectors) {
        for (const node of Array.from(document.querySelectorAll(selector))) {
          if (!isVisible(node) || node.closest('footer')) {
            continue;
          }
          return {
            found: true,
            value: normalize(readValue(node as HTMLElement)),
          };
        }
      }

      return { found: false, value: '' };
    }, { action: 'search_box_state', selectors: SEARCH_BOX_SELECTORS });
  }

  protected async _resetSearchBox(page: PageDriver): Promise<boolean> {
    const cleared = await page.evaluate(({ action, selectors }) => {
      if (action !== 'reset_search_box') {
        return false;
      }

      const isVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      const readValue = (node: HTMLElement): string => {
        if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
          return node.value;
        }
        return node.innerText || node.textContent || '';
      };

      const normalize = (value: string): string => String(value || '')
        .replace(/\u200B/g, '')
        .replace(/\s+/g, ' ')
        .trim();

      const clearNode = (node: HTMLElement): void => {
        if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
          node.value = '';
          node.dispatchEvent(new Event('input', { bubbles: true }));
          node.dispatchEvent(new Event('change', { bubbles: true }));
          return;
        }

        node.textContent = '';
        try {
          node.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            inputType: 'deleteContentBackward',
            data: null,
          }));
        } catch {
          node.dispatchEvent(new Event('input', { bubbles: true }));
        }
      };

      for (const selector of selectors) {
        for (const node of Array.from(document.querySelectorAll(selector))) {
          if (!isVisible(node) || node.closest('footer')) {
            continue;
          }
          const element = node as HTMLElement;
          element.focus();
          element.click();
          clearNode(element);
          return normalize(readValue(element)) === '';
        }
      }

      return false;
    }, { action: 'reset_search_box', selectors: SEARCH_BOX_SELECTORS });

    if (!cleared) {
      return false;
    }

    await page.waitForTimeout?.(150);
    const state = await this._getSearchBoxState(page);
    return state.found && state.value === '';
  }

  protected async _typeSearchQuery(page: PageDriver, term: string): Promise<boolean> {
    if (!(await this._focusExactSearchBox(page))) {
      return false;
    }

    await page.keyboard.insertText(term);
    await page.waitForTimeout?.(250);

    const state = await this._getSearchBoxState(page);
    return state.found && state.value === this._normalizeSearchValue(term);
  }

  protected async _focusExactSearchBox(page: PageDriver): Promise<boolean> {
    return page.evaluate(({ action, selectors }) => {
      if (action !== 'focus_exact_search_box') {
        return false;
      }

      const isVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      for (const selector of selectors) {
        for (const node of Array.from(document.querySelectorAll(selector))) {
          if (!isVisible(node) || node.closest('footer')) {
            continue;
          }
          (node as HTMLElement).focus();
          (node as HTMLElement).click();
          return true;
        }
      }

      return false;
    }, { action: 'focus_exact_search_box', selectors: SEARCH_BOX_SELECTORS });
  }

  protected async _waitForMatchingSearchResultRow(
    page: PageDriver,
    target: ChatTarget,
    query: string,
    timeoutMs: number,
  ): Promise<boolean> {
    const deadline = Date.now() + Math.max(timeoutMs, READY_CHECK_TIMEOUT_MS);
    while (Date.now() < deadline) {
      if (await this._hasMatchingSearchResultRow(page, target, query)) {
        return true;
      }
      await page.waitForTimeout?.(200);
    }
    return false;
  }

  protected async _hasMatchingSearchResultRow(
    page: PageDriver,
    target: ChatTarget,
    query: string,
  ): Promise<boolean> {
    return page.evaluate(({ action, target, query }) => {
      if (action !== 'search_result_row_visible') {
        return false;
      }

      const isVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      const normalize = (value: string): string => String(value || '')
        .toLowerCase()
        .replace(/\u200B/g, '')
        .replace(/\s+/g, ' ')
        .trim();
      const digits = (value: string): string => String(value || '').replace(/\D+/g, '');

      const root = document.querySelector('#pane-side');
      if (!(root instanceof HTMLElement) || !isVisible(root)) {
        return false;
      }

      const seen = new Set<HTMLElement>();
      const rows: HTMLElement[] = [];
      const selectors = [
        '[role="listitem"]',
        'div[data-testid="cell-frame-container"]',
        'a[role="link"]',
      ];

      const addRow = (node: Element | null): void => {
        if (!(node instanceof HTMLElement) || !root.contains(node) || !isVisible(node) || seen.has(node)) {
          return;
        }
        seen.add(node);
        rows.push(node);
      };

      for (const selector of selectors) {
        for (const node of Array.from(root.querySelectorAll(selector))) {
          addRow(node);
        }
      }

      for (const node of Array.from(root.querySelectorAll('*'))) {
        if (!(node instanceof HTMLElement) || !isVisible(node)) {
          continue;
        }
        const row = node.closest(
          '[role="listitem"], [role="button"], a[role="link"], div[data-testid="cell-frame-container"], div[tabindex="0"], div[tabindex="-1"]',
        );
        addRow(row);
      }

      const targetPhone = digits(String(target.phone || ''));
      const queryDigits = digits(String(query || ''));
      const queryTerm = normalize(String(query || ''));
      const terms = [queryTerm, ...(Array.isArray(target.searchTerms) ? target.searchTerms : [])]
        .map((value) => normalize(String(value || '')))
        .filter((value, index, all) => value && all.indexOf(value) === index && (value.length >= 4 || value === queryTerm));

      const scoreRow = (row: HTMLElement): number => {
        const rowText = normalize(row.innerText || row.textContent || '');
        const rowDigits = digits(rowText);
        if (!rowText && !rowDigits) {
          return 0;
        }

        let score = 0;
        if (targetPhone && rowDigits.includes(targetPhone)) {
          score = Math.max(score, 1000);
        }
        if (queryDigits && rowDigits.includes(queryDigits)) {
          score = Math.max(score, 700 + queryDigits.length);
        }
        for (const term of terms) {
          if (rowText.includes(term)) {
            score = Math.max(score, 400 + term.length);
          }
        }
        return score;
      };

      return rows.some((row) => scoreRow(row) > 0);
    }, { action: 'search_result_row_visible', target, query });
  }

  protected async _clickMatchingSearchResultRow(
    page: PageDriver,
    target: ChatTarget,
    query: string,
  ): Promise<boolean> {
    return page.evaluate(({ action, target, query }) => {
      if (action !== 'click_search_result_row') {
        return false;
      }

      const isVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      const normalize = (value: string): string => String(value || '')
        .toLowerCase()
        .replace(/\u200B/g, '')
        .replace(/\s+/g, ' ')
        .trim();
      const digits = (value: string): string => String(value || '').replace(/\D+/g, '');

      const root = document.querySelector('#pane-side');
      if (!(root instanceof HTMLElement) || !isVisible(root)) {
        return false;
      }

      const seen = new Set<HTMLElement>();
      const rows: HTMLElement[] = [];
      const selectors = [
        '[role="listitem"]',
        'div[data-testid="cell-frame-container"]',
        'a[role="link"]',
      ];

      const addRow = (node: Element | null): void => {
        if (!(node instanceof HTMLElement) || !root.contains(node) || !isVisible(node) || seen.has(node)) {
          return;
        }
        seen.add(node);
        rows.push(node);
      };

      for (const selector of selectors) {
        for (const node of Array.from(root.querySelectorAll(selector))) {
          addRow(node);
        }
      }

      for (const node of Array.from(root.querySelectorAll('*'))) {
        if (!(node instanceof HTMLElement) || !isVisible(node)) {
          continue;
        }
        const row = node.closest(
          '[role="listitem"], [role="button"], a[role="link"], div[data-testid="cell-frame-container"], div[tabindex="0"], div[tabindex="-1"]',
        );
        addRow(row);
      }

      const targetPhone = digits(String(target.phone || ''));
      const queryDigits = digits(String(query || ''));
      const queryTerm = normalize(String(query || ''));
      const terms = [queryTerm, ...(Array.isArray(target.searchTerms) ? target.searchTerms : [])]
        .map((value) => normalize(String(value || '')))
        .filter((value, index, all) => value && all.indexOf(value) === index && (value.length >= 4 || value === queryTerm));

      const scoreRow = (row: HTMLElement): number => {
        const rowText = normalize(row.innerText || row.textContent || '');
        const rowDigits = digits(rowText);
        if (!rowText && !rowDigits) {
          return 0;
        }

        let score = 0;
        if (targetPhone && rowDigits.includes(targetPhone)) {
          score = Math.max(score, 1000);
        }
        if (queryDigits && rowDigits.includes(queryDigits)) {
          score = Math.max(score, 700 + queryDigits.length);
        }
        for (const term of terms) {
          if (rowText.includes(term)) {
            score = Math.max(score, 400 + term.length);
          }
        }
        return score;
      };

      let bestRow: HTMLElement | null = null;
      let bestScore = 0;
      for (const row of rows) {
        const score = scoreRow(row);
        if (score > bestScore) {
          bestScore = score;
          bestRow = row;
        }
      }

      if (!bestRow || bestScore <= 0) {
        return false;
      }

      bestRow.click();
      return true;
    }, { action: 'click_search_result_row', target, query });
  }

  protected _chatResultSelectors(term: string): string[] {
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

  protected async _findVisibleLocator(
    page: PageDriver,
    selectors: readonly string[],
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

  protected async _focusFirstVisibleElement(page: PageDriver, selectors: readonly string[]): Promise<boolean> {
    return page.evaluate(({ selectors }) => {
      const isVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      for (const selector of selectors) {
        for (const node of Array.from(document.querySelectorAll(selector))) {
          if (!isVisible(node)) {
            continue;
          }
          node.focus();
          node.click();
          return true;
        }
      }

      return false;
    }, { selectors });
  }

  protected async _clickFirstVisibleElement(page: PageDriver, selectors: readonly string[]): Promise<boolean> {
    return page.evaluate(({ selectors }) => {
      const isVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      for (const selector of selectors) {
        for (const node of Array.from(document.querySelectorAll(selector))) {
          if (!isVisible(node)) {
            continue;
          }
          node.click();
          return true;
        }
      }

      return false;
    }, { selectors });
  }

  protected async _clearFocusedTextbox(page: PageDriver): Promise<void> {
    await this._tryPress(page.keyboard, 'Meta+A');
    await this._tryPress(page.keyboard, 'Control+A');
    await this._tryPress(page.keyboard, 'Backspace');
  }

  protected async _tryPress(keyboard: KeyboardDriver, key: string): Promise<void> {
    try {
      await keyboard.press(key);
    } catch {
      // Ignore platform-specific keyboard shortcuts that are not available.
    }
  }

  protected async _serialize<T>(operation: () => Promise<T>): Promise<T> {
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

  protected _normalizeSearchValue(value: string): string {
    return String(value || '')
      .replace(/\u200B/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }
}

function cssEscape(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function sleep(timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, timeoutMs);
  });
}
