import {
  type BrowserConnector,
  type BrowserLauncher,
  type BrowserMode,
  type ChatTarget,
  COMPOSE_BOX_SELECTORS,
  DEFAULT_BROWSER_CONNECTOR,
  DEFAULT_BROWSER_LAUNCHER,
  HISTORY_SCROLL_LIMIT,
  HISTORY_SCROLL_WAIT_MS,
  ParseSessionUnavailableError,
  type PageDriver,
  POST_SEARCH_SETTLE_WAIT_MS,
  READY_CHECK_TIMEOUT_MS,
  READY_TIMEOUT_MS,
  SEARCH_TIMEOUT_MS,
  WhatsAppWebSession,
} from './webAutomation.js';

export interface ScrapedHistoryMessage {
  id: string;
  content: string;
  timestamp: string;
  fromMe: boolean;
  pushName?: string;
}

export interface HistoryScrapeResult {
  status: 'history_scraped' | 'chat_not_found' | 'login_required' | 'window_launch_failed';
  detail?: string;
  messages?: ScrapedHistoryMessage[];
}

export interface BulkHistoryTargetResult {
  target: ChatTarget;
  status: 'history_scraped' | 'chat_not_found';
  messages?: ScrapedHistoryMessage[];
}

export interface BulkHistoryScrapeResult {
  status: 'history_scraped' | 'login_required' | 'window_launch_failed';
  detail?: string;
  results: BulkHistoryTargetResult[];
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

export class HistoryParser extends WhatsAppWebSession {
  constructor(
    userDataDir: string,
    browserConnector: BrowserConnector = DEFAULT_BROWSER_CONNECTOR,
    browserMode: BrowserMode = 'cdp',
    cdpEndpoint: string = 'http://127.0.0.1:9222',
    cdpChromePath: string = process.env.WEB_CDP_CHROME_PATH || '',
    browserLauncher: BrowserLauncher = DEFAULT_BROWSER_LAUNCHER,
  ) {
    super(
      userDataDir,
      browserConnector,
      browserMode,
      cdpEndpoint,
      cdpChromePath,
      browserLauncher,
    );
  }

  async scrapeHistory(target: ChatTarget): Promise<HistoryScrapeResult> {
    return this._serialize(async () => this._scrapeHistory(target));
  }

  async scrapeReplyTargets(targets: ChatTarget[]): Promise<BulkHistoryScrapeResult> {
    return this._serialize(async () => this._scrapeReplyTargets(targets));
  }

  private async _scrapeHistory(target: ChatTarget): Promise<HistoryScrapeResult> {
    const phone = String(target.phone || '').trim();
    if (!phone) {
      return {
        status: 'chat_not_found',
        detail: `Chat ${target.chatId} is not available for history parsing without a normalized phone.`,
      };
    }

    return this._runParseWithSessionReuse(async (page) => {
      if (!await this._ensureWhatsAppReady(page)) {
        return {
          status: 'login_required',
          detail: 'WhatsApp Web is not logged in or not ready for history parsing.',
        };
      }

      const result = await this._scrapeTargetOnPage(page, target);
      if (result.status === 'session_unusable') {
        throw new ParseSessionUnavailableError('WhatsApp Web attached tab is not usable for history parsing.');
      }
      if (result.status !== 'history_scraped') {
        return {
          status: 'chat_not_found',
          detail: `Chat ${target.chatId} is not available in WhatsApp Web search.`,
        };
      }
      return {
        status: 'history_scraped',
        messages: result.messages,
      };
    });
  }

  private async _scrapeReplyTargets(targets: ChatTarget[]): Promise<BulkHistoryScrapeResult> {
    const normalizedTargets = targets.filter((target) => {
      const chatId = String(target?.chatId || '').trim();
      const phone = String(target?.phone || '').trim();
      return Boolean(chatId && phone);
    });
    if (normalizedTargets.length === 0) {
      return {
        status: 'history_scraped',
        detail: 'No reply targets with a normalized phone were provided for history parsing.',
        results: [],
      };
    }

    return this._runParseWithSessionReuse(async (page) => {
      if (!await this._ensureWhatsAppReady(page)) {
        return {
          status: 'login_required',
          detail: 'WhatsApp Web is not logged in or not ready for history parsing.',
          results: [],
        };
      }
      const results: BulkHistoryTargetResult[] = [];
      for (const target of normalizedTargets) {
        const result = await this._scrapeTargetOnPage(page, target);
        if (result.status === 'session_unusable') {
          throw new ParseSessionUnavailableError('WhatsApp Web attached tab is not usable for history parsing.');
        }
        if (result.status === 'chat_not_found') {
          results.push({
            target,
            status: 'chat_not_found',
          });
          continue;
        }
        results.push({
          target,
          status: 'history_scraped',
          messages: result.messages,
        });
      }
      return {
        status: 'history_scraped',
        results,
      };
    });
  }

