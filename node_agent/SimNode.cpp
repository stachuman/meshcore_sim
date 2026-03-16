#include "SimNode.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <algorithm>

// ---------------------------------------------------------------------------
// Hex helpers
// ---------------------------------------------------------------------------
static const char HEX_UC[] = "0123456789ABCDEF";

static void bytes_to_hex(char* out, const uint8_t* in, size_t len) {
    for (size_t i = 0; i < len; i++) {
        out[i*2]     = HEX_UC[in[i] >> 4];
        out[i*2 + 1] = HEX_UC[in[i] & 0x0F];
    }
    out[len*2] = '\0';
}

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------
SimNode::SimNode(mesh::Radio& radio, mesh::MillisecondClock& ms,
                 mesh::RNG& rng, mesh::RTCClock& rtc,
                 mesh::PacketManager& mgr, mesh::MeshTables& tables,
                 bool is_relay)
    : mesh::Mesh(radio, ms, rng, rtc, mgr, tables), _is_relay(is_relay)
{}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------
void SimNode::emitLog(const char* fmt, ...) const {
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    fprintf(stdout, "{\"type\":\"log\",\"msg\":\"%s\"}\n", msg);
    fflush(stdout);
}

void SimNode::emitJson(const char* json) const {
    fprintf(stdout, "%s\n", json);
    fflush(stdout);
}

// ---------------------------------------------------------------------------
// Routing overrides
// ---------------------------------------------------------------------------
bool SimNode::allowPacketForward(const mesh::Packet* /*packet*/) {
    return _is_relay;
}

int SimNode::searchPeersByHash(const uint8_t* hash) {
    _search_results.clear();
    for (int i = 0; i < (int)_contacts.size(); i++) {
        if (_contacts[i].id.isHashMatch(hash)) {
            _search_results.push_back(i);
        }
    }
    return (int)_search_results.size();
}

void SimNode::getPeerSharedSecret(uint8_t* dest_secret, int peer_idx) {
    if (peer_idx < 0 || peer_idx >= (int)_search_results.size()) return;
    int idx = _search_results[peer_idx];
    memcpy(dest_secret, _contacts[idx].shared_secret, PUB_KEY_SIZE);
}

// ---------------------------------------------------------------------------
// Event callbacks
// ---------------------------------------------------------------------------
void SimNode::onPeerDataRecv(mesh::Packet* /*packet*/, uint8_t type,
                              int sender_idx, const uint8_t* /*secret*/,
                              uint8_t* data, size_t len) {
    if (sender_idx < 0 || sender_idx >= (int)_search_results.size()) return;
    int idx = _search_results[sender_idx];
    const Contact& c = _contacts[idx];

    char pub_hex[PUB_KEY_SIZE * 2 + 1];
    bytes_to_hex(pub_hex, c.id.pub_key, PUB_KEY_SIZE);

    if (type == PAYLOAD_TYPE_TXT_MSG && len > 4) {
        // MeshCore text payload: 4-byte timestamp prefix, then UTF-8 text.
        const char* text = (const char*)(data + 4);
        size_t text_len  = len - 4;
        // Escape any quotes in the text before embedding in JSON.
        char escaped[256];
        size_t ei = 0;
        for (size_t ti = 0; ti < text_len && ei < sizeof(escaped) - 2; ti++) {
            char ch = text[ti];
            if (ch == '"' || ch == '\\') escaped[ei++] = '\\';
            escaped[ei++] = ch;
        }
        escaped[ei] = '\0';

        char json[512];
        snprintf(json, sizeof(json),
                 "{\"type\":\"recv_text\",\"from\":\"%s\",\"name\":\"%s\",\"text\":\"%s\"}",
                 pub_hex, c.name.c_str(), escaped);
        emitJson(json);
    } else {
        // Generic data packet — emit as hex blob.
        char hex[MAX_PACKET_PAYLOAD * 2 + 1];
        bytes_to_hex(hex, data, std::min(len, (size_t)MAX_PACKET_PAYLOAD));
        char json[512];
        snprintf(json, sizeof(json),
                 "{\"type\":\"recv_data\",\"from\":\"%s\",\"payload_type\":%d,\"hex\":\"%s\"}",
                 pub_hex, (int)type, hex);
        emitJson(json);
    }
}

