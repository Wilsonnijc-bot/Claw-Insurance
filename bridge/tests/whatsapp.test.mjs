import test from 'node:test';
import assert from 'node:assert/strict';

import { extractPhoneFromJid, resolvePhoneIdentifier } from '../dist/whatsapp.js';

test('extractPhoneFromJid returns phone digits for legacy direct chat JIDs', () => {
  assert.equal(extractPhoneFromJid('85212345678@s.whatsapp.net'), '85212345678');
  assert.equal(extractPhoneFromJid('+85212345678@c.us'), '+85212345678');
  assert.equal(extractPhoneFromJid('1203630@g.us'), '');
  assert.equal(extractPhoneFromJid('user@lid'), '');
});

test('resolvePhoneIdentifier prefers pn and falls back to phone-like sender JID', () => {
  assert.equal(resolvePhoneIdentifier('+852 1234 5678', 'ignored@lid'), '+85212345678');
  assert.equal(resolvePhoneIdentifier('', '85212345678@s.whatsapp.net'), '85212345678');
  assert.equal(resolvePhoneIdentifier('', 'user@lid'), '');
});
