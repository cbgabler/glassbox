"""ct_lint.py -- pre-flash constant-time linter for gb_target.cpp.

Scans a C / C++ source file for syntactic patterns that almost always cause
side-channel leaks, BEFORE the user wastes minutes flashing + sweeping.
Intentionally a fast regex-based linter -- not a full clang AST tool --
because the universe of dangerous patterns inside a 50-line `gb_target.cpp`
is small and well known.

What it catches (rule_id : pattern):

    CT001  branch on secret           if/while/for whose condition reads the
                                      argument named `secret` (or `key`, etc.)
    CT002  variable-time comparator   strcmp / memcmp / strncmp / bcmp /
                                      strcasecmp on the secret buffer
    CT003  secret-indexed table read  arr[secret_byte] / table[secret[i]]
    CT004  variable-time arithmetic   `secret % N` / `secret / N` /
                                      `1u << secret_bit`
    CT006  unmasked secret print/log  Serial.print(secret) / printf(... secret)
                                      (oracle via debug output)
    CT007  yield during compute       Serial.* / delay() / yield() inside
                                      the function under test (corrupts
                                      timing measurements + may leak)

Each rule emits a Finding via `pipeline.findings.build_static_finding()`.

CLI:
    python -m runner.analyze.ct_lint path/to/gb_target.cpp

Suppress a hit by adding `// CT-OK: <reason>` to the offending line.

Rules are deliberately conservative -- false positives are MUCH cheaper
than false negatives. The user reads the warning and either fixes it or
adds `// CT-OK`.

Adapted from cs370/runner/ct_lint.py.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import re
import sys
from typing import Iterable, List, Optional, Set, Tuple

from pipeline import findings as fmod


# =============================================================================
# Rule definitions
# =============================================================================

# Identifiers we treat as "the secret" -- anything reading these is suspect.
SECRET_NAMES = (
    r"secret|key|password|passwd|pwd|token|nonce|msg|in|input"
)
SECRET_RE = rf"\b(?:{SECRET_NAMES})\b"


def _expand_secret_re(extra_names: List[str]) -> str:
    """Build a regex matching any of SECRET_NAMES + the extra (taint-derived)
    aliases found in this specific file."""
    if not extra_names:
        return SECRET_RE
    extras = "|".join(re.escape(n) for n in extra_names)
    return rf"\b(?:{SECRET_NAMES}|{extras})\b"


# Comparator names that early-exit on the first mismatch.
LEAKY_COMPARATORS_RE = (
    r"\b(?:strcmp|strncmp|memcmp|bcmp|strcasecmp|strncasecmp)\s*\("
)

# Calls that yield, take >tens of us, or print/log -- corrupt the timed window.
YIELD_RE = (
    r"\b(?:delay|delayMicroseconds|yield|vTaskDelay|esp_random|"
    r"Serial\.(?:print|println|printf|write|read)|"
    r"WiFi\.|HTTPClient|Serial2\.)"
)


@dataclasses.dataclass
class LintHit:
    rule_id: str
    severity: str
    line: int
    col: int
    message: str
    excerpt: str
    remediation: Optional[str]
    # Optional campaign hint for scan_target.py's --campaign auto path.
    # Populated by lint_source() when the file shows a comparator-shaped
    # leak (CT001/CT002) AND we can extract an unambiguous static
    # reference constant from the source. None means "no hint".
    suggested_campaign: Optional[str] = None
    suggested_reference_hex: Optional[str] = None


# =============================================================================
# Pre-processing -- strip comments and string literals so regex matches
# don't false-positive on doc-strings or string-literal contents.
# We replace each comment / string with the same number of spaces so column
# numbers are still accurate.
# =============================================================================

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE  = re.compile(r"//[^\n]*")
_STRING_RE        = re.compile(r'"(?:\\.|[^"\\])*"')
_CHAR_RE          = re.compile(r"'(?:\\.|[^'\\])'")


def _blank_match(m: "re.Match[str]") -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


def _strip_for_lint(src: str) -> str:
    src = _BLOCK_COMMENT_RE.sub(_blank_match, src)
    src = _LINE_COMMENT_RE.sub(_blank_match, src)
    src = _STRING_RE.sub(_blank_match, src)
    src = _CHAR_RE.sub(_blank_match, src)
    return src


def _strip_comments_only(src: str) -> str:
    """Like `_strip_for_lint` but PRESERVES string and char literals --
    needed by `extract_reference_constant`, which has to read those tokens
    to recover their bytes."""
    src = _BLOCK_COMMENT_RE.sub(_blank_match, src)
    src = _LINE_COMMENT_RE.sub(_blank_match, src)
    return src


def _line_col(src: str, idx: int) -> Tuple[int, int]:
    """1-indexed (line, col) for a character index into src."""
    line = src.count("\n", 0, idx) + 1
    last_nl = src.rfind("\n", 0, idx)
    col = idx - last_nl                           # 1-indexed
    return line, col


def _excerpt(src: str, line: int) -> str:
    """One-line excerpt at `line` for the finding payload."""
    lines = src.splitlines()
    if not (1 <= line <= len(lines)):
        return ""
    return lines[line - 1].rstrip()


def _is_suppressed(raw_src: str, line: int) -> bool:
    """Honor `// CT-OK: ...` on the offending line."""
    lines = raw_src.splitlines()
    if not (1 <= line <= len(lines)):
        return False
    return "CT-OK" in lines[line - 1]


# Taint propagation: very lightweight. For each assignment of the form
#   `<type?> <name> = <expr-containing-a-secret-name>`
# we add <name> to the file's secret-alias set. One pass; no fixed-point.
_ALIAS_DECL_RE = re.compile(
    rf"\b(?:[A-Za-z_][A-Za-z0-9_<>:\s\*&]*\s+)?"
    rf"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]*?{SECRET_RE}[^;]*);"
)

# Identifiers we should NOT treat as taint sinks (loop counters, common ints).
_ALIAS_DENYLIST = {
    "i", "j", "k", "n", "len", "size", "ret", "rc", "out_len", "out_n",
}


def _collect_aliases(src: str) -> List[str]:
    """Find local-variable names assigned a secret-derived expression.

    Conservative: one-shot scan, no loop fixed-point. Catches the common
    `uint8_t b = secret[i];` and `int x = secret_len - i;` shapes.
    """
    names: List[str] = []
    seen = set()
    for m in _ALIAS_DECL_RE.finditer(src):
        name = m.group(1)
        if name in _ALIAS_DENYLIST or name in seen:
            continue
        if re.fullmatch(SECRET_RE, name):
            continue
        seen.add(name)
        names.append(name)
    return names


# =============================================================================
# Rule implementations
# =============================================================================

def _rule_CT001(src: str, secret_re: str) -> Iterable[LintHit]:
    """Branch on secret: if/while/for whose CONDITION reads a secret-name."""
    pattern = re.compile(
        rf"\b(if|while|for)\s*\(([^)]*{secret_re}[^)]*)\)",
        flags=re.DOTALL,
    )
    for m in pattern.finditer(src):
        # Filter the trivial "iterate from 0 to msg_len" form -- that's a
        # length-loop, not a branch on the secret value.
        cond = m.group(2)
        if re.match(rf"^\s*\w+\s*<\s*{secret_re}_len\s*$", cond):
            continue
        if re.match(rf"^\s*\w+\s*<\s*{secret_re}\s*$", cond):
            continue
        line, col = _line_col(src, m.start())
        yield LintHit(
            rule_id="CT001", severity="HIGH",
            line=line, col=col,
            message=f"`{m.group(1)}` condition reads a secret-named value",
            excerpt=_excerpt(src, line),
            remediation=("Replace the conditional with a constant-time mask: "
                         "compute both branches and select with "
                         "`(mask & a) | (~mask & b)`."),
        )


def _rule_CT002(src: str, secret_re: str) -> Iterable[LintHit]:
    """Variable-time comparator on the secret buffer."""
    pattern = re.compile(LEAKY_COMPARATORS_RE)
    for m in pattern.finditer(src):
        line, col = _line_col(src, m.start())
        yield LintHit(
            rule_id="CT002", severity="HIGH",
            line=line, col=col,
            message="variable-time comparator (early-exits on first mismatch)",
            excerpt=_excerpt(src, line),
            remediation=("Use a constant-time equality primitive "
                         "(`CRYPTO_memcmp`, `sodium_memcmp`, or write a "
                         "loop that XORs every byte and ORs the result)."),
        )


def _rule_CT003(src: str, secret_re: str) -> Iterable[LintHit]:
    """Secret-indexed table read: `something[secret]`, `something[secret[i]]`."""
    pattern = re.compile(
        rf"\b([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*([^]]*{secret_re}[^]]*)\]"
    )
    for m in pattern.finditer(src):
        ident = m.group(1)
        if re.fullmatch(secret_re, ident):
            # `secret[i]` is a read of the secret, not a table lookup.
            continue
        line, col = _line_col(src, m.start())
        yield LintHit(
            rule_id="CT003", severity="HIGH",
            line=line, col=col,
            message=f"table `{ident}[]` indexed by a secret-derived value",
            excerpt=_excerpt(src, line),
            remediation=("Replace the lookup with a scan-and-mask: walk every "
                         "entry, compare the index in constant time, "
                         "conditional-move the matching value."),
        )


def _rule_CT004(src: str, secret_re: str) -> Iterable[LintHit]:
    """Variable-time arithmetic on secret: %, /, or shift by secret."""
    patterns = [
        re.compile(rf"{secret_re}\s*%\s*\w+"),
        re.compile(rf"{secret_re}\s*/\s*\w+"),
        re.compile(rf"\w+\s*<<\s*{secret_re}"),
        re.compile(rf"\w+\s*>>\s*{secret_re}"),
    ]
    for p in patterns:
        for m in p.finditer(src):
            line, col = _line_col(src, m.start())
            yield LintHit(
                rule_id="CT004", severity="MEDIUM",
                line=line, col=col,
                message="variable-time arithmetic on a secret-named value",
                excerpt=_excerpt(src, line),
                remediation=("Avoid integer division, modulo, or variable "
                             "shifts whose right operand is a secret. Most "
                             "MCUs implement these as variable-cycle "
                             "instructions or software loops -- both leak."),
            )


def _rule_CT006(src: str, secret_re: str) -> Iterable[LintHit]:
    """Print / log statement that emits the secret -- oracle via debug output."""
    pattern = re.compile(
        rf"\b(?:Serial\.(?:print|println|printf|write)|printf|fprintf|puts)\s*"
        rf"\([^;]*{secret_re}",
        flags=re.DOTALL,
    )
    for m in pattern.finditer(src):
        line, col = _line_col(src, m.start())
        yield LintHit(
            rule_id="CT006", severity="HIGH",
            line=line, col=col,
            message="debug print emits a secret-named value",
            excerpt=_excerpt(src, line),
            remediation=("Never log the secret; if you need debug output, "
                         "log a constant-time digest (e.g. SHA-256) of the "
                         "secret instead."),
        )


def _rule_CT007(src: str, secret_re: str) -> Iterable[LintHit]:
    """Yield / blocking call inside the timed function -- corrupts measurement."""
    pattern = re.compile(YIELD_RE)
    for m in pattern.finditer(src):
        line, col = _line_col(src, m.start())
        yield LintHit(
            rule_id="CT007", severity="MEDIUM",
            line=line, col=col,
            message="yield/IO/log call inside the function under test",
            excerpt=_excerpt(src, line),
            remediation=("Anything that yields the CPU corrupts the cycle/"
                         "power measurement. Move logging or delays out of "
                         "the timed window."),
        )


_RULES = [_rule_CT001, _rule_CT002, _rule_CT003, _rule_CT004,
          _rule_CT006, _rule_CT007]


# =============================================================================
# Reference-constant extraction
#
# When a file's CT001/CT002 hit is a byte-compare against a STATIC reference
# (the textbook strcmp/memcmp leak shape), we want scan_target.py to be able
# to pick `match_vs_random` automatically with the right reference bytes.
# random_vs_zero is structurally blind to that leak: both all-zero and
# uniform-random inputs early-exit at byte 0 of the comparator with very
# high probability, so the cycles distributions are identical.
#
# We try to recover a single, unambiguous static byte sequence from the
# source. Supported shapes (all `static const ...`):
#
#     uint8_t    NAME[] = { 0x67, 0x6c, ... };          // hex literals
#     uint8_t    NAME[] = { 'g', 'l', 'a', ... };       // char literals
#     char       NAME[] = "glassbox";                   // string literal
#     char *     NAME   = "glassbox";                   // string-literal ptr
#
# We deliberately DO NOT extract when there are zero or >=2 matching
# constants in the file -- ambiguity would let us pick the wrong reference
# and pretend the result is reliable when it isn't. In that case the
# scanner falls through to the safety net (a MEDIUM "inconclusive" finding
# in scan_target.py) instead of silently false-negativing.
# =============================================================================

# Length window that's plausibly a comparator reference. Tighter than the
# theoretical max so we don't grab e.g. a 256-byte AES S-box.
_REFERENCE_MIN_LEN = 4
_REFERENCE_MAX_LEN = 32

# Match `static const <type> [*] NAME [N]? = <init>;`. <init> can be a
# braced byte list OR a "string literal". Tolerant of `unsigned char`.
_STATIC_CONST_RE = re.compile(
    r"\bstatic\s+const\s+"
    r"(?:unsigned\s+)?(?:uint8_t|u8|char)\s*\*?\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?:\[\s*\d*\s*\])?"
    r"\s*=\s*(\{[^}]*\}|\"(?:\\.|[^\"\\])*\")\s*;",
    re.DOTALL,
)


def _parse_braced_bytes(body: str) -> Optional[bytes]:
    """Parse `{0x67, 0x6c, 'a', 100, ...}` into bytes. Returns None on any
    token we don't recognize -- conservative on purpose."""
    inner = body.strip()
    if not (inner.startswith("{") and inner.endswith("}")):
        return None
    inner = inner[1:-1].strip()
    if not inner:
        return None
    out = bytearray()
    for raw in inner.split(","):
        tok = raw.strip()
        if not tok:
            continue
        if tok.startswith("'") and tok.endswith("'") and len(tok) >= 3:
            # 'g' or '\n' style char literal. Use Python's literal_eval
            # for the escape handling so we don't reinvent it.
            try:
                import ast
                ch = ast.literal_eval('"' + tok[1:-1].replace('"', '\\"') + '"')
                if isinstance(ch, str) and len(ch) == 1:
                    out.append(ord(ch))
                    continue
            except Exception:
                return None
            return None
        try:
            n = int(tok, 0)               # base 0 = honor 0x / 0o / 0b prefix
        except ValueError:
            return None
        if not (0 <= n <= 0xFF):
            return None
        out.append(n)
    return bytes(out) if out else None


