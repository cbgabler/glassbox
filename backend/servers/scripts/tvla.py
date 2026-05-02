"""TVLA -- Test Vector Leakage Assessment.

Implements the standard non-specific TVLA test (Welch's two-sample t-test)
used by every cryptographic side-channel evaluation lab in the world (NIST,
Riscure, NewAE, etc.).

Hypothesis test:
    H0:  the function's measurable execution profile is independent of secret
    H1:  the profile depends on the secret  (i.e. it leaks)

Procedure (this file):
    1. Collect two groups of N traces each from the same function:
         - Group A: secret = fixed (typically all-zero)
         - Group B: secret = random per call
       (or any other two distributions you want to distinguish.)
    2. For each measurement axis (cycles is one scalar; the power trace
       is M sample points), compute Welch's t-statistic between the two
       groups.
    3. If |t| > 4.5 anywhere, the function is leaking with > 99.999%
       confidence (Bonferroni-corrected for typical M ~ 1000 sample points).

The 4.5 threshold comes from ISO/IEC 17825 and is the de facto industry
standard. Lower thresholds (e.g. 3.5) are sometimes used for pre-screening.
"""
