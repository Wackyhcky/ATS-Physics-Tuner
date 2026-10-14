"""Minimal reader for SCS HashFS v2 archives (.scs) used by ATS/ETS2."""
import struct, zlib

M = (1 << 64) - 1
k0 = 0xc3a5c85c97cb3127
k1 = 0xb492b66fbe98f273
k2 = 0x9ae16a3b2f90404f


def _rot(v, s):
    return v if s == 0 else ((v >> s) | (v << (64 - s))) & M


def _smix(v):
    return v ^ (v >> 47)


def _f64(s, i):
    return struct.unpack_from('<Q', s, i)[0]


def _f32(s, i):
    return struct.unpack_from('<I', s, i)[0]


def _h16(u, v, mul=0x9ddfea08eb382d69):
    a = ((u ^ v) * mul) & M
    a ^= a >> 47
    b = ((v ^ a) * mul) & M
    b ^= b >> 47
    return (b * mul) & M


k3 = 0xc949d7c7509e6557


def _len0to16(s, n):  # CityHash64 v1.0.3 (the version SCS uses)
    if n > 8:
        a = _f64(s, 0)
        b = _f64(s, n - 8)
        return _h16(a, _rot((b + n) & M, n)) ^ b
    if n >= 4:
        a = _f32(s, 0)
        return _h16((n + (a << 3)) & M, _f32(s, n - 4))
    if n > 0:
        a, b, c = s[0], s[n >> 1], s[n - 1]
        y = (a + (b << 8)) & 0xffffffff
        z = (n + (c << 2)) & 0xffffffff
        return (_smix(((y * k2) ^ (z * k3)) & M) * k2) & M
    return k2


def _len17to32(s, n):
    a = (_f64(s, 0) * k1) & M
    b = _f64(s, 8)
    c = (_f64(s, n - 8) * k2) & M
    d = (_f64(s, n - 16) * k0) & M
    return _h16((_rot((a - b) & M, 43) + _rot(c, 30) + d) & M,
                (a + _rot(b ^ k3, 20) - c + n) & M)


def _len33to64(s, n):
    z = _f64(s, 24)
    a = (_f64(s, 0) + (n + _f64(s, n - 16)) * k0) & M
    b = _rot((a + z) & M, 52)
    c = _rot(a, 37)
    a = (a + _f64(s, 8)) & M
    c = (c + _rot(a, 7)) & M
    a = (a + _f64(s, 16)) & M
    vf = (a + z) & M
    vs = (b + _rot(a, 31) + c) & M
    a = (_f64(s, 16) + _f64(s, n - 32)) & M
    z = _f64(s, n - 8)
    b = _rot((a + z) & M, 52)
    c = _rot(a, 37)
    a = (a + _f64(s, n - 24)) & M
    c = (c + _rot(a, 7)) & M
    a = (a + _f64(s, n - 16)) & M
    wf = (a + z) & M
    ws = (b + _rot(a, 31) + c) & M
    r = _smix(((vf + ws) * k2 + (wf + vs) * k0) & M)
    return (_smix((r * k0 + vs) & M) * k2) & M


def _weak(s, i, a, b):
    w, x, y, z = _f64(s, i), _f64(s, i + 8), _f64(s, i + 16), _f64(s, i + 24)
    a = (a + w) & M
    b = _rot((b + a + z) & M, 21)
    c = a
    a = (a + x + y) & M
    b = (b + _rot(a, 44)) & M
    return (a + z) & M, (b + c) & M


def cityhash64(s: bytes) -> int:
    n = len(s)
    if n <= 16:
        return _len0to16(s, n)
    if n <= 32:
        return _len17to32(s, n)
    if n <= 64:
        return _len33to64(s, n)
    x = _f64(s, n - 40)
    y = (_f64(s, n - 16) + _f64(s, n - 56)) & M
    z = _h16((_f64(s, n - 48) + n) & M, _f64(s, n - 24))
    v = _weak(s, n - 64, n, z)
    w = _weak(s, n - 32, (y + k1) & M, x)
    x = (x * k1 + _f64(s, 0)) & M
    left = (n - 1) & ~63
    p = 0
    while True:
        x = (_rot((x + y + v[0] + _f64(s, p + 8)) & M, 37) * k1) & M
        y = (_rot((y + v[1] + _f64(s, p + 48)) & M, 42) * k1) & M
        x ^= w[1]
        y = (y + v[0] + _f64(s, p + 40)) & M
        z = (_rot((z + w[0]) & M, 33) * k1) & M
        v = _weak(s, p, (v[1] * k1) & M, (x + w[0]) & M)
        w = _weak(s, p + 32, (z + w[1]) & M, (y + _f64(s, p + 16)) & M)
        z, x = x, z
        p += 64
        left -= 64
        if left == 0:
            break
    return _h16((_h16(v[0], w[0]) + _smix(y) * k1 + z) & M,
                (_h16(v[1], w[1]) + x) & M)


def path_hash(path: str) -> int:
    return cityhash64(path.strip('/').encode('utf-8'))


class HashFS:
    def __init__(self, fn):
        self.fn = fn
        self.f = open(fn, 'rb')
        h = self.f.read(0x34)
        magic, ver, salt, meth, ecount, esize, mcount, msize, estart, mstart = \
            struct.unpack_from('<4sHH4sIIIIQQ', h)
        assert magic == b'SCS#' and ver == 2, (magic, ver)
        self.f.seek(estart)
        et = zlib.decompress(self.f.read(esize))
        self.f.seek(mstart)
        mt = zlib.decompress(self.f.read(msize))
        self.entries = {}
        for i in range(ecount):
            hsh, midx, mcnt, flags = struct.unpack_from('<QIHH', et, i * 16)
            info = None
            for j in range(mcnt):
                it = _f32(mt, (midx + j) * 4)
                idx, typ = it & 0xffffff, it >> 24
                if typ in (0x80, 0x81):  # plain / directory
                    cs, sz, _unk, off = struct.unpack_from('<IIII', mt, idx * 4)
                    info = dict(csize=cs & 0x0fffffff, comp=cs >> 28,
                                size=sz & 0x0fffffff, off=off * 16,
                                is_dir=bool(flags & 1))
            if info:
                self.entries[hsh] = info

    def _raw(self, e):
        self.f.seek(e['off'])
        d = self.f.read(e['csize'])
        if e['comp']:
            try:
                d = zlib.decompress(d)
            except zlib.error:
                d = zlib.decompress(d, -15)
        return d

    def read(self, path):
        e = self.entries.get(path_hash(path))
        return None if e is None else self._raw(e)

    def listdir(self, path):
        e = self.entries.get(path_hash(path))
        if not e or not e['is_dir']:
            return None
        d = self._raw(e)
        n = _f32(d, 0)
        lens = d[4:4 + n]
        p = 4 + n
        out = []
        for L in lens:
            out.append(d[p:p + L].decode('utf-8', 'replace'))
            p += L
        return out

    def walk(self, path=''):
        items = self.listdir(path) or []
        for it in items:
            if it.startswith('/'):
                sub = (path + '/' + it[1:]).strip('/')
                yield sub, True
                yield from self.walk(sub)
            else:
                yield (path + '/' + it).strip('/'), False