bool SimNode::onPeerPathRecv(mesh::Packet* /*packet*/, int sender_idx,
                              const uint8_t* /*secret*/,
                              uint8_t* path, uint8_t path_len,
                              uint8_t /*extra_type*/, uint8_t* /*extra*/,
                              uint8_t /*extra_len*/) {
    if (sender_idx < 0 || sender_idx >= (int)_search_results.size()) {
        return false;
    }
    int idx = _search_results[sender_idx];
    Contact& c = _contacts[idx];

    c.has_path = true;
    c.path.assign(path, path + path_len);

    char pub_hex[PUB_KEY_SIZE * 2 + 1];
    bytes_to_hex(pub_hex, c.id.pub_key, PUB_KEY_SIZE);
    emitLog("path learned to %s (len=%d)", pub_hex, (int)path_len);

    return false;  // don't send reciprocal path automatically
}

void SimNode::onAdvertRecv(mesh::Packet* /*packet*/, const mesh::Identity& id,
                           uint32_t /*timestamp*/,
                           const uint8_t* app_data, size_t app_data_len) {
    // Skip if this is our own advert.
    if (id.matches(self_id)) return;

    // Update or insert into contacts.
    Contact* existing = nullptr;
    for (auto& c : _contacts) {
        if (c.id.matches(id)) { existing = &c; break; }
    }
    if (!existing) {
        _contacts.emplace_back();
        existing = &_contacts.back();
        existing->id = id;
        // Pre-compute ECDH shared secret.
        self_id.calcSharedSecret(existing->shared_secret, id);
    }
    // Update name from app_data (treated as a null-terminated string).
    if (app_data && app_data_len > 0) {
        existing->name = std::string((const char*)app_data,
                                     strnlen((const char*)app_data, app_data_len));
    }

    char pub_hex[PUB_KEY_SIZE * 2 + 1];
    bytes_to_hex(pub_hex, id.pub_key, PUB_KEY_SIZE);
    char json[256];
    snprintf(json, sizeof(json),
             "{\"type\":\"advert\",\"pub\":\"%s\",\"name\":\"%s\"}",
             pub_hex, existing->name.c_str());
    emitJson(json);
}

void SimNode::onAckRecv(mesh::Packet* /*packet*/, uint32_t ack_crc) {
    char json[128];
    snprintf(json, sizeof(json), "{\"type\":\"ack\",\"crc\":%u}", ack_crc);
    emitJson(json);
}

void SimNode::logRx(mesh::Packet* packet, int len, float score) {
    emitLog("RX len=%d type=%d route=%s score=%.2f",
            len, (int)packet->getPayloadType(),
            packet->isRouteDirect() ? "D" : "F", score);
}

void SimNode::logTx(mesh::Packet* packet, int len) {
    emitLog("TX len=%d type=%d route=%s",
            len, (int)packet->getPayloadType(),
            packet->isRouteDirect() ? "D" : "F");
}

// ---------------------------------------------------------------------------
// Application-level helpers
// ---------------------------------------------------------------------------
bool SimNode::sendTextTo(const std::string& dest_pub_hex,
                         const std::string& text) {
    // Find contact whose pub_key hex starts with dest_pub_hex (prefix match).
    Contact* target = nullptr;
    for (auto& c : _contacts) {
        char pub_hex[PUB_KEY_SIZE * 2 + 1];
        bytes_to_hex(pub_hex, c.id.pub_key, PUB_KEY_SIZE);
        if (std::string(pub_hex).rfind(dest_pub_hex, 0) == 0) {
            target = &c;
            break;
        }
    }
    if (!target) {
        emitLog("sendTextTo: unknown destination %s", dest_pub_hex.c_str());
        return false;
    }

    // MeshCore text payload: 4-byte timestamp + text (no null terminator needed).
    uint32_t ts = getRTCClock()->getCurrentTimeUnique();
    std::vector<uint8_t> payload(4 + text.size());
    memcpy(payload.data(), &ts, 4);
    memcpy(payload.data() + 4, text.data(), text.size());

    mesh::Packet* pkt = createDatagram(PAYLOAD_TYPE_TXT_MSG,
                                       target->id,
                                       target->shared_secret,
                                       payload.data(), payload.size());
    if (!pkt) {
        emitLog("sendTextTo: createDatagram failed (pool exhausted?)");
        return false;
    }

    if (target->has_path && !target->path.empty()) {
        sendDirect(pkt, target->path.data(), (uint8_t)target->path.size());
    } else {
        sendFlood(pkt);
    }
    return true;
}

void SimNode::broadcastAdvert(const std::string& name) {
    uint8_t app_data[MAX_ADVERT_DATA_SIZE];
    size_t  app_len = 0;
    if (!name.empty()) {
        app_len = std::min(name.size(), (size_t)MAX_ADVERT_DATA_SIZE - 1);
        memcpy(app_data, name.data(), app_len);
        app_data[app_len++] = '\0';
    }
    mesh::Packet* pkt = createAdvert(self_id,
                                     app_len ? app_data : nullptr,
                                     app_len);
    if (pkt) sendFlood(pkt);
}
