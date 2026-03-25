#include "SimRadio.h"
#include <stdio.h>
#include <string.h>
#include <algorithm>
#include <cmath>

SimRadio::SimRadio(int sf, int bw_hz, int cr)
    : _sf(sf), _bw_hz(bw_hz), _cr(cr) {}

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
    // Semtech AN1200.13 §4 — LoRa on-air time in milliseconds.
    // Assumes explicit header and CRC enabled (MeshCore defaults).
    double t_sym = (double)(1 << _sf) / (_bw_hz / 1000.0);  // ms
    double t_pre = (8 + 4.25) * t_sym;  // 8 preamble symbols

    int de = (t_sym >= 16.0) ? 1 : 0;  // low data-rate optimisation
    double num = 8.0 * len_bytes - 4.0 * _sf + 44;  // +28 +16(CRC) -0(explicit hdr)
    double den = 4.0 * (_sf - 2 * de);
    int pay_sym = 8 + (int)std::max(std::ceil(num / den) * (_cr + 4), 0.0);

    return (uint32_t)(t_pre + pay_sym * t_sym);
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
