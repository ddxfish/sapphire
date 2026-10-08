// shared/md5.js - MD5 of a byte array, as hex. RFC 1321, nothing else.
//
// The browser has no MD5 (SubtleCrypto starts at SHA-1), and an ESP32's
// bootloader answers a flash write with the MD5 of what landed: esptool-js
// compares it with this (device-flash.js, calculateMD5Hash). Not for
// anything security-shaped.

const K = new Int32Array(64), S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296) | 0;

export function md5(bytes) {
    const n = bytes.length;
    const total = ((n + 8) >>> 6) + 1;                    // 64-byte blocks, with room for 0x80 and the length
    const buf = new Uint8Array(total * 64);
    buf.set(bytes);
    buf[n] = 0x80;
    const bits = n * 8;
    const v = new DataView(buf.buffer);
    v.setUint32(buf.length - 8, bits >>> 0, true);
    v.setUint32(buf.length - 4, Math.floor(bits / 4294967296), true);
    let a0 = 0x67452301, b0 = 0xefcdab89 | 0, c0 = 0x98badcfe | 0, d0 = 0x10325476;
    const M = new Int32Array(16);
    for (let off = 0; off < buf.length; off += 64) {
        for (let i = 0; i < 16; i++) M[i] = v.getInt32(off + i * 4, true);
        let a = a0, b = b0, c = c0, d = d0;
        for (let i = 0; i < 64; i++) {
            let f, g;
            if (i < 16) { f = (b & c) | (~b & d); g = i; }
            else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) & 15; }
            else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) & 15; }
            else { f = c ^ (b | ~d); g = (7 * i) & 15; }
            const s = S[(i >> 4) * 4 + (i & 3)];
            const t = (a + f + K[i] + M[g]) | 0;
            a = d; d = c; c = b;
            b = (b + ((t << s) | (t >>> (32 - s)))) | 0;
        }
        a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0;
    }
    const out = new DataView(new ArrayBuffer(16));
    out.setInt32(0, a0, true); out.setInt32(4, b0, true); out.setInt32(8, c0, true); out.setInt32(12, d0, true);
    return [...new Uint8Array(out.buffer)].map(x => x.toString(16).padStart(2, '0')).join('');
}
