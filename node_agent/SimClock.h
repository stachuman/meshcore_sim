#pragma once
// Provides both mesh::MillisecondClock and mesh::RTCClock backed by the host
// wall clock.  The Python orchestrator can advance the epoch offset via the
// {"type":"time","epoch":N} message (handled in main.cpp).

#include <Dispatcher.h>   // MillisecondClock
#include <MeshCore.h>     // RTCClock
#include <chrono>

class SimClock : public mesh::MillisecondClock, public mesh::RTCClock {
    using Clock    = std::chrono::steady_clock;
    using TimePoint = Clock::time_point;

    TimePoint _start;
    uint32_t  _epoch_base;  // Unix epoch at startup (overrideable)

public:
    SimClock();

    // mesh::MillisecondClock
    unsigned long getMillis() override;

    // mesh::RTCClock
    uint32_t getCurrentTime() override;
    void     setCurrentTime(uint32_t epoch) override;
};
