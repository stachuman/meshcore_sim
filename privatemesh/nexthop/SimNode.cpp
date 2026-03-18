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
// Proactive next-hop routing table  (experiment: nexthop)
//
// Built from advertisement packets received during the warmup flood.  Each
// entry maps a 2-byte destination prefix to the shortest reversed relay path
// (suitable for passing directly to sendDirect) and tracks an age counter so
// stale entries are evicted after RT_MAX_AGE advertisement cycles.
//
// Memory budget: RT_MAX_ROUTES × sizeof(RouteEntry) = 64 × 70 = 4480 bytes.
// ---------------------------------------------------------------------------
#define RT_MAX_ROUTES  64   // number of table slots (must exceed network size for best coverage)
#define RT_MAX_AGE      3   // expire after this many advert cycles without refresh
#define RT_PATH_CAP    64   // max relay-path bytes per entry (= MAX_PATH_SIZE; fits any path)
#define RT_AGE_EVERY   16   // increment ages every N adverts received

struct RouteEntry {
    uint8_t dest[2];            // first 2 bytes of destination public key
    uint8_t path[RT_PATH_CAP];  // reversed relay path bytes (for sendDirect)
    uint8_t path_bytes;         // byte count in path[] (0 = direct neighbour)
    uint8_t hash_sz;            // bytes per relay hash (normally 1 per PATH_HASH_SIZE)
    uint8_t metric;             // hop count to destination
    uint8_t age;                // incremented each cycle; reset on refresh
};

static RouteEntry rt_table[RT_MAX_ROUTES];
static uint8_t    rt_count       = 0;   // live entries
static uint32_t   rt_advert_seen = 0;   // total adverts received (drives aging)

static RouteEntry* rt_find(const uint8_t dest[2]) {
    for (uint8_t i = 0; i < rt_count; i++) {
        if (rt_table[i].dest[0] == dest[0] && rt_table[i].dest[1] == dest[1])
            return &rt_table[i];
    }
    return nullptr;
}

static void rt_age_entries() {
    uint8_t i = 0;
    while (i < rt_count) {
        if (++rt_table[i].age > RT_MAX_AGE) {
            rt_table[i] = rt_table[--rt_count];  // evict: swap with tail entry
        } else {
            i++;
        }
    }
}

