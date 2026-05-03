# GlassBox Hackathon Demo Script (3-5 min)

## Goal
Show judges a complete "detect -> explain -> verify" security workflow, with hardware as the differentiator.

---

## 0) Pre-Demo Setup (before judges arrive)
- Start stack (`backend`, `frontend`, `ragserver`) --> run bash ./run-all.sh
- Open frontend at `http://localhost:5173`.
- Keep one terminal visible with backend logs.
- Keep one sample target repo ready (preferably with at least 1 obvious issue).
- Optional: have hardware pod connected for live confirmation.

---

## 1) 20-sec Pitch
Use this line:

"Most scanners stop at static warnings. GlassBox goes further: we detect security issues, explain them in context, and for C/C++ side-channel risk, we can confirm leakage on real hardware."

---

## 2) Workflow Demo (Main Path)

### Step A: Start an audit (45-60 sec)
1. Paste repo URL/path in UI.
2. Start audit.
3. Narrate:
   - "We ingest the repo and run multiple security checks."
   - "Findings stream into a triage dashboard with severity and code context."

### Step B: Show findings + prioritization (45-60 sec)
1. Open a HIGH/CRITICAL finding.
2. Point out file, snippet, and remediation guidance.
3. Narrate:
   - "This is actionable, not just a lint warning. We show what, where, and what to do next."

### Step C: Ask the agent (60 sec)
1. Ask: "What should we fix first and why?"
2. Ask: "Show me similar risky patterns in this repo."
3. Narrate:
   - "The agent uses retrieval over indexed findings and code, so answers are grounded in this repo."

### Step D: Hardware differentiator (60-90 sec)
1. If hardware is available:
   - Trigger hardware confirmation path for side-channel candidate.
   - Narrate: "Now we validate on silicon, not just static pattern matching."
2. If hardware is not available:
   - Show hardware workflow panel/log flow and explain expected output.
   - Narrate: "The same flow runs with connected pod during full deployment."

---

## 3) Architecture Soundbite (20 sec)
"Frontend for triage, Go backend orchestration, Python RAG server with FAISS for semantic retrieval, and optional hardware loop for real-world side-channel confirmation."

---

## 4) Judge-Friendly Value Summary (20 sec)
- Faster triage
- Better fix guidance
- Evidence-backed validation (hardware)
- Strong demo-to-production path

---

## 5) Backup Plan (if live API/hardware slows down)
- Use pre-seeded run in UI and replay results.
- Use visualizer with seeded embeddings.
- Focus narrative on end-to-end workflow and differentiator.

---

## Optional Q&A Answers

### "What makes this different from existing scanners?"
"Closed-loop validation. We don't just flag patterns; we can validate side-channel behavior on real hardware."

### "How does the agent avoid hallucinations?"
"It is tool-grounded: retrieval from indexed findings/code and structured tool outputs."

### "Can this scale to other repos?"
"Yes. The flow is repo-agnostic: clone, index, scan, retrieve, explain."