def _parse_string_literal(body: str) -> Optional[bytes]:
    """Parse `"glassbox"` (with C escapes) into raw bytes."""
    s = body.strip()
    if not (s.startswith('"') and s.endswith('"')):
        return None
    try:
        # Python string literals use the same backslash escapes for the
        # ones we care about (\n, \t, \xNN, \\). Good enough for a heuristic.
        decoded = bytes(s[1:-1], "utf-8").decode("unicode_escape")
    except Exception:
        return None
    try:
        return decoded.encode("latin-1")
    except UnicodeEncodeError:
        return None


def extract_reference_constant(src: str) -> Optional[bytes]:
    """If the source contains exactly ONE static-const byte array / string
    literal in the plausible reference-length window, return its bytes.
    Otherwise return None (zero matches, multiple matches, or unparseable)."""
    # Use _strip_comments_only -- the regular `_strip_for_lint` BLANKS
    # string and char literals, which is exactly what we need to read here.
    cleaned = _strip_comments_only(src)
    candidates: List[bytes] = []
    seen: set[bytes] = set()
    for m in _STATIC_CONST_RE.finditer(cleaned):
        body = m.group(2)
        parsed = (_parse_string_literal(body)
                  if body.startswith('"') else _parse_braced_bytes(body))
        if parsed is None:
            continue
        if not (_REFERENCE_MIN_LEN <= len(parsed) <= _REFERENCE_MAX_LEN):
            continue
        if parsed in seen:
            # Same constant defined twice (e.g. through a header) -- still
            # unambiguous, count it once.
            continue
        seen.add(parsed)
        candidates.append(parsed)
        if len(candidates) > 1:
            return None                    # ambiguous -- bail
    return candidates[0] if len(candidates) == 1 else None


