// tests/main.cpp
// Entry point for the meshcore test suite.
//
// Optional argv[1]: substring filter — only run tests whose name contains it.
//   ./meshcore_tests             # run all tests
//   ./meshcore_tests sha256      # run only SHA-256 tests
//   ./meshcore_tests ecdh        # run only ECDH tests
//   ./meshcore_tests packet      # run only packet tests

#include "test_runner.h"
#include <stdio.h>

int main(int argc, char* argv[]) {
    const char* filter = (argc > 1) ? argv[1] : nullptr;

    if (filter)
        printf("Running tests matching: \"%s\"\n\n", filter);
    else
        printf("Running all tests\n\n");

    int failures = run_tests(filter);
    return failures == 0 ? 0 : 1;
}
