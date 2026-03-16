#pragma once
// Minimal self-contained test framework.
//
// Usage:
//   TEST(my_test) { EXPECT(1 + 1 == 2); EXPECT_EQ(foo(), 42); }
//
// Call run_tests() from main(); it returns the number of failures.

#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <functional>
#include <vector>
#include <string>

struct TestCase {
    const char*           name;
    std::function<void()> fn;
};

// Function-local static: guaranteed initialised before first use, no ODR issues.
inline std::vector<TestCase>& test_registry() {
    static std::vector<TestCase> v;
    return v;
}

// Per-test failure tracking (reset by run_tests before each test).
inline int& g_fail_count()  { static int n = 0; return n; }
inline int& g_check_count() { static int n = 0; return n; }
inline const char*& g_current_test() { static const char* s = ""; return s; }

// Hex-dump helper used in failure messages.
inline void _hex_print(const uint8_t* b, size_t n) {
    for (size_t i = 0; i < n; i++) fprintf(stderr, "%02x", b[i]);
}

// ---- Registration -------------------------------------------------------

// Each TEST(name){} expands to:
//   1. A forward declaration of the test function.
//   2. A file-scoped struct whose constructor registers it.
//   3. The function definition.
#define TEST(name)                                                          \
    static void _test_fn_##name();                                         \
    namespace { struct _test_reg_##name {                                  \
        _test_reg_##name() { test_registry().push_back({#name, _test_fn_##name}); } \
    } _test_reg_inst_##name; }                                             \
    static void _test_fn_##name()

// ---- Assertion macros ---------------------------------------------------

#define EXPECT(cond)                                                        \
    do {                                                                    \
        g_check_count()++;                                                  \
        if (!(cond)) {                                                      \
            fprintf(stderr, "  FAIL  [%s] line %d: %s\n",                 \
                    g_current_test(), __LINE__, #cond);                    \
            g_fail_count()++;                                               \
        }                                                                   \
    } while (0)

#define EXPECT_EQ(a, b)                                                     \
    do {                                                                    \
        g_check_count()++;                                                  \
        auto _va = (a); auto _vb = (b);                                    \
        if (_va != _vb) {                                                   \
            fprintf(stderr, "  FAIL  [%s] line %d: (%s) != (%s)\n",       \
                    g_current_test(), __LINE__, #a, #b);                   \
            g_fail_count()++;                                               \
        }                                                                   \
    } while (0)

// Compare n bytes; print a hex dump of both sides on failure.
#define EXPECT_BYTES_EQ(got, want, n)                                       \
    do {                                                                    \
        g_check_count()++;                                                  \
        if (memcmp((got), (want), (n)) != 0) {                             \
            fprintf(stderr, "  FAIL  [%s] line %d: %s\n"                  \
                            "        got : ", g_current_test(), __LINE__, #got); \
            _hex_print((const uint8_t*)(got),  (n));                       \
            fprintf(stderr, "\n        want: ");                           \
            _hex_print((const uint8_t*)(want), (n));                       \
            fprintf(stderr, "\n");                                         \
            g_fail_count()++;                                               \
        }                                                                   \
    } while (0)

// Assert two byte arrays are NOT equal.
#define EXPECT_BYTES_NE(a, b, n)                                            \
    do {                                                                    \
        g_check_count()++;                                                  \
        if (memcmp((a), (b), (n)) == 0) {                                  \
            fprintf(stderr, "  FAIL  [%s] line %d: expected %s != %s but they are equal\n", \
                    g_current_test(), __LINE__, #a, #b);                   \
            g_fail_count()++;                                               \
        }                                                                   \
    } while (0)

// ---- Runner -------------------------------------------------------------

inline int run_tests(const char* filter = nullptr) {
    int passed = 0, failed = 0;
    g_check_count() = 0;

    for (auto& tc : test_registry()) {
        if (filter && std::string(tc.name).find(filter) == std::string::npos)
            continue;

        g_current_test() = tc.name;
        int prev = g_fail_count();
        tc.fn();

        if (g_fail_count() == prev) {
            printf("  PASS  %s\n", tc.name);
            passed++;
        } else {
            failed++;
        }
    }

    printf("\n%d passed, %d failed  (%d checks)\n", passed, failed, g_check_count());
    return failed;
}
