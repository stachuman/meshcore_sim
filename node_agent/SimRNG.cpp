#include "SimRNG.h"
#include <stdexcept>
#include <cstdio>

void SimRNG::random(uint8_t* dest, size_t sz) {
    FILE* f = fopen("/dev/urandom", "rb");
    if (!f) throw std::runtime_error("cannot open /dev/urandom");
    if (fread(dest, 1, sz, f) != sz) {
        fclose(f);
        throw std::runtime_error("short read from /dev/urandom");
    }
    fclose(f);
}