# Rules whose finding directly indicates a byte-compare-shaped leak that
# random_vs_zero will systematically miss. Used to gate the auto-suggestion.
_COMPARATOR_RULE_IDS = {"CT001", "CT002"}


# =============================================================================
# Quote-form `#include "..."` following
#
# Generated wrappers (notably hardwarego/register_synthetic_target's
# comparator_len shape) `#include "/abs/path/to/original.cpp"` so the
# wrapper file itself contains only glue and metadata, not the leaky loop.
# Without following the include, ct_lint sees no comparator pattern in
# the file it was handed -- CT001/CT002 stay silent, no
# `suggested_campaign='match_vs_random'` is emitted, and scan_target.py's
# auto picker falls back to `random_vs_zero`, which is structurally
# blind to early-exit comparator leaks. The end-to-end effect is a
# false negative: a textbook leaky strcmp-shape gets reported as safe.
#
# We follow only `"..."` (quote-form) includes -- `<...>` are system
# headers we don't lint. Cycle-safe via a `_seen` set in lint_file.
# =============================================================================

_INCLUDE_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.MULTILINE)

_LINTABLE_INCLUDE_EXTS = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inc", ".ino",
})


def _resolve_local_includes(path: str, src: str) -> List[str]:
    """Return absolute paths of `#include "..."` targets that resolve to a
    real, lintable source file. Paths are tried both verbatim (absolute) and
    relative to the including file's directory. `<...>` system includes are
    skipped. Order is preserved (file order in `src`); duplicates removed.
    """
    base_dir = os.path.dirname(os.path.abspath(path))
    seen: Set[str] = set()
    out: List[str] = []
    for m in _INCLUDE_RE.finditer(src):
        inc = m.group(1).strip()
        if not inc:
            continue
        cand = inc if os.path.isabs(inc) else os.path.join(base_dir, inc)
        cand = os.path.normpath(cand)
        if cand in seen:
            continue
        if not os.path.isfile(cand):
            continue
        ext = os.path.splitext(cand)[1].lower()
        if ext not in _LINTABLE_INCLUDE_EXTS:
            continue
        seen.add(cand)
        out.append(cand)
    return out


