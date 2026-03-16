#pragma once
#include <Dispatcher.h>
#include <queue>
#include <vector>
#include <cstdint>

// A single raw packet sitting in the inbound queue.
struct IncomingPacket {
    std::vector<uint8_t> data;
    float snr;
    float rssi;
};

// Simulator Radio implementation.
//
// recvRaw()      – pops one packet from the inbound queue populated by enqueue().
// startSendRaw() – writes a JSON "tx" line to stdout and returns immediately
//                  (the orchestrator decides if/when neighbours receive it).
// isSendComplete()– returns true on the very next call after startSendRaw(),
//                  modelling a zero-latency (from the node's perspective) transmit.
class SimRadio : public mesh::Radio {
    std::queue<IncomingPacket> _rx_queue;
    float _last_snr   = 0.0f;
    float _last_rssi  = -100.0f;
    bool  _tx_pending = false;   // set by startSendRaw, cleared by isSendComplete

public:
    // Called by main.cpp to deliver a packet from the orchestrator.
    void enqueue(const uint8_t* data, int len, float snr, float rssi);

    // ---- mesh::Radio interface ----
    int      recvRaw(uint8_t* bytes, int sz)         override;
    uint32_t getEstAirtimeFor(int len_bytes)          override;
    float    packetScore(float snr, int packet_len)   override;
    bool     startSendRaw(const uint8_t* bytes, int len) override;
    bool     isSendComplete()                         override;
    void     onSendFinished()                         override {}
    bool     isInRecvMode() const                     override { return !_tx_pending; }
    bool     isReceiving()                            override { return false; }
    float    getLastSNR()  const                      override { return _last_snr;  }
    float    getLastRSSI() const                      override { return _last_rssi; }
};
