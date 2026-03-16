#pragma once
#include <Utils.h>

// Cryptographically secure RNG backed by /dev/urandom.
class SimRNG : public mesh::RNG {
public:
    void random(uint8_t* dest, size_t sz) override;
};