# =============================================================================
# Public API
# =============================================================================

def lint_source(src: str, file: str = "<source>") -> List[LintHit]:
    """Return all unsuppressed hits for the given source string.

    If a comparator-shaped hit (CT001/CT002) fires AND we can recover a
    single unambiguous static reference constant from the file, those hits
    carry a `suggested_campaign='match_vs_random'` + `suggested_reference_hex`
    so scan_target.py's --campaign auto can pick the right campaign without
    operator input.
    """
    cleaned = _strip_for_lint(src)
    aliases = _collect_aliases(cleaned)
    secret_re = _expand_secret_re(aliases)
    out: List[LintHit] = []
    seen = set()  # de-dup (rule, line, col)
    for rule in _RULES:
        for hit in rule(cleaned, secret_re):
            if _is_suppressed(src, hit.line):
                continue
            key = (hit.rule_id, hit.line, hit.col)
            if key in seen:
                continue
            seen.add(key)
            out.append(hit)
    out.sort(key=lambda h: (h.line, h.col, h.rule_id))

    # Tag comparator hits with the auto-campaign hint when we can resolve
    # an unambiguous reference. Done at the end so it sees the full hit
    # list (and only fires when there's something for it to help).
    if any(h.rule_id in _COMPARATOR_RULE_IDS for h in out):
        ref = extract_reference_constant(src)
        if ref is not None:
            ref_hex = ref.hex()
            for h in out:
                if h.rule_id in _COMPARATOR_RULE_IDS:
                    h.suggested_campaign = "match_vs_random"
                    h.suggested_reference_hex = ref_hex
    return out


