import {
  type BrowserConnector,
  type BrowserLauncher,
  type BrowserMode,
  type ChatTarget,
  COMPOSE_BOX_SELECTORS,
  DEFAULT_BROWSER_CONNECTOR,
  DEFAULT_BROWSER_LAUNCHER,
  READY_TIMEOUT_MS,
  type PageDriver,
  WhatsAppWebSession,
} from './webAutomation.js';

export type { ChatTarget } from './webAutomation.js';

export interface DraftPrepareResult {
  status: 'draft_prepared' | 'chat_not_found' | 'compose_box_busy' | 'not_ready';
  detail?: string;
}

export const CDP_DRAFT_DISABLED_DETAIL = 'WhatsApp Web draft placement is disabled in CDP mode; CDP is reserved for history parsing.';

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

  private async _prepareDraftPage(): Promise<PageDriver> {
    const page = await this._ensurePage();
    if (page.bringToFront) {
      await page.bringToFront();
    }
    return page;
  }
}
