#pragma once
// Minimal Arduino.h shim for compiling MeshCore helpers (BaseChatMesh etc.)
// on POSIX.  Only provides the types and macros that the helpers actually use.

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

// Arduino's ltoa (used by TxtDataHelpers.cpp _ftoa).
inline char* ltoa(long value, char* str, int base) {
    if (base == 10) {
        sprintf(str, "%ld", value);
    } else if (base == 16) {
        sprintf(str, "%lx", value);
    } else {
        str[0] = '\0';
    }
    return str;
}
