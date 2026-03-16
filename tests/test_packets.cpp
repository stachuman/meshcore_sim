// test_packets.cpp
//
// Tests for:
//  1. Packet serialisation roundtrip  – writeTo / readFrom
//  2. Path encoding                   – flood with accumulated hops
//  3. Packet hash                     – stability and collision resistance
//  4. Header accessors                – route type, payload type, flags
//  5. SimpleMeshTables                – flood and ACK deduplication

#include "test_runner.h"

#include <Packet.h>
#include <helpers/SimpleMeshTables.h>
#include <string.h>
#include <stdint.h>

using namespace mesh;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Build a minimal valid flood packet with a given payload type and payload.
static Packet make_flood(uint8_t payload_type,
                         const uint8_t* payload, uint16_t payload_len) {
    Packet p;
    p.header   = ROUTE_TYPE_FLOOD | (payload_type << PH_TYPE_SHIFT);
    p.path_len = 0;  // no hops yet
    p.payload_len = payload_len;
    if (payload && payload_len)
        memcpy(p.payload, payload, payload_len);
    p._snr = 0;
    return p;
}

// Build a direct-route packet with a supplied path.
static Packet make_direct(uint8_t payload_type,
                          const uint8_t* path, uint8_t path_hops,
                          const uint8_t* payload, uint16_t payload_len) {
    Packet p;
    p.header = ROUTE_TYPE_DIRECT | (payload_type << PH_TYPE_SHIFT);
    p.setPathHashSizeAndCount(1, path_hops);   // 1-byte hashes
    if (path && path_hops)
        memcpy(p.path, path, path_hops);
    p.payload_len = payload_len;
    if (payload && payload_len)
        memcpy(p.payload, payload, payload_len);
    p._snr = 24;  // 6.0 dB * 4
    return p;
}

// ---------------------------------------------------------------------------
// Packet serialisation — flood
// ---------------------------------------------------------------------------

TEST(packet_flood_roundtrip_empty_path) {
    const uint8_t pl[] = {0x01, 0x02, 0x03, 0xAA, 0xBB};
    Packet orig = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, sizeof(pl));

    uint8_t raw[MAX_TRANS_UNIT];
    uint8_t raw_len = orig.writeTo(raw);
    EXPECT(raw_len > 0);

    Packet got;
    EXPECT(got.readFrom(raw, raw_len));

    EXPECT_EQ(got.header,      orig.header);
    EXPECT_EQ(got.path_len,    orig.path_len);
    EXPECT_EQ(got.payload_len, orig.payload_len);
    EXPECT_BYTES_EQ(got.payload, orig.payload, orig.payload_len);
}

TEST(packet_flood_roundtrip_with_accumulated_path) {
    // Simulate a flood packet that has passed through 3 relay nodes.
    const uint8_t pl[]   = {0xDE, 0xAD, 0xBE, 0xEF};
    const uint8_t hops[] = {0xAA, 0xBB, 0xCC};    // 1-byte hashes of 3 relays

    Packet orig = make_flood(PAYLOAD_TYPE_ADVERT, pl, sizeof(pl));
    orig.setPathHashSizeAndCount(1, 3);
    memcpy(orig.path, hops, 3);

    uint8_t raw[MAX_TRANS_UNIT];
    uint8_t raw_len = orig.writeTo(raw);

    Packet got;
    EXPECT(got.readFrom(raw, raw_len));

    EXPECT_EQ(got.header,           orig.header);
    EXPECT_EQ(got.getPathHashCount(), (uint8_t)3);
    EXPECT_EQ(got.getPathHashSize(),  (uint8_t)1);
    EXPECT_EQ(got.payload_len,       orig.payload_len);
    EXPECT_BYTES_EQ(got.path,    hops,         3);
    EXPECT_BYTES_EQ(got.payload, orig.payload, orig.payload_len);
}

// ---------------------------------------------------------------------------
// Packet serialisation — direct route
// ---------------------------------------------------------------------------

