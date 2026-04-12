import test from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_CDP_HELPER_CLIENT } from '../dist/webAutomation.js';

test('DEFAULT_CDP_HELPER_CLIENT sends bearer token when provided', async () => {
  const calls = [];
  const originalFetch = global.fetch;

  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async text() {
        return JSON.stringify({
          status: 'launched',
          detail: 'Chrome window opened.',
          endpointUrl: 'http://127.0.0.1:9222',
        });
      },
    };
  };

  try {
    const result = await DEFAULT_CDP_HELPER_CLIENT.ensureBrowser({
      helperUrl: 'http://127.0.0.1:9230',
      endpointUrl: 'http://127.0.0.1:9222',
      profileDir: '/tmp/wa-profile',
      startUrl: 'https://web.whatsapp.com/',
      helperToken: 'secret-token',
    });

    assert.equal(result.status, 'launched');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].options.headers.Authorization, 'Bearer secret-token');
  } finally {
    global.fetch = originalFetch;
  }
});
