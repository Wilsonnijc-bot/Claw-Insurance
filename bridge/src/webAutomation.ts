import { spawn as spawnProcess, type ChildProcess, type SpawnOptions } from 'child_process';
import { lookup as dnsLookup } from 'dns/promises';
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
  nth(index: number): LocatorDriver;
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

export interface CdpHelperEnsureRequest {
  helperUrl: string;
  endpointUrl: string;
  profileDir: string;
  startUrl: string;
  chromePath?: string;
  forceNewWindow?: boolean;
  helperToken?: string;
}

export interface CdpHelperEnsureResult {
  status: 'reused' | 'launched' | 'failed';
  detail?: string;
  endpointUrl?: string;
}

export interface CdpHelperClient {
  ensureBrowser(request: CdpHelperEnsureRequest): Promise<CdpHelperEnsureResult>;
}

export type CdpHostResolver = (hostname: string) => Promise<string>;

export const DEFAULT_BROWSER_CONNECTOR: BrowserConnector = chromium;
export const DEFAULT_BROWSER_LAUNCHER: BrowserLauncher = spawnProcess as BrowserLauncher;
export const DEFAULT_CDP_HOST_RESOLVER: CdpHostResolver = async (hostname: string) => {
  const result = await dnsLookup(hostname);
  return result.address;
};
export const DEFAULT_CDP_HELPER_CLIENT: CdpHelperClient = {
  async ensureBrowser({
    helperUrl,
    endpointUrl,
    profileDir,
    startUrl,
    chromePath,
    forceNewWindow,
    helperToken,
  }: CdpHelperEnsureRequest): Promise<CdpHelperEnsureResult> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (helperToken && helperToken.trim()) {
      headers.Authorization = `Bearer ${helperToken.trim()}`;
    }
    const response = await fetch(`${helperUrl.replace(/\/+$/, '')}/v1/cdp/ensure`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        endpointUrl,
        profileDir,
        startUrl,
        chromePath,
        forceNewWindow: Boolean(forceNewWindow),
      }),
    });
    const rawBody = await response.text();
    const payload = rawBody ? JSON.parse(rawBody) as Record<string, unknown> : {};
    if (!response.ok) {
      const detail = typeof payload.detail === 'string' ? payload.detail : `CDP helper returned HTTP ${response.status}.`;
      throw new Error(detail);
    }
    const status = payload.status === 'reused' || payload.status === 'launched'
      ? payload.status
      : 'failed';
    return {
      status,
      detail: typeof payload.detail === 'string' ? payload.detail : '',
      endpointUrl: typeof payload.endpointUrl === 'string' ? payload.endpointUrl : endpointUrl,
    };
  },
};

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
export const POST_SEARCH_SETTLE_WAIT_MS = 3000;

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

export const SEARCH_RESULT_ROW_SELECTOR = 'div[role="gridcell"][tabindex="0"]';

export class ParseSessionUnavailableError extends Error {}

interface OpenedTargetSnapshot {
  url: string;
  title: string;
  headerFound: boolean;
  headerText: string;
  composeLabel: string;
  composePlaceholder: string;
}

interface SearchBoxState {
  found: boolean;
  value: string;
}

interface SearchResultsRefreshState {
  queryMatches: boolean;
  refreshed: boolean;
  hasRow: boolean;
}

export class WhatsAppWebSession {
  protected browser: BrowserDriver | null = null;
  protected context: BrowserContextDriver | null = null;
  protected page: PageDriver | null = null;
  protected queue: Promise<void> = Promise.resolve();
  protected launchedCdpProcess: SpawnedBrowserProcess | null = null;
  protected preferNewestAttachedPage: boolean = false;
  protected lastCdpAcquisition: 'attached' | 'helper_reused' | 'helper_launched' | 'local_launch' | null = null;
  protected resolvedCdpEndpoint: string | null = null;

