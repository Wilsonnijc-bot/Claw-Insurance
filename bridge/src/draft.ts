import {
  type BrowserConnector,
  type BrowserLauncher,
  type BrowserMode,
  type ChatTarget,
  COMPOSE_BOX_SELECTORS,
  DEFAULT_BROWSER_CONNECTOR,
  DEFAULT_BROWSER_LAUNCHER,
  READY_CHECK_TIMEOUT_MS,
  READY_TIMEOUT_MS,
  SEARCH_TIMEOUT_MS,
  WHATSAPP_WEB_URL,
  type PageDriver,
  WhatsAppWebSession,
} from './webAutomation.js';

export type { ChatTarget } from './webAutomation.js';

export interface DraftPrepareResult {
  status: 'draft_prepared' | 'chat_not_found' | 'compose_box_busy' | 'not_ready';
  detail?: string;
}

export const CDP_DRAFT_DISABLED_DETAIL = 'WhatsApp Web draft placement is disabled in CDP mode; CDP is reserved for history parsing.';

type DraftOpenResult = 'opened' | 'chat_not_found' | 'session_unusable';

export class DraftComposer extends WhatsAppWebSession {
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

  async prepareDraft(target: ChatTarget, text: string): Promise<DraftPrepareResult> {
    return this._serialize(async () => this._prepareDraft(target, text));
  }

  private async _prepareDraft(target: ChatTarget, text: string): Promise<DraftPrepareResult> {
    if (this.browserMode === 'cdp') {
      return {
        status: 'not_ready',
        detail: CDP_DRAFT_DISABLED_DETAIL,
      };
    }

    const page = await this._prepareDraftPage();
    if (!await this._ensureWhatsAppReady(page)) {
      return {
        status: 'not_ready',
        detail: 'WhatsApp Web is not ready. Log in to the browser session first.',
      };
    }

    const opened = await this._openDraftTarget(page, target);
    if (opened === 'session_unusable') {
      return {
        status: 'not_ready',
        detail: 'WhatsApp Web chat could not be opened from the current browser session.',
      };
    }
    if (opened !== 'opened') {
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

  private async _prepareDraftPage(): Promise<PageDriver> {
    const page = await this._ensurePage();
    if (page.bringToFront) {
      await page.bringToFront();
    }
    return page;
  }

  private async _openDraftTarget(page: PageDriver, target: ChatTarget): Promise<DraftOpenResult> {
    const phone = String(target.phone || '').trim();
    if (phone) {
      const openedByPhone = await this._openDraftChatByPhone(page, phone);
      if (openedByPhone) {
        return 'opened';
      }
    }
    return this._searchAndOpenDraftTarget(page, target);
  }

  private async _openDraftChatByPhone(page: PageDriver, phone: string): Promise<boolean> {
    await page.goto(
      `${WHATSAPP_WEB_URL}send?phone=${encodeURIComponent(phone)}&app_absent=0`,
      { waitUntil: 'domcontentloaded' },
    );
    const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, SEARCH_TIMEOUT_MS);
    return composeBox !== null;
  }

  private async _searchAndOpenDraftTarget(page: PageDriver, target: ChatTarget): Promise<DraftOpenResult> {
    const searchBoxState = await this._getSearchBoxState(page);
    if (!searchBoxState.found) {
      return 'session_unusable';
    }

    for (const rawTerm of target.searchTerms) {
      const term = String(rawTerm || '').trim();
      if (!term) {
        continue;
      }
      if (!await this._resetSearchBox(page)) {
        return 'session_unusable';
      }
      if (!await this._typeSearchQuery(page, term)) {
        return 'session_unusable';
      }
      if (!await this._waitForMatchingDraftSearchResultRow(page, target, term, SEARCH_TIMEOUT_MS)) {
        continue;
      }
      if (!await this._clickMatchingDraftSearchResultRow(page, target, term)) {
        return 'session_unusable';
      }
      if (await this._waitForDraftOpenedChat(page, target, term, SEARCH_TIMEOUT_MS)) {
        return 'opened';
      }
      return 'session_unusable';
    }

    return 'chat_not_found';
  }

  private async _waitForMatchingDraftSearchResultRow(
    page: PageDriver,
    target: ChatTarget,
    query: string,
    timeoutMs: number,
  ): Promise<boolean> {
    const deadline = Date.now() + Math.max(timeoutMs, READY_CHECK_TIMEOUT_MS);
    while (Date.now() < deadline) {
      if (await this._hasMatchingDraftSearchResultRow(page, target, query)) {
        return true;
      }
      await page.waitForTimeout?.(200);
    }
    return false;
  }

  private async _hasMatchingDraftSearchResultRow(
    page: PageDriver,
    target: ChatTarget,
    query: string,
  ): Promise<boolean> {
    return page.evaluate(({ action, target: currentTarget, query: currentQuery }) => {
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

      const targetPhone = digits(String(currentTarget.phone || ''));
      const queryDigits = digits(String(currentQuery || ''));
      const queryTerm = normalize(String(currentQuery || ''));
      const terms = [queryTerm, ...(Array.isArray(currentTarget.searchTerms) ? currentTarget.searchTerms : [])]
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

  private async _clickMatchingDraftSearchResultRow(
    page: PageDriver,
    target: ChatTarget,
    query: string,
  ): Promise<boolean> {
    return page.evaluate(({ action, target: currentTarget, query: currentQuery }) => {
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

      const targetPhone = digits(String(currentTarget.phone || ''));
      const queryDigits = digits(String(currentQuery || ''));
      const queryTerm = normalize(String(currentQuery || ''));
      const terms = [queryTerm, ...(Array.isArray(currentTarget.searchTerms) ? currentTarget.searchTerms : [])]
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

  private async _waitForDraftOpenedChat(
    page: PageDriver,
    target: ChatTarget,
    matchedTerm: string,
    timeoutMs: number,
  ): Promise<boolean> {
    const deadline = Date.now() + Math.max(timeoutMs, READY_CHECK_TIMEOUT_MS);
    while (Date.now() < deadline) {
      const composeBox = await this._findVisibleLocator(page, COMPOSE_BOX_SELECTORS, READY_CHECK_TIMEOUT_MS);
      if (composeBox && await this._draftOpenedChatMatchesTarget(page, target, matchedTerm)) {
        return true;
      }
      await page.waitForTimeout?.(200);
    }
    return false;
  }

  private async _draftOpenedChatMatchesTarget(
    page: PageDriver,
    target: ChatTarget,
    matchedTerm: string,
  ): Promise<boolean> {
    const snapshot = await this._getOpenedChatSnapshot(page);

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
}
