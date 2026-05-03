"""cpa.py -- Correlation Power Analysis (CPA) on AES S-box outputs.

Given:
  * (n, m) power traces (n traces, m samples per trace)
  * (n, 16) plaintext bytes that were fed to the function under test

CPA recovers each AES-128 round-key byte independently by:

  1. For each candidate key byte k in 0..255:
       compute hypothesis H_k[i] = HW(SBOX[plaintext[i] XOR k])
       (Hamming-weight of the first-round S-box output for trace i)
  2. For each sample s in the trace:
       compute Pearson correlation between H_k[:] and trace[:, s]
  3. Take max |correlation| across samples -> score_k
  4. The byte whose score is highest is the recovered key byte.

If the runner knows the true key (in our pipeline it always does -- we set
the secret in firmware), we can ALSO report `true_rank`: where the actual
key byte ranks among the 256 candidates by score. rank=1 means the attack
recovered exactly that byte; ranks <=10 mean a few more traces would
finish the job.

This module is **pure** (numpy only). It does not know about parquet,
serial ports, or the harness protocol. eval.py wires it in.

Limitations:
  * Hamming-weight model only -- works on most ESP32 bus widths, not on
    chips with strong dual-rail logic.
  * AES-128 first-round S-box only. To attack the last round (works for
    decryption / known-ciphertext), pass mode="decrypt" -- TODO.
  * 16 key bytes is hard-coded. For other key lengths pass key_bytes=N.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np


# Standard AES S-box.
AES_SBOX = np.array([
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
], dtype=np.uint8)

# Hamming weight of every byte 0..255.
HW = np.array([bin(x).count("1") for x in range(256)], dtype=np.float64)


@dataclass
class CpaByteResult:
    byte_index: int
    best_guess: int
    correlation: float        # max |rho| at best_guess
    true_rank: Optional[int]  # 1 = recovered, None = true key not provided
    # Top-5 scoring candidates (for debugging / UI).
    top5: List[int]


@dataclass
class CpaReport:
    n_traces: int
    n_samples: int
    per_byte: List[CpaByteResult]
    full_key_recovered: bool
    # Top-1 guesses concatenated (None if not all bytes attempted).
    recovered_key: Optional[bytes]


# =============================================================================
# Core math
# =============================================================================

def _vectorized_corr(H: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Pearson correlation between every column of H (shape (n, K)) and every
    column of T (shape (n, m)). Result shape: (K, m).

    Uses centered sums to keep memory in O(K*m) rather than O(n*K*m).
    """
    H = H.astype(np.float64)
    T = T.astype(np.float64)
    n = H.shape[0]
    Hc = H - H.mean(axis=0, keepdims=True)
    Tc = T - T.mean(axis=0, keepdims=True)
    # Covariance: (K, m) = (K, n) @ (n, m)
    cov = (Hc.T @ Tc) / n
    sH = Hc.std(axis=0, ddof=0)            # (K,)
    sT = Tc.std(axis=0, ddof=0)            # (m,)
    denom = np.outer(sH, sT)               # (K, m)
    denom[denom == 0] = np.inf             # avoid div-by-zero on flat columns
    return cov / denom


def attack_byte(plaintexts: np.ndarray, traces: np.ndarray,
                byte_index: int,
                true_key_byte: Optional[int] = None) -> CpaByteResult:
    """Run CPA on one key byte.

    Args:
        plaintexts: (n, key_bytes) uint8.
        traces:     (n, m) float-like power samples.
        byte_index: which key byte to recover (0..key_bytes-1).
        true_key_byte: if known, populates true_rank in the result.
    """
    n, key_bytes = plaintexts.shape
    if not (0 <= byte_index < key_bytes):
        raise ValueError(f"byte_index {byte_index} out of range")
    pt = plaintexts[:, byte_index].astype(np.uint8)

    # Hypothesis matrix: (n, 256) -- HW(SBOX[pt[i] XOR k]) for k in 0..255.
    candidates = np.arange(256, dtype=np.uint8)
    sbox_out = AES_SBOX[(pt[:, None] ^ candidates[None, :])]   # (n, 256)
    H = HW[sbox_out]                                            # (n, 256)

    rho = _vectorized_corr(H, traces)                           # (256, m)
    score = np.max(np.abs(rho), axis=1)                         # (256,)
    best = int(np.argmax(score))
    top5 = list(np.argsort(score)[-5:][::-1].tolist())

    rank: Optional[int]
    if true_key_byte is not None:
        order = np.argsort(-score)
        rank = int(np.where(order == int(true_key_byte))[0][0]) + 1
    else:
        rank = None

    return CpaByteResult(
        byte_index=byte_index,
        best_guess=best,
        correlation=float(score[best]),
        true_rank=rank,
        top5=[int(x) for x in top5],
    )