// test_crypto.cpp
//
// Tests for:
//  1. SHA256 shim              – known NIST/RFC vectors
//  2. AES128 shim              – NIST SP 800-38A ECB vector + roundtrip
//  3. Ed25519 (lib/ed25519)    – sign/verify properties
//  4. ECDH (LocalIdentity)     – shared-secret symmetry
//  5. Utils encrypt/MAC layer  – encryptThenMAC / MACThenDecrypt
//  6. Utils::sha256 wrapper    – exercises the MeshCore-level helper

#include "test_runner.h"

#include <SHA256.h>   // our crypto shim
#include <AES.h>      // our crypto shim
#include <Identity.h> // mesh::LocalIdentity, mesh::Identity
#include <Utils.h>    // mesh::Utils

// Pull in SimRNG so we can generate fresh key-pairs in tests that need them.
#include "../node_agent/SimRNG.h"

#include <string.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// Helper: parse a lower-case hex string into a byte array.
// Aborts (returns false) on length mismatch.
// ---------------------------------------------------------------------------
static bool from_hex(uint8_t* out, size_t out_len, const char* hex) {
    size_t hex_len = strlen(hex);
    if (hex_len != out_len * 2) return false;
    auto nib = [](char c) -> uint8_t {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return 0;
    };
    for (size_t i = 0; i < out_len; i++)
        out[i] = (nib(hex[i*2]) << 4) | nib(hex[i*2+1]);
    return true;
}

// ---------------------------------------------------------------------------
// SHA-256
// ---------------------------------------------------------------------------

// NIST FIPS 180-4, Section 6.1 examples.
TEST(sha256_empty_string) {
    uint8_t got[32];
    SHA256 sha;
    sha.finalize(got, 32);

    uint8_t want[32];
    from_hex(want, 32, "e3b0c44298fc1c149afbf4c8996fb924"
                       "27ae41e4649b934ca495991b7852b855");
    EXPECT_BYTES_EQ(got, want, 32);
}

TEST(sha256_abc) {
    uint8_t got[32];
    SHA256 sha;
    sha.update("abc", 3);
    sha.finalize(got, 32);

    uint8_t want[32];
    from_hex(want, 32, "ba7816bf8f01cfea414140de5dae2223"
                       "b00361a396177a9cb410ff61f20015ad");
    EXPECT_BYTES_EQ(got, want, 32);
}

// Truncated output: MeshCore requests only MAX_HASH_SIZE (8) bytes.
TEST(sha256_truncation) {
    uint8_t full[32], trunc[8];
    SHA256 sha1, sha2;
    sha1.update("hello", 5); sha1.finalize(full,  32);
    sha2.update("hello", 5); sha2.finalize(trunc,  8);
    // Truncated output must match the leading bytes of the full hash.
    EXPECT_BYTES_EQ(trunc, full, 8);
}

TEST(sha256_consistency) {
    uint8_t h1[32], h2[32];
    SHA256 sha1, sha2;
    sha1.update("meshcore", 8); sha1.finalize(h1, 32);
    sha2.update("meshcore", 8); sha2.finalize(h2, 32);
    EXPECT_BYTES_EQ(h1, h2, 32);
}

TEST(sha256_different_inputs_differ) {
    uint8_t h1[32], h2[32];
    SHA256 sha1, sha2;
    sha1.update("aaa", 3); sha1.finalize(h1, 32);
    sha2.update("bbb", 3); sha2.finalize(h2, 32);
    EXPECT_BYTES_NE(h1, h2, 32);
}

// Utils::sha256 is the MeshCore-level thin wrapper — exercises the same path.
TEST(utils_sha256_matches_direct) {
    const char* msg = "test vector";
    uint8_t via_utils[32], via_class[32];

    mesh::Utils::sha256(via_utils, 32, (const uint8_t*)msg, (int)strlen(msg));

    SHA256 sha;
    sha.update(msg, strlen(msg));
    sha.finalize(via_class, 32);

    EXPECT_BYTES_EQ(via_utils, via_class, 32);
}

