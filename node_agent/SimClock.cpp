#include "SimClock.h"
#include <ctime>

SimClock::SimClock()
    : _start(Clock::now()),
      _epoch_base((uint32_t)std::time(nullptr))
{}

unsigned long SimClock::getMillis() {
    auto elapsed = Clock::now() - _start;
    return (unsigned long)
        std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
}

uint32_t SimClock::getCurrentTime() {
    // Epoch = base epoch + elapsed seconds since SimClock was constructed.
    auto elapsed = Clock::now() - _start;
    uint32_t secs = (uint32_t)
        std::chrono::duration_cast<std::chrono::seconds>(elapsed).count();
    return _epoch_base + secs;
}

void SimClock::setCurrentTime(uint32_t epoch) {
    // Recalibrate: store the desired epoch, adjusted for elapsed time so that
    // future calls to getCurrentTime() are consistent.
    auto elapsed = Clock::now() - _start;
    uint32_t secs_elapsed = (uint32_t)
        std::chrono::duration_cast<std::chrono::seconds>(elapsed).count();
    _epoch_base = epoch - secs_elapsed;
}
