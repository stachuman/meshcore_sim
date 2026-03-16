cmake_minimum_required(VERSION 3.16)
project(meshcore_tests CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 11)

# ---------------------------------------------------------------------------
# OpenSSL
# ---------------------------------------------------------------------------
find_package(OpenSSL REQUIRED)

# ---------------------------------------------------------------------------
# Paths  (relative to this CMakeLists.txt)
# ---------------------------------------------------------------------------
set(ROOT         ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(NODE_AGENT   ${ROOT}/node_agent)
set(MESHCORE_SRC ${ROOT}/MeshCore/src)
set(MESHCORE_LIB ${ROOT}/MeshCore/lib)
set(SHIM_DIR     ${NODE_AGENT}/arduino_shim)
set(CRYPTO_DIR   ${NODE_AGENT}/crypto_shim)

# ---------------------------------------------------------------------------
# MeshCore core sources (same subset as node_agent)
# ---------------------------------------------------------------------------
set(MESHCORE_CORE_SOURCES
    ${MESHCORE_SRC}/Packet.cpp
    ${MESHCORE_SRC}/Dispatcher.cpp
    ${MESHCORE_SRC}/Mesh.cpp
    ${MESHCORE_SRC}/Utils.cpp
    ${MESHCORE_SRC}/Identity.cpp
    ${MESHCORE_SRC}/helpers/StaticPoolPacketManager.cpp
)

# lib/ed25519 — portable C, no platform deps
file(GLOB ED25519_SOURCES ${MESHCORE_LIB}/ed25519/*.c)
list(FILTER ED25519_SOURCES EXCLUDE REGEX "seed\\.c$")

# ---------------------------------------------------------------------------
# Test executable
# ---------------------------------------------------------------------------
add_executable(meshcore_tests
    main.cpp
    test_crypto.cpp
    test_packets.cpp

    # Crypto shims (same as node_agent)
    ${CRYPTO_DIR}/SHA256.cpp
    ${CRYPTO_DIR}/AES.cpp

    # SimRNG — needed by crypto tests for key generation
    ${NODE_AGENT}/SimRNG.cpp

    # MeshCore core
    ${MESHCORE_CORE_SOURCES}
    ${ED25519_SOURCES}
)

target_include_directories(meshcore_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}   # test_runner.h
    ${SHIM_DIR}                   # Stream.h (Arduino shim)
    ${CRYPTO_DIR}                 # SHA256.h, AES.h, Ed25519.h
    ${MESHCORE_SRC}               # Packet.h, Identity.h, Utils.h, …
    ${MESHCORE_SRC}/helpers       # SimpleMeshTables.h, StaticPoolPacketManager.h
    ${MESHCORE_LIB}/ed25519       # ed_25519.h
    ${NODE_AGENT}                 # SimRNG.h
)

target_compile_definitions(meshcore_tests PRIVATE
    ED25519_NO_SEED=1
)

target_compile_options(meshcore_tests PRIVATE
    -Wall -Wextra -Wno-unused-parameter
)

target_link_libraries(meshcore_tests PRIVATE
    OpenSSL::SSL
    OpenSSL::Crypto
)

# ---------------------------------------------------------------------------
# CTest integration — lets you run tests with `ctest` or `cmake --build . --target test`
# ---------------------------------------------------------------------------
enable_testing()
add_test(NAME all_tests COMMAND meshcore_tests)
add_test(NAME crypto_tests  COMMAND meshcore_tests crypto)
add_test(NAME sha256_tests  COMMAND meshcore_tests sha256)
add_test(NAME hmac_tests    COMMAND meshcore_tests hmac)
add_test(NAME aes_tests     COMMAND meshcore_tests aes128)
add_test(NAME ecdh_tests    COMMAND meshcore_tests ecdh)
add_test(NAME ed25519_tests COMMAND meshcore_tests ed25519)
add_test(NAME packet_tests  COMMAND meshcore_tests packet)
add_test(NAME tables_tests  COMMAND meshcore_tests tables)
