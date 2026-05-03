"""pipeline/ -- cross-cutting glue between collect, analyze, and the host.

Owns the canonical Finding schema (findings.py) and orchestration helpers.
Pure Python; no hardware, no numpy beyond what flows through Findings.
"""
