#pragma once
// Minimal Arduino Stream/Print stub for POSIX compilation.
// Only used by Identity::readFrom/writeTo/printTo (key serialisation during init).

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>

class Print {
public:
    virtual size_t write(uint8_t c) = 0;
    virtual size_t write(const uint8_t* buf, size_t n) {
        size_t written = 0;
        while (n--) written += write(*buf++);
        return written;
    }
    size_t print(const char* s)  { return write((const uint8_t*)s, strlen(s)); }
    size_t print(char c)         { return write((uint8_t)c); }
    size_t println()             { return write((uint8_t)'\n'); }
    size_t println(const char* s){ size_t n = print(s); return n + println(); }
    virtual ~Print() = default;
};

class Stream : public Print {
public:
    virtual int read() = 0;
    virtual size_t readBytes(uint8_t* buf, size_t n) {
        size_t count = 0;
        while (count < n) {
            int c = read();
            if (c < 0) break;
            buf[count++] = (uint8_t)c;
        }
        return count;
    }
};

// Buffer-backed stream: read from a const byte slice, write into a growable buffer.
class BufferStream : public Stream {
    const uint8_t* _rbuf;
    size_t         _rlen, _rpos;
    uint8_t*       _wbuf;
    size_t         _wmax, _wpos;
public:
    BufferStream(const uint8_t* rbuf, size_t rlen, uint8_t* wbuf, size_t wmax)
        : _rbuf(rbuf), _rlen(rlen), _rpos(0),
          _wbuf(wbuf), _wmax(wmax), _wpos(0) {}

    int    read()              override { return _rpos < _rlen ? _rbuf[_rpos++] : -1; }
    size_t write(uint8_t c)   override { if (_wpos < _wmax) { _wbuf[_wpos++] = c; return 1; } return 0; }
    size_t bytesWritten() const { return _wpos; }
};
