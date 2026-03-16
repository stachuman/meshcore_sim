#include "SimRadio.h"
#include <stdio.h>
#include <string.h>
#include <algorithm>

// --- hex helpers (local, no external deps) ---
static const char HEX[] = "0123456789abcdef";
static void bytes_to_hex(char* out, const uint8_t* in, int len) {
    for (int i = 0; i < len; i++) {
        out[i*2]     = HEX[in[i] >> 4];
        out[i*2 + 1] = HEX[in[i] & 0x0F];
    }
    out[len*2] = '\0';
}

void SimRadio::enqueue(const uint8_t* data, int len, float snr, float rssi) {
    IncomingPacket pkt;
    pkt.data.assign(data, data + len);
    pkt.snr  = snr;
    pkt.rssi = rssi;
    _rx_queue.push(std::move(pkt));
}

int SimRadio::recvRaw(uint8_t* bytes, int sz) {
    if (_rx_queue.empty()) return 0;
    IncomingPacket& front = _rx_queue.front();
    int len = (int)std::min((size_t)sz, front.data.size());
    memcpy(bytes, front.data.data(), len);
    _last_snr  = front.snr;
    _last_rssi = front.rssi;
    _rx_queue.pop();
    return len;
}

uint32_t SimRadio::getEstAirtimeFor(int len_bytes) {
    // LoRa SF7 BW125 approximation: ~8 bytes/ms at the air.
    return (uint32_t)(len_bytes * 1000 / 8);
}

float SimRadio::packetScore(float snr, int /*packet_len*/) {
    // Map SNR to a 0..1 score used by Dispatcher to decide retransmit delay.
    // Good SNR (≥ 10 dB) → 1.0; marginal (≤ -5 dB) → 0.0.
    float clamped = snr < -5.0f ? -5.0f : (snr > 10.0f ? 10.0f : snr);
    return (clamped + 5.0f) / 15.0f;
}

bool SimRadio::startSendRaw(const uint8_t* bytes, int len) {
    // Emit a JSON "tx" line to stdout for the orchestrator to route.
    // Format: {"type":"tx","hex":"<hex-encoded packet>"}
    static char hex_buf[MAX_TRANS_UNIT * 2 + 1];
    bytes_to_hex(hex_buf, bytes, len);
    fprintf(stdout, "{\"type\":\"tx\",\"hex\":\"%s\"}\n", hex_buf);
    fflush(stdout);
    _tx_pending = true;
    return true;
}

bool SimRadio::isSendComplete() {
    if (_tx_pending) {
        _tx_pending = false;
        return true;
    }
    return false;
}