  private _mapParseError(error: unknown): HistoryScrapeResult {
    const detail = String(error || '').trim();
    const message = detail || 'WhatsApp Web is not ready for history parsing.';

    if (
      detail.includes('CDP browser is not available at')
      || detail.includes('No Chrome/Chromium executable was found')
      || detail.includes('Configured Chrome executable does not exist')
      || detail.includes('Failed to launch the CDP browser')
    ) {
      return {
        status: 'window_launch_failed',
        detail: message,
      };
    }

    return {
      status: 'login_required',
      detail: message,
    };
  }

  private async _runParseWithSessionReuse<T extends { status: string; detail?: string }>(
    operation: (page: PageDriver) => Promise<T>,
  ): Promise<T> {
    let initial: T;
    try {
      const page = await this._prepareParsePage();
      initial = await operation(page);
    } catch (error) {
      const failure = this._mapParseError(error) as T;
      if (this.browserMode !== 'cdp') {
        return failure;
      }
      const retried = await this._retryParseInFreshWindow(operation);
      return retried ?? failure;
    }

    if (this.browserMode === 'cdp' && initial.status === 'login_required') {
      const retried = await this._retryParseInFreshWindow(operation);
      return retried ?? initial;
    }

    return initial;
  }

  private async _retryParseInFreshWindow<T extends { status: string; detail?: string }>(
    operation: (page: PageDriver) => Promise<T>,
  ): Promise<T | null> {
    try {
      await this._openFreshCdpWindow();
      const page = await this._prepareParsePage();
      return await operation(page);
    } catch (error) {
      return this._mapParseError(error) as T;
    }
  }

  private async _prepareParsePage(): Promise<PageDriver> {
    const page = await this._ensurePage();
    if (page.bringToFront) {
      await page.bringToFront();
    }
    return page;
  }

  private async _openFreshCdpWindow(): Promise<void> {
    if (this.browserMode !== 'cdp') {
      return;
    }
    this.page = null;
    this.context = null;
    this.browser = null;
    this.preferNewestAttachedPage = true;
    await this._launchCdpBrowser(true);
    this.browser = await this._waitForCDPBrowser();
    if (!this.browser) {
      throw new Error(
        `CDP browser is not available at ${this.cdpEndpoint}. Start Chrome with --remote-debugging-port or set webCdpChromePath so nanobot can launch it.`,
      );
    }
    this.context = null;
    this.page = null;
  }

  private async _openTargetForParse(
    page: PageDriver,
    target: ChatTarget,
  ): Promise<'opened' | 'chat_not_found' | 'session_unusable'> {
    const query = String(target.phone || '').trim();
    if (!query) {
      return 'chat_not_found';
    }
    const searchBoxState = await this._getSearchBoxState(page);
    if (!searchBoxState.found) {
      return 'session_unusable';
    }
    if (!await this._focusExactSearchBox(page)) {
      return 'session_unusable';
    }
    if (!await this._resetSearchBox(page)) {
      return 'session_unusable';
    }
    const searchResultsMutationBaseline = await this._armSearchResultsRefreshObserver(page);
    if (searchResultsMutationBaseline === null) {
      return 'session_unusable';
    }
    if (!await this._typeSearchQuery(page, query)) {
      return 'session_unusable';
    }
    await page.waitForTimeout?.(POST_SEARCH_SETTLE_WAIT_MS);
    if (!await this._waitForFirstSearchResultRow(page, query, searchResultsMutationBaseline, SEARCH_TIMEOUT_MS)) {
      if (!await this._ensureWhatsAppReady(page, READY_CHECK_TIMEOUT_MS)) {
        return 'session_unusable';
      }
      return 'chat_not_found';
    }
    if (!await this._clickFirstSearchResultRow(page)) {
      return 'session_unusable';
    }
    if (await this._waitForOpenedChatReady(page, SEARCH_TIMEOUT_MS)) {
      return 'opened';
    }
    if (!await this._ensureWhatsAppReady(page, READY_CHECK_TIMEOUT_MS)) {
      return 'session_unusable';
    }
    return 'session_unusable';
  }

  private async _scrapeTargetOnPage(
    page: PageDriver,
    target: ChatTarget,
  ): Promise<{ status: 'history_scraped' | 'chat_not_found' | 'session_unusable'; messages?: ScrapedHistoryMessage[] }> {
    const opened = await this._openTargetForParse(page, target);
    if (opened !== 'opened') {
      return { status: opened };
    }
    return {
      status: 'history_scraped',
      messages: await this._collectOpenChatHistory(page),
    };
  }

  private async _collectOpenChatHistory(page: PageDriver): Promise<ScrapedHistoryMessage[]> {
    const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, READY_TIMEOUT_MS);
    if (!composeBox) {
      throw new Error('WhatsApp Web chat is not ready. Log in to the browser session first.');
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

    return this._normalizeScrapedMessages([...collected.values()]);
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

}