def lint_file(path: str, id_offset: int = 0,
              _seen: Optional[Set[str]] = None) -> List[fmod.Finding]:
    """Lint a file and return Finding objects (ready for run_detail.json).

    Quote-form `#include "..."` directives that resolve to a real source
    file are followed and linted as well, so generated wrappers stay
    transparent to the rule set. `_seen` makes the recursion cycle-safe
    (a file lints itself once per call regardless of include geometry).
    """
    abs_path = os.path.abspath(path)
    if _seen is None:
        _seen = set()
    if abs_path in _seen:
        return []
    _seen.add(abs_path)

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError as e:
        # Don't blow up the whole pipeline if one source is unreadable.
        # Caller (scan_target.py) treats an empty list as "lint clean".
        sys.stderr.write(f"[ct_lint] WARN: cannot read {abs_path}: {e}\n")
        return []

    out: List[fmod.Finding] = []
    for h in lint_source(src, abs_path):
        fid = f"f_{id_offset + len(out) + 1:03d}"
        out.append(fmod.build_static_finding(
            fid,
            rule_id=h.rule_id, severity=h.severity,
            file=abs_path, line=h.line, col=h.col,
            message=h.message, excerpt=h.excerpt,
            remediation=h.remediation,
            suggested_campaign=h.suggested_campaign,
            suggested_reference_hex=h.suggested_reference_hex,
        ))

    for inc_path in _resolve_local_includes(abs_path, src):
        out.extend(lint_file(inc_path,
                             id_offset=id_offset + len(out),
                             _seen=_seen))
    return out


# =============================================================================
# CLI
# =============================================================================

def _format_hit(h: LintHit, file: str) -> str:
    return (f"{file}:{h.line}:{h.col}: [{h.rule_id} {h.severity}] "
            f"{h.message}\n    {h.excerpt}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("path", help="C/C++ source file to lint (e.g. gb_target.cpp)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on ANY hit (default: only on >=MEDIUM)")
    args = ap.parse_args()

    if not os.path.isfile(args.path):
        print(f"ct_lint: {args.path}: not a file", file=sys.stderr)
        return 2

    with open(args.path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    hits = lint_source(src, args.path)

    if not hits:
        print(f"ct_lint: {args.path}: no findings")
        return 0

    print(f"ct_lint: {len(hits)} finding(s) in {args.path}")
    for h in hits:
        print(_format_hit(h, args.path))

    bad = [h for h in hits if h.severity in ("CRITICAL", "HIGH", "MEDIUM")]
    if args.strict and hits:
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
