# Final Checks Plan

## 1) Implement failure/retry handling for long-running audit steps
- Define a shared run-state model (`queued`, `running`, `retrying`, `failed`, `done`, `cancelled`).
- Add per-step timeout + retry policy (max attempts, backoff, retryable error classes).
- Persist attempt count, last error, and current step so runs are resumable.
- Expose retry/status fields to frontend and add a manual "Retry failed step" action.

## 2) ChatPanel processing hints while Claude is running
- Add staged processing hint messages in chat (shown one-by-one until response is ready).
- Start hint sequence immediately on submit; stop and clear on response/error.
- Prefer backend phase-driven hints when available; fallback to timed generic hints.
- Ensure timers are cleaned up on unmount and rapid re-prompts.

## 3) Plan PR automation after patch details are shown in UI
- Add backend endpoint to create PR from selected patch candidate.
- Implement git automation flow: branch -> apply patch -> commit -> push.
- Add pre-PR verification gates (build/test/lint/security checks).
- Auto-generate PR title/body with finding IDs + severity + hardware evidence summary.
- Return PR URL/status to UI and store in run artifacts.

## 4) Create a Final readme in the root direcotry - explaining the broad overview:
- Problem/Benefit
- Solution (our project)
- What it does, how it works
- Tech stack
- Tools Used
- Workflow diagram
- How to run it (frontend+backend+rag server)
- What API keys are required
