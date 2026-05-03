// byte_compare.cpp -- intentionally vulnerable demo target for GlassBox.
//
// Implements an early-exit byte comparator: bails on the first mismatch.
// That makes execution time scale with the length of the matching prefix,
// which is observable both as a timing side-channel (CPU cycles) and a
// power-trace side-channel on the ESP32. ct_lint flags the early-exit
// pattern (CT001/CT002), and the TVLA + CPA stages of scan_target.py
// will measure the leak on real silicon.
//
// The harness wraps this via register_synthetic_target's comparator_len
// shape: every secret it sees is compared against the static reference
// constant `kReference`. The agent registers reference_hex='676c617373626f78'
// so the auto-campaign picker uses match_vs_random against that value.

#include <stddef.h>
#include <stdint.h>

// Reference constant the comparator checks against. ASCII "glassbox".
// scan_target.py / ct_lint extracts this automatically via
// `suggested_reference_hex` so the operator doesn't have to retype it.
static const uint8_t kReference[8] = {
    0x67, 0x6c, 0x61, 0x73, 0x73, 0x62, 0x6f, 0x78,
};

// Returns 0 on full match, 1 on first mismatch. Early-exit makes timing
// depend on the length of the matching prefix -- the textbook string
// compare side-channel.
int byte_compare(const uint8_t* secret,
                 const uint8_t* reference,
                 size_t len) {
    for (size_t i = 0; i < len; ++i) {
        if (secret[i] != reference[i]) {
            return 1;
        }
    }
    return 0;
}