TEST(packet_direct_roundtrip) {
    const uint8_t pl[]   = {0x10, 0x20, 0x30};
    const uint8_t path[] = {0xA1, 0xB2};    // 2 hops

    Packet orig = make_direct(PAYLOAD_TYPE_REQ, path, 2, pl, sizeof(pl));

    uint8_t raw[MAX_TRANS_UNIT];
    uint8_t raw_len = orig.writeTo(raw);

    Packet got;
    EXPECT(got.readFrom(raw, raw_len));

    EXPECT_EQ(got.getRouteType(),    (uint8_t)ROUTE_TYPE_DIRECT);
    EXPECT_EQ(got.getPayloadType(),  (uint8_t)PAYLOAD_TYPE_REQ);
    EXPECT_EQ(got.getPathHashCount(), (uint8_t)2);
    EXPECT_BYTES_EQ(got.path,    path, 2);
    EXPECT_BYTES_EQ(got.payload, pl,   sizeof(pl));
}

// ---------------------------------------------------------------------------
// Transport-codes variant
// ---------------------------------------------------------------------------

TEST(packet_transport_flood_roundtrip) {
    const uint8_t pl[] = {0xFF, 0xEE};
    Packet orig = make_flood(PAYLOAD_TYPE_ACK, pl, sizeof(pl));
    // Promote to TRANSPORT_FLOOD
    orig.header = ROUTE_TYPE_TRANSPORT_FLOOD | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
    orig.transport_codes[0] = 0x1234;
    orig.transport_codes[1] = 0x5678;

    uint8_t raw[MAX_TRANS_UNIT];
    uint8_t raw_len = orig.writeTo(raw);

    Packet got;
    EXPECT(got.readFrom(raw, raw_len));

    EXPECT_EQ(got.getRouteType(),       (uint8_t)ROUTE_TYPE_TRANSPORT_FLOOD);
    EXPECT_EQ(got.transport_codes[0],   orig.transport_codes[0]);
    EXPECT_EQ(got.transport_codes[1],   orig.transport_codes[1]);
    EXPECT_BYTES_EQ(got.payload, orig.payload, orig.payload_len);
}

// ---------------------------------------------------------------------------
// Header accessors
// ---------------------------------------------------------------------------

TEST(packet_route_type_accessors) {
    Packet flood, direct, t_flood, t_direct;
    flood.header   = ROUTE_TYPE_FLOOD;
    direct.header  = ROUTE_TYPE_DIRECT;
    t_flood.header = ROUTE_TYPE_TRANSPORT_FLOOD;
    t_direct.header= ROUTE_TYPE_TRANSPORT_DIRECT;

    EXPECT(flood.isRouteFlood());   EXPECT(!flood.isRouteDirect());
    EXPECT(direct.isRouteDirect()); EXPECT(!direct.isRouteFlood());
    EXPECT(t_flood.isRouteFlood());
    EXPECT(t_direct.isRouteDirect());
    EXPECT(!flood.hasTransportCodes());
    EXPECT(t_flood.hasTransportCodes());
    EXPECT(t_direct.hasTransportCodes());
}

TEST(packet_payload_type_accessor) {
    for (uint8_t type = 0; type <= PAYLOAD_TYPE_RAW_CUSTOM; type++) {
        Packet p;
        p.header = ROUTE_TYPE_FLOOD | ((type & PH_TYPE_MASK) << PH_TYPE_SHIFT);
        EXPECT_EQ(p.getPayloadType(), type);
    }
}

// ---------------------------------------------------------------------------
// Packet hash
// ---------------------------------------------------------------------------

TEST(packet_hash_stability) {
    const uint8_t pl[] = {0xCA, 0xFE, 0xBA, 0xBE};
    Packet p = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, sizeof(pl));

    uint8_t h1[MAX_HASH_SIZE], h2[MAX_HASH_SIZE];
    p.calculatePacketHash(h1);
    p.calculatePacketHash(h2);

    EXPECT_BYTES_EQ(h1, h2, MAX_HASH_SIZE);
}

