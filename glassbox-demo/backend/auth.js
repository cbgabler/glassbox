// glassbox-demo: backend/auth.js
//
// Intentionally vulnerable auth helper. Two side-channel patterns:
//   - verifyToken: byte-by-byte === on the HMAC tag (timing leak)
//   - checkPassword: plain === on the password (timing leak)
//
// Both should produce CRITICAL side-channel findings.

const crypto = require('crypto');

function verifyToken(provided, expected) {
  if (provided.length !== expected.length) return false;
  for (let i = 0; i < provided.length; i++) {
    if (provided[i] !== expected[i]) {
      return false;
    }
  }
  return true;
}

function checkPassword(stored, attempted) {
  return stored === attempted;
}

function hmac(secret, data) {
  return crypto.createHmac('sha256', secret).update(data).digest('hex');
}

module.exports = { verifyToken, checkPassword, hmac };