// ---------------------------------------------------------------------------
// HMAC-SHA-256  (RFC 4231 test vectors)
// ---------------------------------------------------------------------------

// RFC 4231 Test Case 1
//   Key  = 0x0b0b...0b (20 bytes)
//   Data = "Hi There"
//   HMAC = b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7
TEST(hmac_sha256_rfc4231_tc1) {
    uint8_t key[20];
    memset(key, 0x0b, 20);

    uint8_t want[32];
    from_hex(want, 32, "b0344c61d8db38535ca8afceaf0bf12b"
                       "881dc200c9833da726e9376c2e32cff7");

    uint8_t got[32];
    SHA256 sha;
    sha.resetHMAC(key, 20);
    sha.update("Hi There", 8);
    sha.finalizeHMAC(key, 20, got, 32);

    EXPECT_BYTES_EQ(got, want, 32);
}

// RFC 4231 Test Case 2
//   Key  = "Jefe"
//   Data = "what do ya want for nothing?"
//   HMAC = 5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964a72020
TEST(hmac_sha256_rfc4231_tc2) {
    uint8_t want[32];
    from_hex(want, 32, "5bdcc146bf60754e6a042426089575c7"
                       "5a003f089d2739839dec58b964ec3843");

    uint8_t got[32];
    SHA256 sha;
    sha.resetHMAC("Jefe", 4);
    sha.update("what do ya want for nothing?", 28);
    sha.finalizeHMAC("Jefe", 4, got, 32);

    EXPECT_BYTES_EQ(got, want, 32);
}

// Different key → different HMAC.
TEST(hmac_sha256_key_sensitivity) {
    uint8_t key1[4] = {0x01,0x02,0x03,0x04};
    uint8_t key2[4] = {0x01,0x02,0x03,0x05};  // last byte differs

    uint8_t h1[32], h2[32];
    SHA256 sha1, sha2;
    sha1.resetHMAC(key1, 4); sha1.update("msg", 3); sha1.finalizeHMAC(key1, 4, h1, 32);
    sha2.resetHMAC(key2, 4); sha2.update("msg", 3); sha2.finalizeHMAC(key2, 4, h2, 32);

    EXPECT_BYTES_NE(h1, h2, 32);
}

// ---------------------------------------------------------------------------
// AES-128 ECB
// ---------------------------------------------------------------------------

// NIST SP 800-38A, Appendix F.1.1 — AES-128-ECB Encrypt, Block #1.
//   Key  = 2b7e151628aed2a6abf7158809cf4f3c
//   PT   = 6bc1bee22e409f96e93d7e117393172a
//   CT   = 3ad77bb40d7a3660a89ecaf32466ef97
TEST(aes128_ecb_nist_encrypt) {
    uint8_t key[16], pt[16], want_ct[16];
    from_hex(key,     16, "2b7e151628aed2a6abf7158809cf4f3c");
    from_hex(pt,      16, "6bc1bee22e409f96e93d7e117393172a");
    from_hex(want_ct, 16, "3ad77bb40d7a3660a89ecaf32466ef97");

    uint8_t ct[16];
    AES128 aes;
    aes.setKey(key, 16);
    aes.encryptBlock(ct, pt);

    EXPECT_BYTES_EQ(ct, want_ct, 16);
}

TEST(aes128_ecb_nist_decrypt) {
    uint8_t key[16], ct[16], want_pt[16];
    from_hex(key,     16, "2b7e151628aed2a6abf7158809cf4f3c");
    from_hex(ct,      16, "3ad77bb40d7a3660a89ecaf32466ef97");
    from_hex(want_pt, 16, "6bc1bee22e409f96e93d7e117393172a");

    uint8_t pt[16];
    AES128 aes;
    aes.setKey(key, 16);
    aes.decryptBlock(pt, ct);

    EXPECT_BYTES_EQ(pt, want_pt, 16);
}