// Insert or refresh a route.  Only updates an existing entry if the new metric
// is equal or better (shorter path).  When the table is full, evicts the
// stalest entry if the new metric is strictly better.
static void rt_upsert(const uint8_t dest[2],
                      const uint8_t* rev_path, uint8_t path_bytes,
                      uint8_t hash_sz, uint8_t metric) {
    RouteEntry* e = rt_find(dest);
    if (e) {
        if (metric <= e->metric) {
            uint8_t n = std::min(path_bytes, (uint8_t)RT_PATH_CAP);
            memcpy(e->path, rev_path, n);
            e->path_bytes = n;
            e->hash_sz    = hash_sz;
            e->metric     = metric;
            e->age        = 0;
        }
        return;
    }
    if (rt_count < RT_MAX_ROUTES) {
        RouteEntry& ne = rt_table[rt_count++];
        ne.dest[0]    = dest[0];
        ne.dest[1]    = dest[1];
        uint8_t n     = std::min(path_bytes, (uint8_t)RT_PATH_CAP);
        memcpy(ne.path, rev_path, n);
        ne.path_bytes = n;
        ne.hash_sz    = hash_sz;
        ne.metric     = metric;
        ne.age        = 0;
        return;
    }
    // Table full: prefer evicting useless direct-neighbour entries (path_bytes=0)
    // first; among equally-useful entries, evict the stalest.
    uint8_t worst = 0;
    for (uint8_t i = 1; i < rt_count; i++) {
        bool i_useless    = (rt_table[i].path_bytes == 0);
        bool worst_useless = (rt_table[worst].path_bytes == 0);
        if (i_useless && !worst_useless) {
            worst = i;   // always prefer evicting a useless entry
        } else if (i_useless == worst_useless) {
            // Both equally useful (or useless): pick stalest
            if (rt_table[i].age > rt_table[worst].age)
                worst = i;
        }
    }
    // Replace if the incoming route is useful (has a path) and the worst slot
    // is either useless or strictly older than us.
    bool incoming_useful = (path_bytes > 0);
    bool worst_useless   = (rt_table[worst].path_bytes == 0);
    if (incoming_useful && (worst_useless || metric < rt_table[worst].metric)) {
        rt_table[worst].dest[0]    = dest[0];
        rt_table[worst].dest[1]    = dest[1];
        uint8_t n                  = std::min(path_bytes, (uint8_t)RT_PATH_CAP);
        memcpy(rt_table[worst].path, rev_path, n);
        rt_table[worst].path_bytes = n;
        rt_table[worst].hash_sz    = hash_sz;
        rt_table[worst].metric     = metric;
        rt_table[worst].age        = 0;
    }
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
void SimNode::onPeerDataRecv(mesh::Packet* packet, uint8_t type,
                              int sender_idx, const uint8_t* /*secret*/,
                              uint8_t* data, size_t len) {
    if (sender_idx < 0 || sender_idx >= (int)_search_results.size()) return;
    int idx = _search_results[sender_idx];
    Contact& c = _contacts[idx];

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

    // -----------------------------------------------------------------------
    // Path exchange: when we receive a flood TXT_MSG from a peer we don't yet
    // have a direct route to, (1) store the *reversed* relay-hash sequence as
    // our direct path back to that sender, and (2) flood a PATH reply so the
    // sender learns the forward path to reach us directly next time.
    // -----------------------------------------------------------------------
    if (type == PAYLOAD_TYPE_TXT_MSG
        && packet->isRouteFlood()
        && !c.has_path
        && packet->getPathHashCount() > 0) {

        uint8_t sz  = packet->getPathHashSize();
        uint8_t cnt = packet->getPathHashCount();

        // Reverse the relay-hash sequence: forward path in the arriving packet
        // is [r1, r2, ..., rN] (origin → us).  To route back we need the
        // reversed order [rN, ..., r2, r1].
        c.path.resize((size_t)cnt * sz);
        for (uint8_t i = 0; i < cnt; i++) {
            memcpy(c.path.data() + (size_t)(cnt - 1 - i) * sz,
                   packet->path + (size_t)i * sz, sz);
        }
        c.has_path = true;

        // Flood a PATH packet back so the sender learns the direct path to us.
        mesh::Packet* rpath = createPathReturn(
            c.id, c.shared_secret,
            packet->path, packet->path_len,
            0, nullptr, 0);
        if (rpath) sendFlood(rpath);

        char pub_hex2[PUB_KEY_SIZE * 2 + 1];
        bytes_to_hex(pub_hex2, c.id.pub_key, PUB_KEY_SIZE);
        emitLog("path-exchange: stored %d-hop reverse path to %.16s; sent PATH return",
                (int)cnt, pub_hex2);
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

void SimNode::onAdvertRecv(mesh::Packet* packet, const mesh::Identity& id,
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

    // --- routing table update ---
    // Extract source identity's relay path from this advert packet.
    // packet->path is [r1, r2, ..., rN] (origin → us); sendDirect needs the
    // reverse [rN, ..., r1] (nearest relay first).
    uint8_t hash_sz  = packet->getPathHashSize();
    uint8_t hash_cnt = packet->getPathHashCount();
    uint8_t metric   = hash_cnt + 1;   // direct neighbour = 1 hop

    uint8_t rev_path[RT_PATH_CAP] = {};
    uint8_t rev_bytes = 0;
    uint8_t copy_cnt  = std::min(hash_cnt,
                                 (uint8_t)(RT_PATH_CAP / (hash_sz ? hash_sz : 1)));
    for (uint8_t i = 0; i < copy_cnt; i++) {
        uint8_t src_off = (hash_cnt - 1 - i) * hash_sz;
        uint8_t dst_off = i * hash_sz;
        memcpy(rev_path + dst_off, packet->path + src_off, hash_sz);
        rev_bytes += hash_sz;
    }

    // Age all entries periodically (every RT_AGE_EVERY adverts received).
    if ((++rt_advert_seen % RT_AGE_EVERY) == 0)
        rt_age_entries();

    rt_upsert(id.pub_key, rev_path, rev_bytes, hash_sz, metric);
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

    // --- routing table lookup (nexthop experiment) ---
    // Use a proactively cached multi-hop path from the advert flood, bypassing
    // the flood → path-exchange round-trip required by standard node_agent.
    // Only applies to multi-hop destinations (metric > 1); direct neighbours
    // fall through to the standard path/flood logic below.
    const RouteEntry* r = rt_find(target->id.pub_key);
    if (r && r->metric > 1 && r->path_bytes > 0) {
        sendDirect(pkt, r->path, r->path_bytes);
        emitLog("nexthop: table-direct to %.8s (metric=%d age=%d)",
                dest_pub_hex.c_str(), (int)r->metric, (int)r->age);
        return true;
    }

    // Fallback: standard path-exchange direct or flood.
    if (target->has_path && !target->path.empty()) {
        sendDirect(pkt, target->path.data(), (uint8_t)target->path.size());
    } else {
        sendFlood(pkt);
    }
    return true;
}

// ---------------------------------------------------------------------------
// RoomServerNode implementation
// ---------------------------------------------------------------------------

// JSON-escape a raw string into a fixed-size buffer.  Returns false if the
// buffer was too small (output is still null-terminated and safe to use).
static bool json_escape(char* out, size_t out_size,
                        const char* in, size_t in_len) {
    size_t wi = 0;
    for (size_t ri = 0; ri < in_len; ri++) {
        unsigned char ch = (unsigned char)in[ri];
        if (wi + 3 >= out_size) { out[wi] = '\0'; return false; }
        if (ch == '"' || ch == '\\') { out[wi++] = '\\'; }
        out[wi++] = (char)ch;
    }
    out[wi] = '\0';
    return true;
}

RoomServerNode::RoomServerNode(mesh::Radio& radio, mesh::MillisecondClock& ms,
                               mesh::RNG& rng, mesh::RTCClock& rtc,
                               mesh::PacketManager& mgr,
                               mesh::MeshTables& tables)
    : SimNode(radio, ms, rng, rtc, mgr, tables, /*is_relay=*/false)
{}

void RoomServerNode::onPeerDataRecv(mesh::Packet* packet, uint8_t type,
                                    int sender_idx, const uint8_t* secret,
                                    uint8_t* data, size_t len) {
    // Let the base class handle recv_text emission and path exchange first.
    SimNode::onPeerDataRecv(packet, type, sender_idx, secret, data, len);

    if (type != PAYLOAD_TYPE_TXT_MSG || len <= 4) return;

    if (sender_idx < 0 || sender_idx >= (int)_search_results.size()) return;
    int idx = _search_results[sender_idx];
    Contact& sender = _contacts[idx];

    const char* raw_text = (const char*)(data + 4);
    size_t      raw_len  = len - 4;

    char esc_name[128], esc_text[256];
    json_escape(esc_name, sizeof(esc_name),
                sender.name.c_str(), sender.name.size());
    json_escape(esc_text, sizeof(esc_text), raw_text, raw_len);

    char pub_hex[PUB_KEY_SIZE * 2 + 1];
    bytes_to_hex(pub_hex, sender.id.pub_key, PUB_KEY_SIZE);
    char event_json[640];
    snprintf(event_json, sizeof(event_json),
             "{\"type\":\"room_post\",\"from\":\"%s\","
             "\"name\":\"%s\",\"text\":\"%s\"}",
             pub_hex, esc_name, esc_text);
    emitJson(event_json);

    char fwd[320];
    snprintf(fwd, sizeof(fwd), "[%s]: %.*s",
             sender.name.c_str(), (int)raw_len, raw_text);
    std::string fwd_str(fwd);

    for (int ci = 0; ci < (int)_contacts.size(); ci++) {
        if (ci == idx) continue;
        char dest_hex[PUB_KEY_SIZE * 2 + 1];
        bytes_to_hex(dest_hex, _contacts[ci].id.pub_key, PUB_KEY_SIZE);
        sendTextTo(std::string(dest_hex), fwd_str);
    }
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
