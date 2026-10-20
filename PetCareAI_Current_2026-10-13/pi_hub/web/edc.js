// EDC + HMAC-SHA256 for the phone app, written out in plain JavaScript so it also works over plain HTTP
// (the browser's built-in crypto only exists on HTTPS pages). Mirrors EdcCodec in pi_hub.py exactly.
"use strict";
(function () {
  // ---------- SHA-256 / HMAC / PBKDF2 ----------
  const K = new Int32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  const IV = new Int32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const W = new Int32Array(64);

  function compress(H, block) {  // H: Int32Array(8), updated in place; block: 16 big-endian words in W[0..15]
    for (let i = 0; i < 16; i++) W[i] = block[i];
    for (let i = 16; i < 64; i++) {
      const a = W[i - 15], b = W[i - 2];
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
      W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
    }
    let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const t1 = (h + S1 + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
    H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
  }

  function sha256(bytes, state = IV, already = 0) {  // `already` = bytes absorbed into `state` before this call
    const H = new Int32Array(state), total = already + bytes.length;
    const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6);
    padded.set(bytes); padded[bytes.length] = 0x80;
    const bits = total * 8, view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(bits / 0x100000000)); view.setUint32(padded.length - 4, bits >>> 0);
    const block = new Int32Array(16);
    for (let off = 0; off < padded.length; off += 64) {
      for (let i = 0; i < 16; i++) block[i] = view.getInt32(off + i * 4);
      compress(H, block);
    }
    const out = new Uint8Array(32), ov = new DataView(out.buffer);
    for (let i = 0; i < 8; i++) ov.setInt32(i * 4, H[i]);
    return out;
  }

  function hmacStates(key) {
    if (key.length > 64) key = sha256(key);
    const pads = [0x36, 0x5c].map((x) => {
      const block = new Uint8Array(64); block.set(key);
      for (let i = 0; i < 64; i++) block[i] ^= x;
      const H = new Int32Array(IV), view = new DataView(block.buffer), words = new Int32Array(16);
      for (let i = 0; i < 16; i++) words[i] = view.getInt32(i * 4);
      compress(H, words);
      return H;
    });
    return {inner: pads[0], outer: pads[1]};
  }
  function hmac(key, message) {
    const st = hmacStates(key);
    return sha256(sha256(message, st.inner, 64), st.outer, 64);
  }
  function pbkdf2(password, salt, iterations) {  // 32-byte output, the same as hashlib.pbkdf2_hmac("sha256", ...)
    const st = hmacStates(password);
    const first = new Uint8Array(salt.length + 4); first.set(salt); first[first.length - 1] = 1;
    let u = hmac(password, first);
    const out = new Int32Array(8), uw = new Int32Array(8), block = new Int32Array(16);
    const v = new DataView(u.buffer);
    for (let i = 0; i < 8; i++) { uw[i] = v.getInt32(i * 4); out[i] = uw[i]; }
    // Each further round hashes exactly 32 bytes after a 64-byte pad: one block, fixed padding.
    block[8] = 0x80000000 | 0; for (let i = 9; i < 15; i++) block[i] = 0; block[15] = (64 + 32) * 8;
    const H = new Int32Array(8);
    for (let n = 1; n < iterations; n++) {
      for (let i = 0; i < 8; i++) block[i] = uw[i];
      H.set(st.inner); compress(H, block);
      for (let i = 0; i < 8; i++) block[i] = H[i];
      H.set(st.outer); compress(H, block);
      for (let i = 0; i < 8; i++) { uw[i] = H[i]; out[i] ^= H[i]; }
    }
    const result = new Uint8Array(32), rv = new DataView(result.buffer);
    for (let i = 0; i < 8; i++) rv.setInt32(i * 4, out[i]);
    return result;
  }

  // ---------- encoding helpers ----------
  const utf8 = (text) => new TextEncoder().encode(text);
  const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  const unhex = (text) => new Uint8Array(text.match(/../g).map((h) => parseInt(h, 16)));
  const random = (n) => crypto.getRandomValues(new Uint8Array(n));
  const concat = (a, b) => { const c = new Uint8Array(a.length + b.length); c.set(a); c.set(b, a.length); return c; };
  function sameText(a, b) {  // constant time for equal lengths
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return diff === 0;
  }
  const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  function base32(bytes) {
    let out = "", bits = 0, value = 0;
    for (const byte of bytes) {
      value = ((value << 8) | byte) & 0xffff; bits += 8;
      while (bits >= 5) { out += B32[(value >>> (bits - 5)) & 31]; bits -= 5; }
    }
    if (bits) out += B32[(value << (5 - bits)) & 31];
    return out;
  }
  function unbase32(text) {
    const out = new Uint8Array(Math.floor(text.length * 5 / 8));
    let bits = 0, value = 0, n = 0;
    for (const ch of text) {
      const v = B32.indexOf(ch);
      if (v < 0) throw new Error("Invalid Base32");
      value = ((value << 5) | v) & 0xffff; bits += 5;
      if (bits >= 8) { out[n++] = (value >>> (bits - 8)) & 255; bits -= 8; }
    }
    return out;
  }

  // ---------- the EDC cipher (same transformations as the Pi) ----------
  const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", ROWS = ["ECE", "FIT", "TRC", "MEC", "CHE", "CIV"];
  function matrix(key) {
    const alphabet = [...new Set(key + ALPHABET)].join("");
    const coords = {};
    for (let i = 0; i < 36; i++) coords[alphabet[i]] = [Math.floor(i / 6), i % 6];
    return {alphabet, coords};
  }
  function permutation(key, block, length) {
    const scores = [];
    for (let p = 0; p < length; p++) scores.push((ALPHABET.indexOf(key[(block * 4 + p) % key.length]) + 11 * p + 7 * block) % 97);
    return [...Array(length).keys()].sort((a, b) => scores[a] - scores[b] || a - b);
  }
  const feedback = (key) => { const t = [...key].reduce((s, c) => s + ALPHABET.indexOf(c), 0) % 36; return [Math.floor(t / 6), t % 6]; };
  const mod6 = (n) => ((n % 6) + 6) % 6;
  function edcEncrypt(message, key) {
    const {coords} = matrix(key);
    let [rowPrev, colPrev] = feedback(key);
    const tokens = [];
    for (let i = 0; i < message.length; i++) {
      const [row, col] = coords[message[i]], k = ALPHABET.indexOf(key[i % key.length]);
      rowPrev = mod6(row + Math.floor(k / 6) + i + rowPrev);
      colPrev = mod6(col + (k % 6) + 2 * i + colPrev);
      tokens.push(ROWS[rowPrev] + (colPrev + 1));
    }
    const out = [];
    for (let s = 0; s < tokens.length; s += 4) {
      const block = tokens.slice(s, s + 4);
      for (const p of permutation(key, s / 4, block.length)) out.push(block[p]);
    }
    return out.join("-");
  }
  function edcDecrypt(ciphertext, key) {
    const sent = ciphertext.split("-"), tokens = [];
    for (let s = 0; s < sent.length; s += 4) {
      const block = sent.slice(s, s + 4), restored = new Array(block.length);
      permutation(key, s / 4, block.length).forEach((original, position) => { restored[original] = block[position]; });
      tokens.push(...restored);
    }
    const {alphabet} = matrix(key);
    let [rowPrev, colPrev] = feedback(key), out = "";
    tokens.forEach((token, i) => {
      const r = ROWS.indexOf(token.slice(0, 3)), c = +token[3] - 1;
      if (token.length !== 4 || r < 0 || !(c >= 0 && c < 6)) throw new Error("Invalid EDC token");
      const k = ALPHABET.indexOf(key[i % key.length]);
      out += alphabet[mod6(r - Math.floor(k / 6) - i - rowPrev) * 6 + mod6(c - (k % 6) - 2 * i - colPrev)];
      rowPrev = r; colPrev = c;
    });
    return out;
  }

  // ---------- a session channel: encrypt-then-HMAC, verify-then-decrypt ----------
  const AUTH_CONTEXT = utf8("EDC-HMAC-v1\u0000");
  class Channel {
    constructor(edcKey, macKey) {
      this.edcKey = edcKey; this.macKey = macKey; this.seen = new Map();
      this.stats = {sent: 0, verified: 0, rejected: 0, last: null};
    }
    tag(ciphertext) { return hex(hmac(this.macKey, concat(AUTH_CONTEXT, utf8(ciphertext)))); }
    encode(message) {
      // Nonce first: EDC has no IV, but its ciphertext feedback carries this random start through every later token.
      const inner = {nonce: hex(random(16)).toUpperCase(), v: 1, sent_at: Math.floor(Date.now() / 1000), message};
      const plaintext = base32(utf8(JSON.stringify(inner)));
      const ciphertext = edcEncrypt(plaintext, this.edcKey);
      this.stats.sent++;
      return {type: "EDC_PACKET", v: 1, ciphertext, tag: this.tag(ciphertext)};
    }
    decode(packet) {
      try {
        if (!packet || packet.type !== "EDC_PACKET" || packet.v !== 1 || typeof packet.ciphertext !== "string") throw new Error("Not an EDC packet");
        if (!/^[0-9a-f]{64}$/.test(packet.tag) || !sameText(this.tag(packet.ciphertext), packet.tag)) throw new Error("HMAC verification failed");
        const inner = JSON.parse(new TextDecoder().decode(unbase32(edcDecrypt(packet.ciphertext, this.edcKey))));
        if (inner.v !== 1 || Math.abs(Date.now() / 1000 - inner.sent_at) > 120) throw new Error("Expired packet: check the phone's clock");
        const now = Date.now();
        for (const [n, t] of this.seen) if (now - t > 300000) this.seen.delete(n);
        if (this.seen.has(inner.nonce)) throw new Error("Replay rejected");
        this.seen.set(inner.nonce, now);
        this.stats.verified++;
        return inner.message;
      } catch (e) { this.stats.rejected++; throw e; }
    }
    save() { return JSON.stringify({edcKey: this.edcKey, macKey: hex(this.macKey)}); }
    static load(text) { const o = JSON.parse(text); return new Channel(o.edcKey, unhex(o.macKey)); }
  }

  // ---------- login: prove the password without sending it, then derive the session keys ----------
  async function login(username, password, onStep = () => {}) {
    const challenge = await fetch(`/api/login/challenge?username=${encodeURIComponent(username)}`, {credentials: "same-origin"});
    const c = await challenge.json();
    if (!challenge.ok) throw new Error(c.error || "Sign-in unavailable");
    onStep("Deriving your key on this phone…");
    await new Promise((r) => setTimeout(r, 30));  // let the message paint before the heavy loop
    const hash = pbkdf2(utf8(password), unhex(c.salt), c.iterations);
    const clientNonce = hex(random(16));
    const proof = hex(hmac(hash, utf8(`login|${c.nonce}|${clientNonce}`)));
    onStep("Verifying…");
    const r = await fetch("/api/login", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-PetCare": "1"},
      body: JSON.stringify({username, nonce: c.nonce, client_nonce: clientNonce, proof})});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "Sign-in failed");
    if (!sameText(hex(hmac(hash, utf8(`server|${c.nonce}|${clientNonce}`))), data.server_proof || "")) throw new Error("This hub could not prove it knows your password");
    const master = hmac(hash, utf8(`session|${c.nonce}|${clientNonce}`));
    return new Channel(hex(hmac(master, utf8("edc"))).toUpperCase().slice(0, 32), hmac(master, utf8("mac")));
  }

  window.EDC = {Channel, login, edcEncrypt, edcDecrypt, sha256, hmac, pbkdf2, hex, utf8};
})();