  constructor(
    protected readonly userDataDir: string,
    protected readonly browserConnector: BrowserConnector = DEFAULT_BROWSER_CONNECTOR,
    protected readonly browserMode: BrowserMode = 'cdp',
    protected readonly cdpEndpoint: string = 'http://127.0.0.1:9222',
    protected readonly cdpChromePath: string = process.env.WEB_CDP_CHROME_PATH || '',
    protected readonly browserLauncher: BrowserLauncher = DEFAULT_BROWSER_LAUNCHER,
    protected readonly cdpHelperUrl: string = process.env.WEB_CDP_HELPER_URL || '',
    protected readonly hostProfileDir: string = process.env.WEB_HOST_PROFILE_DIR || '',
    protected readonly cdpHelperClient: CdpHelperClient = DEFAULT_CDP_HELPER_CLIENT,
    protected readonly cdpHostResolver: CdpHostResolver = DEFAULT_CDP_HOST_RESOLVER,
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
    this.lastCdpAcquisition = null;
    this.resolvedCdpEndpoint = null;
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
    this.lastCdpAcquisition = null;
    const attached = await this._tryConnectOverCDP();
    if (attached) {
      this.lastCdpAcquisition = 'attached';
      return attached;
    }

    const helperResult = await this._ensureCdpBrowserViaHelper();
    if (helperResult) {
      const acquired = await this._waitForCDPBrowser();
      if (acquired) {
        this.lastCdpAcquisition = helperResult.status === 'launched' ? 'helper_launched' : 'helper_reused';
        return acquired;
      }
      throw new Error(
        helperResult.detail
        || `Host Chrome CDP is not reachable at ${this.cdpEndpoint} after the host helper ran.`,
      );
    }

    await this._launchCdpBrowser();
    const launched = await this._waitForCDPBrowser();
    if (launched) {
      this.lastCdpAcquisition = 'local_launch';
      return launched;
    }

    throw new Error(
      `CDP browser is not available at ${this.cdpEndpoint}. Start Chrome with --remote-debugging-port or set webCdpChromePath so nanobot can launch it.`,
    );
  }