// Encrypt then decrypt a fresh random block — must recover original.
TEST(aes128_ecb_roundtrip) {
    SimRNG rng;
    uint8_t key[16], plaintext[16], ciphertext[16], recovered[16];
    rng.random(key,       16);
    rng.random(plaintext, 16);

    AES128 aes;
    aes.setKey(key, 16);
    aes.encryptBlock(ciphertext, plaintext);
    aes.decryptBlock(recovered,  ciphertext);

    EXPECT_BYTES_EQ(recovered, plaintext,  16);
    EXPECT_BYTES_NE(ciphertext, plaintext, 16);  // sanity: encrypted != plain
}

// Different keys → different ciphertext for the same plaintext.
TEST(aes128_ecb_key_sensitivity) {
    uint8_t key1[16], key2[16], pt[16], ct1[16], ct2[16];
    memset(key1, 0x00, 16);
    memset(key2, 0xFF, 16);
    memset(pt,   0xAB, 16);

    AES128 aes1, aes2;
    aes1.setKey(key1, 16); aes1.encryptBlock(ct1, pt);
    aes2.setKey(key2, 16); aes2.encryptBlock(ct2, pt);

    EXPECT_BYTES_NE(ct1, ct2, 16);
}

// ---------------------------------------------------------------------------
// Ed25519 — sign / verify (property-based, not vector-based)
// ---------------------------------------------------------------------------

TEST(ed25519_sign_verify_roundtrip) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng);

    const uint8_t msg[] = "meshcore test message";
    uint8_t sig[SIGNATURE_SIZE];
    alice.sign(sig, msg, sizeof(msg));

    EXPECT(alice.verify(sig, msg, sizeof(msg)));
}

TEST(ed25519_wrong_key_rejects) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng);
    mesh::LocalIdentity bob(&rng);

    const uint8_t msg[] = "hello";
    uint8_t sig[SIGNATURE_SIZE];
    alice.sign(sig, msg, sizeof(msg));

    // Bob's key should not verify Alice's signature.
    EXPECT(!bob.verify(sig, msg, sizeof(msg)));
}

TEST(ed25519_tampered_message_rejects) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng);

    uint8_t msg[32];
    rng.random(msg, sizeof(msg));
    uint8_t sig[SIGNATURE_SIZE];
    alice.sign(sig, msg, sizeof(msg));

    msg[0] ^= 0x01;  // flip one bit
    EXPECT(!alice.verify(sig, msg, sizeof(msg)));
}

TEST(ed25519_tampered_signature_rejects) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng);

    const uint8_t msg[] = "signed payload";
    uint8_t sig[SIGNATURE_SIZE];
    alice.sign(sig, msg, sizeof(msg));

    sig[0] ^= 0xFF;  // corrupt first byte of signature
    EXPECT(!alice.verify(sig, msg, sizeof(msg)));
}

// ---------------------------------------------------------------------------
// ECDH — shared secret symmetry
// ---------------------------------------------------------------------------

// Core property: A.calc(B.pub) == B.calc(A.pub).
TEST(ecdh_symmetry) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng);
    mesh::LocalIdentity bob(&rng);

    uint8_t ss_alice[PUB_KEY_SIZE], ss_bob[PUB_KEY_SIZE];
    alice.calcSharedSecret(ss_alice, bob);
    bob.calcSharedSecret(ss_bob,   alice);

    EXPECT_BYTES_EQ(ss_alice, ss_bob, PUB_KEY_SIZE);
}

// Shared secret must be non-zero (all-zero would indicate a degenerate key).
TEST(ecdh_non_trivial) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng);
    mesh::LocalIdentity bob(&rng);

    uint8_t ss[PUB_KEY_SIZE];
    alice.calcSharedSecret(ss, bob);

    uint8_t zeros[PUB_KEY_SIZE] = {};
    EXPECT_BYTES_NE(ss, zeros, PUB_KEY_SIZE);
}