TEST(packet_hash_payload_sensitivity) {
    uint8_t pl1[] = {0x01, 0x02, 0x03};
    uint8_t pl2[] = {0x01, 0x02, 0x04};   // last byte differs

    Packet p1 = make_flood(PAYLOAD_TYPE_TXT_MSG, pl1, sizeof(pl1));
    Packet p2 = make_flood(PAYLOAD_TYPE_TXT_MSG, pl2, sizeof(pl2));

    uint8_t h1[MAX_HASH_SIZE], h2[MAX_HASH_SIZE];
    p1.calculatePacketHash(h1);
    p2.calculatePacketHash(h2);

    EXPECT_BYTES_NE(h1, h2, MAX_HASH_SIZE);
}

TEST(packet_hash_type_sensitivity) {
    // Same payload bytes, different payload type → different hash.
    const uint8_t pl[] = {0xAA, 0xBB, 0xCC};
    Packet p1 = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, sizeof(pl));
    Packet p2 = make_flood(PAYLOAD_TYPE_REQ,     pl, sizeof(pl));

    uint8_t h1[MAX_HASH_SIZE], h2[MAX_HASH_SIZE];
    p1.calculatePacketHash(h1);
    p2.calculatePacketHash(h2);

    EXPECT_BYTES_NE(h1, h2, MAX_HASH_SIZE);
}

// ---------------------------------------------------------------------------
// getRawLength
// ---------------------------------------------------------------------------

TEST(packet_raw_length_flood_no_path) {
    const uint8_t pl[10] = {};
    Packet p = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, 10);
    // wire format: 1 (header) + 1 (path_len byte) + 0 (no path bytes) + 10 (payload)
    EXPECT_EQ(p.getRawLength(), 12);
}

TEST(packet_raw_length_flood_with_path) {
    const uint8_t pl[8] = {};
    Packet p = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, 8);
    p.setPathHashSizeAndCount(1, 3);   // 3 one-byte hashes = 3 path bytes
    // 1 + 1 + 3 + 8 = 13
    EXPECT_EQ(p.getRawLength(), 13);
}

// ---------------------------------------------------------------------------
// isValidPathLen
// ---------------------------------------------------------------------------

TEST(packet_valid_path_len) {
    EXPECT(Packet::isValidPathLen(0));           // 0 hops, 1-byte hash
    EXPECT(Packet::isValidPathLen(0 | (0<<6)));  // same
    EXPECT(Packet::isValidPathLen(10));          // 10 hops, 1-byte hash = 10 bytes ≤ 64
    EXPECT(Packet::isValidPathLen(63));          // max hops at 1-byte hash
    // 1-byte hash size (bits [7:6]=0b00), 64 hops → 64 bytes = MAX_PATH_SIZE: valid
    EXPECT(Packet::isValidPathLen(0b00000000 | 64));  // 64 × 1 = 64 bytes
}

TEST(packet_invalid_path_len) {
    // Hash size = 4 (bits [7:6] = 0b11) is reserved.
    EXPECT(!Packet::isValidPathLen(0b11000001));  // size field = 3 → hash_size = 4
    // 1-byte hashes, 65 hops → 65 bytes > MAX_PATH_SIZE: invalid
    // path_len = (0b00 << 6) | 65 = 65
    // 65 > 63 (6-bit field max), so getPathHashCount() = 65 & 63 = 1... actually
    // need to check: (65 & 63) = 1 hop × 1 byte = 1 → valid. So pick a value that
    // encodes > 64 bytes.
    // 2-byte hashes (bits=0b01), 33 hops → 33 × 2 = 66 bytes > 64: invalid
    uint8_t path_len = (uint8_t)((1 << 6) | 33);  // size=2, count=33
    EXPECT(!Packet::isValidPathLen(path_len));
}

