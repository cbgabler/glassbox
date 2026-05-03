# GlassBox Workflow Gaps (Not Fully Implemented Yet)

- [X] Implement automated **repo intake + clone** stage (git URL/path handling, workspace setup, run_id assignment).
- [ ] Implement a single **end-to-end scanner orchestrator pipeline** that runs all scanners as one workflow and emits unified progress/events. (partial: agent tools and hardware audit flow exist, but no single unified run controller yet)
- [ ] Fully wire **hardware confirmation loop** into orchestrator:
  - finding -> hardware test trigger -> captured evidence -> attach back to finding.
- [ ] Implement deterministic **finding/evidence schema contract** between scanners, hardware results, and agent triage. (partial: hardware findings schema exists; cross-system canonical mapping still needed)
- [ ] Implement **patch generation workflow** from validated findings (candidate diff generation + rationale + file/line references). (partial: chat can describe fixes; no structured diff pipeline yet)
- [ ] Implement **verification gates** before fix acceptance (tests/lint/build/security checks per repo type).
- [ ] Implement **Git automation** for fixes (branch create, commit, push, metadata linking to findings).
- [ ] Implement **PR creation automation** (title/body template, evidence summary, risk notes, reviewer assignment hooks).
- [ ] Add **failure/retry handling** for long-running audit steps (timeouts, partial failures, resumable runs).
- [ ] Add **operator safety controls** for auto-fix mode (dry-run, approval checkpoint, scope limits by severity/scanner).
- [ ] Resolve config portability issues (remove machine-specific absolute paths in server configs).
