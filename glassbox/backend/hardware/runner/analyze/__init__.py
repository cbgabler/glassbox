"""analyze/ -- pure-numpy analysis layer.

Takes traces (parquet or arrays) in, emits Findings out. No serial I/O,
no subprocess calls. Submodules:
    tvla     Welch's t-test (first + second order)
    cpa      Correlation Power Analysis (AES S-box recovery)
    ct_lint  Pre-flash regex linter for C/C++ targets
"""
