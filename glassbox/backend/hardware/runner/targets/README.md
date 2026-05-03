# GlassBox target examples

Drop-in `gb_target.cpp` implementations you can copy on top of
`backend/hardware/esp/harness/gb_target.cpp` (or the Raspberry harness)
to exercise the runner against known-leaky and known-safe primitives.

Each pair illustrates one well-known side-channel anti-pattern and its
constant-time fix. TVLA / CPA / the ML classifier should flag the
`*_leaky.cpp` variant and clear the `*_safe.cpp` variant.

| Pair                    | Anti-pattern (leaky)                          | Fix (safe)                                  |
| ----------------------- | --------------------------------------------- | ------------------------------------------- |
| `strcmp_*`              | Early-return byte compare                     | XOR-accumulate all bytes, branchless        |
| `password_check_*`      | Auth wrapper around a leaky compare           | Constant-time compare + uniform reject path |
| `lookup_table_*`        | Secret-indexed table access (cache timing)    | Scan all entries with masked select         |
| `branch_on_secret_*`    | `if (secret_bit) do_one() else do_other()`    | Branchless conditional select               |

## Usage

```sh
cp targets/strcmp_leaky.cpp ../esp/harness/gb_target.cpp
# re-flash the harness, then:
python -m runner.analyze.eval --target-name strcmp_leaky
```