TEST(packet_readfrom_rejects_bad_path) {
    // Construct a raw buffer with a path_len encoding that claims more bytes
    // than are actually present.
    uint8_t raw[8];
    raw[0] = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
    raw[1] = 10;   // claims 10 one-byte path hops = 10 bytes, but buffer is only 8 total
    raw[2] = 0xAA; // only 1 path byte present
    // rest is payload (3 bytes)
    raw[3] = 0x01; raw[4] = 0x02; raw[5] = 0x03;

    Packet p;
    // Should reject because path would overrun the buffer.
    EXPECT(!p.readFrom(raw, 6));
}

// ---------------------------------------------------------------------------
// SimpleMeshTables — flood packet deduplication
// ---------------------------------------------------------------------------

TEST(tables_first_packet_not_seen) {
    SimpleMeshTables tables;
    const uint8_t pl[] = {0x11, 0x22, 0x33};
    Packet p = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, sizeof(pl));
    EXPECT(!tables.hasSeen(&p));
}

TEST(tables_second_packet_seen) {
    SimpleMeshTables tables;
    const uint8_t pl[] = {0x11, 0x22, 0x33};
    Packet p = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, sizeof(pl));

    EXPECT(!tables.hasSeen(&p));   // first: not seen
    EXPECT(tables.hasSeen(&p));    // second: already seen
}

TEST(tables_different_payload_not_seen) {
    SimpleMeshTables tables;
    const uint8_t pl1[] = {0xAA, 0xBB};
    const uint8_t pl2[] = {0xAA, 0xBC};   // last byte differs
    Packet p1 = make_flood(PAYLOAD_TYPE_TXT_MSG, pl1, sizeof(pl1));
    Packet p2 = make_flood(PAYLOAD_TYPE_TXT_MSG, pl2, sizeof(pl2));

    EXPECT(!tables.hasSeen(&p1));
    EXPECT(!tables.hasSeen(&p2));  // different payload → different hash → not a dup
}

TEST(tables_different_type_not_seen) {
    SimpleMeshTables tables;
    const uint8_t pl[] = {0x01, 0x02, 0x03};
    Packet p1 = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, sizeof(pl));
    Packet p2 = make_flood(PAYLOAD_TYPE_REQ,     pl, sizeof(pl));

    EXPECT(!tables.hasSeen(&p1));
    EXPECT(!tables.hasSeen(&p2));  // type differs → different hash
    EXPECT(tables.hasSeen(&p1));   // but p1 itself is still deduplicated
}

TEST(tables_clear_allows_resend) {
    SimpleMeshTables tables;
    const uint8_t pl[] = {0xDE, 0xAD};
    Packet p = make_flood(PAYLOAD_TYPE_TXT_MSG, pl, sizeof(pl));

    EXPECT(!tables.hasSeen(&p));
    EXPECT(tables.hasSeen(&p));    // seen
    tables.clear(&p);              // forget it
    EXPECT(!tables.hasSeen(&p));   // unseen again
}

// ---------------------------------------------------------------------------
// SimpleMeshTables — ACK packet deduplication
// ---------------------------------------------------------------------------

TEST(tables_ack_dedup) {
    SimpleMeshTables tables;

    // ACK packets use the first 4 bytes of the payload as the CRC key.
    uint32_t crc = 0xDEADBEEF;
    Packet ack;
    ack.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
    ack.path_len  = 0;
    ack.payload_len = 4;
    memcpy(ack.payload, &crc, 4);

    EXPECT(!tables.hasSeen(&ack));
    EXPECT(tables.hasSeen(&ack));
}

TEST(tables_different_ack_crcs_not_dupes) {
    SimpleMeshTables tables;
    uint32_t crc1 = 0x12345678;
    uint32_t crc2 = 0x87654321;

    Packet a1, a2;
    a1.header = a2.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
    a1.path_len = a2.path_len = 0;
    a1.payload_len = a2.payload_len = 4;
    memcpy(a1.payload, &crc1, 4);
    memcpy(a2.payload, &crc2, 4);

    EXPECT(!tables.hasSeen(&a1));
    EXPECT(!tables.hasSeen(&a2));
    EXPECT(tables.hasSeen(&a1));
    EXPECT(tables.hasSeen(&a2));
}