// Different peer → different shared secret.
TEST(ecdh_different_peers_differ) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng);
    mesh::LocalIdentity bob(&rng);
    mesh::LocalIdentity carol(&rng);

    uint8_t ss_ab[PUB_KEY_SIZE], ss_ac[PUB_KEY_SIZE];
    alice.calcSharedSecret(ss_ab, bob);
    alice.calcSharedSecret(ss_ac, carol);

    EXPECT_BYTES_NE(ss_ab, ss_ac, PUB_KEY_SIZE);
}

// ---------------------------------------------------------------------------
// Utils::encryptThenMAC / MACThenDecrypt
// ---------------------------------------------------------------------------

TEST(encrypt_then_mac_roundtrip) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng), bob(&rng);
    uint8_t secret[PUB_KEY_SIZE];
    alice.calcSharedSecret(secret, bob);

    const uint8_t plaintext[] = "Hello, MeshCore!";
    uint8_t buf[256];

    int enc_len = mesh::Utils::encryptThenMAC(secret, buf,
                                              plaintext, sizeof(plaintext));
    EXPECT(enc_len > 0);

    uint8_t recovered[256];
    int dec_len = mesh::Utils::MACThenDecrypt(secret, recovered, buf, enc_len);
    EXPECT(dec_len > 0);

    // Decrypted output must begin with the original plaintext.
    // (Output may be padded to a block boundary with zeroes.)
    EXPECT(dec_len >= (int)sizeof(plaintext));
    EXPECT_BYTES_EQ(recovered, plaintext, sizeof(plaintext));
}

TEST(encrypt_then_mac_tamper_ciphertext_rejected) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng), bob(&rng);
    uint8_t secret[PUB_KEY_SIZE];
    alice.calcSharedSecret(secret, bob);

    const uint8_t pt[] = "secret payload 1234";
    uint8_t buf[256];
    int enc_len = mesh::Utils::encryptThenMAC(secret, buf, pt, sizeof(pt));

    // Flip a byte in the ciphertext (after the MAC prefix).
    buf[CIPHER_MAC_SIZE + 2] ^= 0xFF;

    uint8_t out[256];
    int dec_len = mesh::Utils::MACThenDecrypt(secret, out, buf, enc_len);
    EXPECT_EQ(dec_len, 0);  // must reject
}

TEST(encrypt_then_mac_tamper_mac_rejected) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng), bob(&rng);
    uint8_t secret[PUB_KEY_SIZE];
    alice.calcSharedSecret(secret, bob);

    const uint8_t pt[] = "another secret payload";
    uint8_t buf[256];
    int enc_len = mesh::Utils::encryptThenMAC(secret, buf, pt, sizeof(pt));

    // Corrupt the MAC prefix itself.
    buf[0] ^= 0x01;

    uint8_t out[256];
    int dec_len = mesh::Utils::MACThenDecrypt(secret, out, buf, enc_len);
    EXPECT_EQ(dec_len, 0);  // must reject
}

TEST(encrypt_then_mac_wrong_key_rejected) {
    SimRNG rng;
    mesh::LocalIdentity alice(&rng), bob(&rng), carol(&rng);

    uint8_t secret_ab[PUB_KEY_SIZE], secret_ac[PUB_KEY_SIZE];
    alice.calcSharedSecret(secret_ab, bob);
    alice.calcSharedSecret(secret_ac, carol);

    const uint8_t pt[] = "only bob should read this";
    uint8_t buf[256];
    mesh::Utils::encryptThenMAC(secret_ab, buf, pt, sizeof(pt));

    uint8_t out[256];
    // Carol tries to decrypt with the wrong shared secret.
    int dec_len = mesh::Utils::MACThenDecrypt(secret_ac, out, buf,
                  CIPHER_MAC_SIZE + 32 /* approx enc len */);
    EXPECT_EQ(dec_len, 0);
}
