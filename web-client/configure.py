"""Patch the complete pinned Web A checkout; never fall back to Telegram DCs."""
import base64
import hashlib
import os
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
host = "risegram-risegram.up.railway.app"
pem = """MIIBCgKCAQEAmzg4fDC+4pvNzDaCaVUnFftTg5YdY9OLuZCq8a1GjvKhSaiXsxrv
CM/tbpqdE8KLixEtL6VzmVLNbcGic8tcdj1QKPUAoHyCdlqU6dh8diI95OIQBJ7F
v2ZUC3NPXrIxD1S+XvpPTnMyxxuL3IwDIWtcVdeNgKqtEpcX+Ba+PRo8QEnRSpu9
HT5k4vQqyAK/E7ZXbHFa9RPTcAknnqBmIlbEYC0P6cVtcqV/l3ZvQPc9ErtIpGzn
Zr86GptV70AmzujXwNJxajrHcBC1f8fAV+Vyu8JUhy0uWoRgDXC1kmoswmwD+0iN
N8I20e+JfEeRKNWOxZck0aawO2RpHcO+PQIDAQAB"""
der = base64.b64decode(pem)
def tlv(buf, pos, tag):
    assert buf[pos] == tag
    n = buf[pos+1]
    pos += 2
    if n & 128:
        k = n & 127
        n = int.from_bytes(buf[pos:pos+k], "big")
        pos += k
    return buf[pos:pos+n], pos+n
seq, end = tlv(der, 0, 48)
assert end == len(der)
n, pos = tlv(seq, 0, 2)
e, pos = tlv(seq, pos, 2)
n = n.lstrip(b"\0")
e = e.lstrip(b"\0")
def tl_bytes(b):
    h = bytes([len(b)]) if len(b) < 254 else bytes([254]) + len(b).to_bytes(3, "little")
    return h + b + bytes((-(len(h)+len(b))) % 4)
fp = int.from_bytes(hashlib.sha1(tl_bytes(n)+tl_bytes(e)).digest()[-8:], "little")
assert fp == 0x41684b3ac49ce891, hex(fp)
def edit(path, transform):
    p = root / path
    old = p.read_text()
    new = transform(old)
    assert old != new, path
    p.write_text(new)
def once(s, a, b):
    assert s.count(a) == 1, a
    return s.replace(a, b, 1)
def rsa(s):
    start = s.index("export const SERVER_KEYS = [")
    end = s.index("].reduce(", start)
    return s[:start] + "export const SERVER_KEYS = [\n  { fingerprint: BigInt('" + str(fp) + "'), n: BigInt('" + str(int.from_bytes(n, "big")) + "'), e: " + str(int.from_bytes(e,"big")) + " },\n" + s[end:]
edit("src/lib/gramjs/crypto/RSA.ts", rsa)
def dc(s):
    start = s.index("function getDC(")
    end = s.index("\n}", start)
    return s[:start] + "function getDC(dcId: number, downloadDC = false) {\n  void downloadDC;\n  if (dcId !== 2) throw new Error('RiseGram supports only DC2');\n  return { id: 2, ipAddress: '" + host + "', port: 443 };\n" + s[end:]
edit("src/lib/gramjs/Utils.ts", dc)
def ws(s):
    start = s.index("    if (port === 443) {", s.index("getWebSocketLink("))
    end = s.index("\n  }", start)
    return s[:start] + "    void ip; void port; void isTestServer; void isPremium;\n    return 'wss://" + host + "/apiws';" + s[end:]
edit("src/lib/gramjs/extensions/PromisedWebSockets.ts", ws)
edit("vite.config.ts", lambda s: once(s, "connect-src 'self' wss://*.web.telegram.org blob: http: https: ${appEnv === 'development' ? 'ws: wss: ipc:' : ''};", "connect-src 'self' wss://" + host + " https://" + host + " blob:;"))
print("RiseGram pinned DC2, RSA fingerprint and WSS route configured")
