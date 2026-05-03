# glassbox-demo

End-to-end demo repository for [GlassBox](https://github.com/cbgabler/glassbox).
Every file in this repo is **intentionally vulnerable** so a single audit
run can exercise every GlassBox feature and produce findings across all
five scanner categories.

> Do not deploy any of this code. The credentials, tokens, and
> dependency versions here are real-looking but disarmed (AWS docs
> example keys, fake Stripe / GitHub token shapes). They exist to match
> scanner regexes, not to grant access to anything.

---

## How to run a full demo

```bash
# 1. Start the GlassBox backend
cd glassbox/backend
./build-all.sh
./main.exe

# 2. From the frontend (or curl), point at this repo
curl -X POST http://localhost:8080/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Audit https://github.com/<you>/glassbox-demo"}'

# 3. After the agent reports the registered synthetic target:
curl -X POST http://localhost:8080/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"audit it"}'
```

If you want git-history secrets to be picked up, run `seed-git-history.sh`
after `git init` and before pushing (see that script for instructions).

---

## What each file is for

| File | Scanner | Severity | Finding |
|------|---------|----------|---------|
| `byte_compare.cpp` | Side-channel (hardware) | CRITICAL | Early-exit byte compare; registered as synthetic target, confirmed via TVLA on ESP32 |
| `backend/auth.js` | Side-channel (software) | CRITICAL | `verifyToken` early-exit `!==`, `checkPassword` plain `===` |
| `backend/server.js` | Exposed endpoints | HIGH | `0.0.0.0` bind, `cors({origin:'*'})`, returns `password`/`api_key`, dumps `process.env`, echoes stack traces |
| `backend/server.js` | Secrets | HIGH | `JWT_SECRET` and Stripe-shaped `sk_live_…` literal in source |
| `backend/dbconfig.js` | Secrets | HIGH | Hardcoded production DB password |
| `backend/package.json` | Dependencies | HIGH/MEDIUM | `jsonwebtoken@8.5.1` (CVE-2022-23529), `lodash@4.17.20` (CVE-2021-23337), `axios@0.21.0` (CVE-2021-3749), `express@4.16.0` (multiple), `ws@7.4.0` (CVE-2024-37890) |
| `scripts/deploy.py` | Secrets | HIGH | AWS access key + secret pair (AWS docs example values, real shape) |
| `scripts/requirements.txt` | Dependencies | HIGH/MEDIUM | `pyyaml==5.1` (CVE-2020-1747), `requests==2.20.0` (CVE-2018-18074), `urllib3==1.24.1` (CVE-2019-11324), `flask==0.12.2` (CVE-2018-1000656) |
| `.github/workflows/deploy.yml` | Secrets | HIGH | Hardcoded `ghp_…` GitHub PAT and inline API key in env |
| `seed-git-history.sh` | Git history secrets | HIGH | Adds + removes `.env.production` so the secret survives in history |

---

## Expected GlassBox flow

1. **Repo-init turn** (user pastes the URL):
   - `clone_repo` → `get_repo_context` → `list_hardware_targets`
   - `list_hardware_targets` returns `count: 0` (Branch B)
   - `get_file_contents` on `byte_compare.cpp` → `register_synthetic_target`
     with shape `comparator_len`, reference `glassbox` (hex `676c617373626f78`)
   - Chat: "registered `byte_compare`, say **audit it** to flash"

2. **"audit it" turn**:
   - `start_hardware_audit` → poll `get_hardware_audit_status`
   - Reads high-signal files: `backend/server.js`, `backend/auth.js`,
     `backend/dbconfig.js`, `backend/package.json`, `scripts/deploy.py`,
     `scripts/requirements.txt`, `.github/workflows/deploy.yml`
   - Emits one `<<code>>` block per finding (≥ 8 expected)
   - Persists summary via `add_memory_note`

3. **Follow-up turn** (no tools):
   - User asks "what should I fix first?" — agent reasons across
     categories using chat history only.

---

## Severity coverage

| Severity | Count | Examples |
|----------|-------|----------|
| CRITICAL | 3+    | hardware side-channel, JS token compare, JS password compare |
| HIGH     | 6+    | DB password, AWS keys, GitHub PAT, JWT secret, exposed admin route, env dump |
| MEDIUM   | 4+    | older `express`, older `axios`, older `urllib3`, older `flask` |
| LOW      | 1+    | CORS wildcard (best-practice violation on its own; HIGH when paired with the leaky routes) |