  protected async _tryConnectOverCDP(): Promise<BrowserDriver | null> {
    try {
      return await this.browserConnector.connectOverCDP(await this._effectiveCdpEndpoint());
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

  protected async _ensureCdpBrowserViaHelper(forceNewWindow: boolean = false): Promise<CdpHelperEnsureResult | null> {
    const helperUrl = this.cdpHelperUrl.trim();
    if (!helperUrl) {
      return null;
    }

    try {
      const helperToken = (process.env.WEB_CDP_HELPER_TOKEN || '').trim();
      const request: CdpHelperEnsureRequest = {
        helperUrl,
        endpointUrl: this.cdpEndpoint,
        profileDir: this._effectiveHostProfileDir(),
        startUrl: WHATSAPP_WEB_URL,
        chromePath: this.cdpChromePath.trim() || undefined,
        forceNewWindow,
        ...(helperToken ? { helperToken } : {}),
      };
      const result = await this.cdpHelperClient.ensureBrowser(request);
      if (result.status === 'failed') {
        throw new Error(
          result.detail
          || `Host CDP helper failed to launch or reuse Chrome for ${this.cdpEndpoint}.`,
        );
      }
      return result;
    } catch (error) {
      const detail = this._stringifyError(error);
      throw new Error(
        `Host CDP helper is not installed/running at ${helperUrl}, or it could not launch Chrome successfully. ${detail}`.trim(),
      );
    }
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
          reject(this._normalizeBrowserLaunchError(error, executable));
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

  protected _effectiveHostProfileDir(): string {
    const raw = (this.hostProfileDir || this.userDataDir).trim();
    return String(this._expandHome(raw));
  }

  protected async _effectiveCdpEndpoint(): Promise<string> {
    if (this.resolvedCdpEndpoint) {
      return this.resolvedCdpEndpoint;
    }

    try {
      const parsed = new URL(this.cdpEndpoint);
      const hostname = String(parsed.hostname || '').trim();
      if (!hostname || this._isLocalOrIpHost(hostname)) {
        return this.cdpEndpoint;
      }
      const resolvedHost = await this.cdpHostResolver(hostname);
      if (!resolvedHost) {
        return this.cdpEndpoint;
      }
      parsed.hostname = resolvedHost;
      this.resolvedCdpEndpoint = parsed.toString();
      return this.resolvedCdpEndpoint;
    } catch {
      return this.cdpEndpoint;
    }
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

  protected _isLocalOrIpHost(hostname: string): boolean {
    return hostname === 'localhost'
      || hostname === '127.0.0.1'
      || hostname === '::1'
      || /^[0-9.]+$/.test(hostname)
      || hostname.includes(':');
  }

  protected _normalizeBrowserLaunchError(error: unknown, executable: string): Error {
    const detail = this._stringifyError(error);
    if (detail.includes('ENOENT')) {
      return new Error(
        `No Chrome/Chromium executable was found for CDP launch. Tried ${executable}.`,
      );
    }
    return new Error(`Failed to launch the CDP browser: ${detail}`);
  }

  protected _stringifyError(error: unknown): string {
    if (error instanceof Error) {
      return error.message || String(error);
    }
    return String(error || '');
  }

  protected _cdpLoginRequiredDetail(): string {
    switch (this.lastCdpAcquisition) {
      case 'helper_launched':
      case 'local_launch':
        return 'Chrome window opened. Scan the WhatsApp Web QR code in that window, wait for login to finish, then retry sync.';
      case 'helper_reused':
      case 'attached':
        return 'WhatsApp Web is not logged in or not ready in the reusable CDP browser. Finish login there and retry sync.';
      default:
        return 'WhatsApp Web is not logged in or not ready for history parsing.';
    }
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

  protected async _getOpenedChatSnapshot(page: PageDriver): Promise<OpenedTargetSnapshot> {
    return await page.evaluate(({ action }) => {
      if (action !== 'opened_chat_snapshot') {
        return {
          url: '',
          title: '',
          headerFound: false,
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
        headerFound: header instanceof HTMLElement,
        headerText: String((header as HTMLElement | null)?.innerText || ''),
        composeLabel: String((compose as HTMLElement | null)?.getAttribute('aria-label') || ''),
        composePlaceholder: String((compose as HTMLElement | null)?.getAttribute('aria-placeholder') || ''),
      };
    }, { action: 'opened_chat_snapshot' }) as OpenedTargetSnapshot;
  }

  protected async _openedChatIsReady(page: PageDriver): Promise<boolean> {
    const snapshot = await this._getOpenedChatSnapshot(page);
    return snapshot.headerFound;
  }

  protected async _waitForOpenedChatReady(
    page: PageDriver,
    timeoutMs: number,
  ): Promise<boolean> {
    const deadline = Date.now() + Math.max(timeoutMs, READY_CHECK_TIMEOUT_MS);
    while (Date.now() < deadline) {
      const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, READY_CHECK_TIMEOUT_MS);
      if (composeBox && await this._openedChatIsReady(page)) {
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

  protected async _armSearchResultsRefreshObserver(page: PageDriver): Promise<number | null> {
    return page.evaluate(({ action }) => {
      if (action !== 'arm_search_results_refresh_observer') {
        return null;
      }

      const root = document.querySelector('#pane-side');
      if (!(root instanceof HTMLElement)) {
        return null;
      }

      type SearchResultsWindow = Window & typeof globalThis & {
        __nanobotSearchResultsMutationCount?: number;
        __nanobotSearchResultsObserver?: MutationObserver;
        __nanobotSearchResultsObserverRoot?: HTMLElement | null;
      };

      const scopedWindow = window as SearchResultsWindow;
      if (scopedWindow.__nanobotSearchResultsObserverRoot !== root || !scopedWindow.__nanobotSearchResultsObserver) {
        scopedWindow.__nanobotSearchResultsObserver?.disconnect();
        scopedWindow.__nanobotSearchResultsMutationCount = 0;
        scopedWindow.__nanobotSearchResultsObserverRoot = root;
        scopedWindow.__nanobotSearchResultsObserver = new MutationObserver((records) => {
          if (records.length > 0) {
            scopedWindow.__nanobotSearchResultsMutationCount = (scopedWindow.__nanobotSearchResultsMutationCount || 0) + 1;
          }
        });
        scopedWindow.__nanobotSearchResultsObserver.observe(root, {
          childList: true,
          subtree: true,
          characterData: true,
        });
      } else if (typeof scopedWindow.__nanobotSearchResultsMutationCount !== 'number') {
        scopedWindow.__nanobotSearchResultsMutationCount = 0;
      }

      return scopedWindow.__nanobotSearchResultsMutationCount ?? 0;
    }, { action: 'arm_search_results_refresh_observer' }) as Promise<number | null>;
  }

  protected async _waitForFirstSearchResultRow(
    page: PageDriver,
    expectedQuery: string,
    baselineMutationCount: number,
    timeoutMs: number,
  ): Promise<boolean> {
    const deadline = Date.now() + Math.max(timeoutMs, READY_CHECK_TIMEOUT_MS);
    while (Date.now() < deadline) {
      if (await this._hasFirstSearchResultRow(page, expectedQuery, baselineMutationCount)) {
        return true;
      }
      await page.waitForTimeout?.(200);
    }
    return false;
  }

  protected async _hasFirstSearchResultRow(
    page: PageDriver,
    expectedQuery: string,
    baselineMutationCount: number,
  ): Promise<boolean> {
    const state = await this._getSearchResultsRefreshState(page, expectedQuery, baselineMutationCount);
    return state.queryMatches && state.refreshed && state.hasRow;
  }

  protected async _getSearchResultsRefreshState(
    page: PageDriver,
    expectedQuery: string,
    baselineMutationCount: number,
  ): Promise<SearchResultsRefreshState> {
    return page.evaluate(({ action, selectors, expectedValue, selector, baseline }) => {
      if (action !== 'search_results_refresh_state') {
        return {
          queryMatches: false,
          refreshed: false,
          hasRow: false,
        };
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

      const isRowVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width >= 80
          && rect.height >= 24;
      };

      const normalize = (value: string): string => String(value || '')
        .replace(/\u200B/g, '')
        .replace(/\s+/g, ' ')
        .trim();

      const readValue = (node: HTMLElement): string => {
        if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
          return node.value;
        }
        return node.innerText || node.textContent || '';
      };

      type SearchResultsWindow = Window & typeof globalThis & {
        __nanobotSearchResultsMutationCount?: number;
      };

      const scopedWindow = window as SearchResultsWindow;
      const root = document.querySelector('#pane-side');
      if (!(root instanceof HTMLElement) || !isVisible(root)) {
        return {
          queryMatches: false,
          refreshed: false,
          hasRow: false,
        };
      }

      let queryMatches = false;
      for (const searchSelector of selectors) {
        for (const node of Array.from(document.querySelectorAll(searchSelector))) {
          if (!isVisible(node) || node.closest('footer')) {
            continue;
          }
          queryMatches = normalize(readValue(node as HTMLElement)) === normalize(expectedValue);
          break;
        }
        if (queryMatches) {
          break;
        }
      }

      const mutationCount = scopedWindow.__nanobotSearchResultsMutationCount || 0;
      const hasRow = Array.from(root.querySelectorAll(selector)).some((node) => isRowVisible(node));

      return {
        queryMatches,
        refreshed: mutationCount > baseline,
        hasRow,
      };
    }, {
      action: 'search_results_refresh_state',
      selectors: SEARCH_BOX_SELECTORS,
      expectedValue: expectedQuery,
      selector: SEARCH_RESULT_ROW_SELECTOR,
      baseline: baselineMutationCount,
    }) as Promise<SearchResultsRefreshState>;
  }

  protected async _getFirstSearchResultRowIndex(page: PageDriver): Promise<number | null> {
    return page.evaluate(({ action, selector }) => {
      if (action !== 'first_search_result_row_index') {
        return null;
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

      const isRowVisible = (node: Element | null): node is HTMLElement => {
        if (!(node instanceof HTMLElement)) {
          return false;
        }
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width >= 80
          && rect.height >= 24;
      };

      const root = document.querySelector('#pane-side');
      if (!(root instanceof HTMLElement) || !isVisible(root)) {
        return null;
      }

      const candidates = Array.from(root.querySelectorAll(selector))
        .map((node, index) => ({ node, index }))
        .filter(({ node }) => isRowVisible(node))
        .map(({ node, index }) => {
          const rect = (node as HTMLElement).getBoundingClientRect();
          return {
            index,
            top: rect.top,
            left: rect.left,
          };
        })
        .sort((left, right) => left.top - right.top || left.left - right.left);

      return candidates[0]?.index ?? null;
    }, { action: 'first_search_result_row_index', selector: SEARCH_RESULT_ROW_SELECTOR }) as Promise<number | null>;
  }

  protected async _clickFirstSearchResultRow(page: PageDriver): Promise<boolean> {
    const rowIndex = await this._getFirstSearchResultRowIndex(page);
    if (rowIndex === null) {
      return false;
    }

    const locator = page.locator(`#pane-side ${SEARCH_RESULT_ROW_SELECTOR}`).nth(rowIndex);
    try {
      await locator.waitFor({ state: 'visible', timeout: READY_CHECK_TIMEOUT_MS });
      if ((await locator.count()) === 0) {
        return false;
      }
      await locator.click();
      return true;
    } catch {
      return false;
    }
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

function sleep(timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, timeoutMs);
  });
}
