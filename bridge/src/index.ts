#!/usr/bin/env node
/**
 * nanobot WhatsApp Bridge
 * 
 * This bridge connects WhatsApp Web to nanobot's Python backend
 * via WebSocket. It handles authentication, message forwarding,
 * and reconnection logic.
 * 
 * Usage:
 *   npm run build && npm start
 *   
 * Or with custom settings:
 *   BRIDGE_PORT=3001 AUTH_DIR=$PWD/whatsapp-auth WEB_BROWSER_MODE=cdp WEB_CDP_URL=http://127.0.0.1:9222 npm start
 */

// Polyfill crypto for Baileys in ESM
import { webcrypto } from 'crypto';
if (!globalThis.crypto) {
  (globalThis as any).crypto = webcrypto;
}

import { BridgeServer } from './server.js';
import { join } from 'path';

const PORT = parseInt(process.env.BRIDGE_PORT || '3001', 10);
const AUTH_DIR = process.env.AUTH_DIR || join(process.cwd(), 'whatsapp-auth');
const WEB_BROWSER_MODE = (process.env.WEB_BROWSER_MODE || 'cdp') as 'cdp' | 'launch';
const WEB_CDP_URL = process.env.WEB_CDP_URL || 'http://127.0.0.1:9222';
const WEB_CDP_CHROME_PATH = process.env.WEB_CDP_CHROME_PATH || '';
const WEB_PROFILE_DIR = process.env.WEB_PROFILE_DIR || join(process.cwd(), 'whatsapp-web');
const TOKEN = process.env.BRIDGE_TOKEN || undefined;

console.log('🐈 nanobot WhatsApp Bridge');
console.log('========================\n');

console.log(`🌐 Web automation mode: ${WEB_BROWSER_MODE}`);
if (WEB_BROWSER_MODE === 'cdp') {
  console.log(`🔌 CDP endpoint: ${WEB_CDP_URL}`);
  if (WEB_CDP_CHROME_PATH) {
    console.log(`🌐 CDP Chrome path: ${WEB_CDP_CHROME_PATH}`);
  }
} else {
  console.log(`🗂️ Playwright profile: ${WEB_PROFILE_DIR}`);
}

const server = new BridgeServer(
  PORT,
  AUTH_DIR,
  WEB_PROFILE_DIR,
  TOKEN,
  WEB_BROWSER_MODE,
  WEB_CDP_URL,
  WEB_CDP_CHROME_PATH,
);

// Handle graceful shutdown
process.on('SIGINT', async () => {
  console.log('\n\nShutting down...');
  await server.stop();
  process.exit(0);
});

process.on('SIGTERM', async () => {
  await server.stop();
  process.exit(0);
});

// Start the server
server.start().catch((error) => {
  console.error('Failed to start bridge:', error);
  process.exit(1);
});
