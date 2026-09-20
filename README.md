# SecureDocs Platform

Two systems that ship as one:

| Part | What it does | Stack |
|---|---|---|
| **SecureDocs** (`backend/`, `frontend/`) | Evidence and case management: encrypted storage, versions that never overwrite, mandatory redaction on protected cases, a tamper-evident audit trail, and a hash anchored to a ledger for every file. | Node 20 + Express 5, React 19 + Vite, PostgreSQL 14+ |
| **Screening** (`screening/`) | Fake identity and document screening: MRZ reading and check digits, printed-field comparison, forgery and stamp-copy detection, face verification, watchlist and risk scoring. | Python 3.11 + FastAPI, PyTorch (CPU), OpenCV, Tesseract |

They are joined at one seam. When an officer files an **identity document**
as evidence, SecureDocs sends the image to the screening service and puts
the verdict on that case's audit trail - alerting every officer on the
case when it is anything but CLEAR. Everything else in either system works
exactly as it did standalone.

```
officer files evidence (doc_type: identity_document)
        |
        v
SecureDocs  --- POST /api/v1/screen --->  screening service
        |                                       |
        |<------ disposition + risk score ------+
        v
case audit trail  +  alert for every officer on the case
```

This file is the handover document. `RUNNING.md` covers running the demo,
`DEPLOY.md` covers hosting the evidence system, `screening/README.md`
covers the screening service on its own, and **`openapi.yaml` is the
contract for every SecureDocs endpoint** - change it in the same commit as
the route.

---

## 1. Run it locally

### Everything at once, with Docker

```bash
docker compose up --build
```

That starts PostgreSQL, MinIO, the evidence system, the OCR worker and the
screening service, wired together and seeded:

- http://localhost:5000 - the evidence system (sign in with the table below)
- http://localhost:8000 - the screening service's own console and `/docs`

The first build downloads PyTorch and takes several minutes; later starts
are quick. Data lives in named volumes, so stopping and starting loses
nothing.

### From source

You need Node 20+ and PostgreSQL 14+ (tested on 18). For the screening
service you also need Python 3.11+ and Tesseract on the PATH.

```bash
cd backend
cp .env.example .env          # then set MASTER_KEY and DATABASE_URL
npm install
npm run db:setup              # schema + every migration, safe to re-run
npm run seed                  # test officers, two cases
npm run seed:scan             # a scanned FIR so search has something to find
```

```bash
cd frontend
npm install
npm run build                 # the backend then serves the screens itself
```

```bash
cd backend
npm start                     # http://localhost:5000
```

`MASTER_KEY` must be 64 hex characters. Generate one with
`node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`.
**It decrypts every stored document. Lose it and every file is gone for
good.**

The screening service runs separately:

```bash
cd screening
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --port 8000
```

Then point the evidence system at it by setting `SCREENING_URL=http://localhost:8000`
in `backend/.env` and restarting it. Left unset, identity documents are
filed exactly as before and recorded as not screened - the seam is
optional, never required.

For frontend work run `npm run dev` in `frontend/` instead (port 5173,
proxies `/api` to 5000) so you get hot reload.

### Signing in

Seeded officers all use password `Test@1234`. Sign-in needs service number,
password **and** the registered mobile number:

| Service number | Mobile | Rank | Notes |
|---|---|---|---|
| DL-INS-1001 | 0000000001 | inspector | can create cases, assign officers |
| DL-SI-2002 | 0000000002 | sub_inspector | can upload, cannot assign |
| DL-CON-3003 | 0000000003 | constable | assigned to nothing - use to demo refusal |
| DL-PRO-4004 | 0000000004 | prosecutor | read and verify only |
| DL-FSL-5005 | 0000000005 | forensic_analyst | lab role |

In development there is no real SMS: the code prints in the server's
terminal (`SMS_PROVIDER=console`). Those numbers are deliberately not real
Indian mobile numbers, so pointing a live gateway at the seed data cannot
text a stranger.

### The scripts worth knowing

| Command | What it does |
|---|---|
| `npm run db:setup` | Creates the schema and applies migrations. Idempotent. |
| `npm run seed` / `seed:scan` | Rebuilds test data. **Wipes documents and the audit log.** |
| `npm start` / `npm run dev` | The API plus the built screens. |
| `npm run worker` | OCR worker as its own process (or `RUN_WORKER_INLINE=true`). |
| `npm test` | Unit tests. No database, no server. |
| `npm run test:integration` | Needs a running server and the real database. |
| `npm run tamper -- <doc-id>` | Corrupts one stored file, for the tamper demo. Run twice to undo. |
| `npm run integrity:check` | Runs the scheduled integrity sweep once, now. |
| `npm run preflight` | Checks the environment before a demo. |

---

## 2. How it is put together

Express 5 + PostgreSQL on the backend, React 19 + Vite on the frontend. One
process serves both in production. No ORM - SQL is written out, always with
parameters.

```
backend/
  server.js            wiring: CORS, routes, static frontend, error handler,
                       and the startup log that says which backends are live
  db.js                the pool. query() and transaction() - the ONLY way in
  db/schema.sql        base schema
  db/migrations/       001.. applied in filename order by db:setup
  middleware/
    auth.js            requireAuth: who is this? (session token -> req.user)
    policy.js          can this? RBAC by rank + ABAC by case assignment
  routes/              auth, cases, documents, notifications, users, icjs
  services/            one concern each - see below
  worker/ocr-worker.js reads scans, extracts entities, never touches disk
  scripts/             setup, seeding, demo helpers
  test/                services.test.js (pure), integration.test.js (live)
frontend/src/
  api/client.js        every call to the backend. One function per endpoint
  auth.jsx             AuthProvider + RequireAuth
  components/Layout.jsx  shell: sidebar, search, integrity alert banners
  pages/               one file per screen
```

### The services

| Service | Responsibility |
|---|---|
| `storage.js` | Envelope encryption (AES-256-GCM per file, key wrapped with `MASTER_KEY`) and the object store (local disk or MinIO). Plaintext never hits disk. |
| `crypto.js` | Passwords (scrypt), session tokens, TOTP, OTP generation and hashing. Node built-ins only. |
| `sms.js` | Sign-in codes and tamper alerts. Twilio, or a console provider for development. |
| `audit.js` | The hash-chained audit log: `append()` and `verifyChain()`. |
| `integrity.js` | `checkIntegrity()` (one file vs its hash and the ledger) and the 20-minute sweep. |
| `ledger.js` | Facade over `ledger-stub.js` (in-memory) and `ledger-fabric.js` (Hyperledger Fabric). |
| `ocr.js`, `entities.js`, `pdf.js` | Reading scans, and pulling names/addresses/sections out of the text. |
| `redaction.js`, `watermark.js` | Removing protected identities, and stamping who pulled a copy. |
| `certificate.js`, `dsc.js` | The BSA s.63(4) certificate and its signature. |
| `icjs.js` | ICJS/CCTNS exchange. **Mock** - every response says `mock: true`. |

---

## 3. The data model

```
users --< sessions              cases --< case_assignments >-- users
  |                               |
  +--< otp_challenges             +--< case_protected_identities
                                  +--< documents --< document_versions
                                  +--< audit_log >-- users
                                       notifications
```

- **`users`** - officers. `rank` drives permissions; `mobile_number` is
  E.164 and unique; `password_hash` is scrypt; `mfa_secret` is the
  authenticator seed.
- **`cases`** - `case_number` (e.g. `FIR/0142/2026`), `status`,
  `sensitivity` (`normal` / `restricted` / `protected`).
- **`case_assignments`** - **this table is the access control.** No row, no
  access, whatever the rank.
- **`documents`** - one exhibit. `evidence_number` (`FIR-001`, `STM-002`) is
  assigned by a database trigger, unique within the case, and immutable
  along with the case, type and creator. `title` is an optional
  description that defaults to the evidence number.
- **`document_versions`** - the actual files. Never overwritten: a new
  upload is a new row. Holds the hash, the wrapped key, OCR output,
  extracted entities, the ledger anchor and the integrity check state.
- **`audit_log`** - append-only, enforced by database RULEs, each row
  carrying the hash of the row before it.
- **`notifications`** - officer inbox for integrity alerts.

### Invariants you must not break

These are the point of the system. Read this list before changing anything
in `routes/` or `db/`.

1. **`policy.js` is the only place that decides access.** Two independent
   checks, both must pass: rank permits the action, and the officer is
   assigned to the case. Rank is never a bypass.
2. **Identity comes from the session, never the request.** The backend
   reads `req.user.id`. A body field like `created_by` is ignored - there
   is a test that sends one and asserts it is.
3. **Nothing is overwritten.** New file means new version row.
4. **Every case or evidence change is audited in the same transaction as
   the change**, so evidence cannot exist without a record of who filed it.
   Pass the client: `audit.append({...}, client)`.
5. **The audit log is append-only.** Updates and deletes are silently
   discarded by database rules. Only the seeding script may lift that, and
   only to wipe test data.
6. **On a protected case, refuse rather than release.** If redaction is
   unavailable, the analysis is incomplete, or the file type cannot be
   redacted safely, return 503. Never fall back to the original.
7. **Protected identities are never exported, logged, or put in an audit
   detail** - the audit records *that* one was registered, never its value.
8. **Sensitivity only goes up.** Lowering it would switch off redaction on
   evidence already released under it.
9. **Search must never reveal a document in a case you are not assigned
   to**, and never returns a text snippet from a protected case.
10. **Plaintext never touches disk.** Decrypt in memory, hand the buffer on.

---

## 4. Authentication

Two paths. Both end in an opaque session token (32 random bytes, only its
SHA-256 stored, deleting the row revokes it instantly - that is why these
are not JWTs). Sessions last 8 hours.

**Mobile OTP (what the screens use):**

```
service number + password + registered mobile
        |  POST /auth/otp/request
        v
  all three must match the same active officer
        |  6-digit code by SMS, otp_token in the response
        v
  otp_token + code   POST /auth/otp/verify
        v
  session token
```

Only an HMAC of the code is stored (keyed with `MASTER_KEY`, bound to the
challenge id), codes expire in 5 minutes, allow 5 attempts, work once, and
are limited to 3 per officer per 15 minutes and 60 seconds apart. A wrong
password and someone else's phone number get the same answer. Verification
is keyed on `otp_token`, not the phone number, so a stranger cannot spend
an officer's attempts.

**Password + authenticator app** (`/auth/login` then `/auth/mfa/verify`)
still exists for tooling and the test suite. The account must have an
authenticator enrolled, and the challenge dies after 5 wrong codes.

`GET /api/v1/me` returns the officer plus `permissions` - what their rank
allows. The screens use it to decide which controls to show; the server
checks again on every call regardless.

Permissions by rank live in `policy.js`: `case.view`, `case.create`,
`case.update`, `case.assign`, `document.view`, `document.download`,
`document.upload`, `document.new_version`, `document.verify`.

---

## 5. Notes for the AI / ML team

Your work sits in `services/ocr.js`, `services/entities.js` and
`worker/ocr-worker.js`, and is consumed by `services/redaction.js`.

**The pipeline.** Upload returns immediately with `ocr_status='pending'`.
The worker claims a batch in a single `UPDATE ... RETURNING` (so two
workers are safe), decrypts in memory, reads the file - text as-is, PDFs
via their text layer, images via Tesseract - runs entity extraction, and
writes back `extracted_text`, `entities` and `ocr_status='done'`. A stuck
claim is reaped after `OCR_STALE_MINUTES`.

**The contract for `document_versions.entities`:**

```json
{
  "persons":   ["..."],
  "addresses": ["..."],
  "phones":    ["..."],
  "fir_numbers": ["..."],
  "sections":  ["..."],
  "dates":     ["..."],
  "confidence": 91,
  "skew": 0,
  "source": "ocr"
}
```

`persons`, `addresses` and `phones` feed redaction and are removed.
`fir_numbers`, `sections` and `dates` are evidence and are deliberately
kept - redacting the evidence out of the evidence is worse than not
redacting at all. Keep those key names.

**Where to plug in a better model.** `entities.extract(text)` is a rules
layer today. A real NER model replaces or augments that one function - keep
the return shape and everything downstream keeps working. `entities.js`
has no database or HTTP dependency, so it is unit-testable in isolation
(`npm test` covers it).

**Rules that bind your code:**

- Never write plaintext to a temp file, not even "just for Tesseract".
- The officer-supplied protected identity list (`case_protected_identities`)
  is merged with your output, never replaced by it. A human naming the
  victim beats inference.
- On a protected case, incomplete analysis blocks release. If
  `ocr_status !== 'done'`, the download is refused - partial redaction that
  looks complete is the worst outcome available.
- Report confidence honestly. The document screen shows it.
- `OCR_LANGS` (default `eng+hin`) - only add a language you have tested
  against a real sample.

---

## 6. Notes for the frontend team

Six screens are routed in `App.jsx`: Login, CaseList, CaseDetail,
DocumentView, Verify, AuditTimeline. Everything except Login sits behind
`RequireAuth`, and a 401 anywhere drops the token and returns to Login.

`Dashboard.jsx`, `Documents.jsx`, `Search.jsx`, `Upload.jsx` and
`Users.jsx` exist but are **not routed** - they are work in progress. Wire
them up in `App.jsx` when they are ready.

**Conventions:**

- **Never call `fetch` from a component.** Every endpoint gets one function
  in `api/client.js`. That file is also the only place the `Authorization`
  header is attached, and the only place the token is read.
- The session token lives in `sessionStorage`, so it dies with the tab -
  deliberate, for shared station machines.
- Show or hide controls with `user.permissions.includes("case.assign")`
  from `/me`. Never re-implement the rank table in the frontend.
- Binary downloads go through `downloadFile()`, which fetches with the auth
  header and hands the browser a blob. A plain `<a href>` cannot work.
- Styles are plain CSS: `index.css` (base) and `styles/app.css` (app). No
  framework. Keep controls inside containers that wrap rather than pushing
  the page sideways.
- Never render server text as HTML. Search snippets come from OCR of an
  uploaded file; `plainSnippet()` strips the markup for that reason.

**Things that will surprise you:**

- A document's identifier is `evidence_number`. `title` is an optional
  description, and equals the evidence number when the officer gave none -
  which is why the screens print the title only when it differs.
- **503 is not a bug.** On a protected case the API refuses to release a
  document when redaction is unavailable or the analysis is unfinished.
  Show the message; do not retry.
- **409 on verify** means the document does not currently verify, so no
  certificate can be issued.
- Integrity alerts appear as banners above every screen, polled every 60
  seconds from `GET /notifications`. Dismissing marks the notification read
  on the server.
- A case's audit trail is part of the case screen and reloads after every
  change, so an officer sees their own action land on the record.

---

## 7. The scheduled integrity check

Every 20 minutes, and once at startup, the server re-verifies every version
of every document in cases whose status is `under_investigation`: decrypt,
recompute the hash, compare with the hash recorded at upload and with the
ledger.

When a file's state changes, the check writes a system entry to the case's
audit trail and alerts every officer on the case - in the app and by SMS.
It alerts on the change, not on every sweep: one alert when a file starts
failing, one when it verifies again.

It is careful about false alarms, and that matters more than catching a
tamper 20 minutes sooner: storage that cannot be reached is skipped and
retried, a file whose ledger anchor is still pending is only checked
against its upload hash, and the sweep does not run at all if the ledger
could not be loaded at startup.

Demo it with `npm run tamper -- <doc-id>` then `npm run integrity:check`.

---

## 7a. The screening seam

`backend/services/screening.js` is the whole of the SecureDocs side.

- **When it runs.** Only for `doc_type: identity_document`, and only
  after the upload has committed - on the first version and on every
  replacement version. It is never awaited on the request path, so a slow
  or unavailable screening service cannot fail or delay an officer's
  upload.
- **What it sends.** The document bytes as multipart to
  `POST /api/v1/screen`, plus the officer's id. Nothing else.
- **What it records.** `document_versions.screening_status` (`pending`,
  `done`, `failed`, `skipped`), the full verdict in `screening_result`,
  and a `screening` entry on the case's audit trail carrying only the
  disposition, risk score, risk band, document type and the number of
  findings - never the image or the holder's name, because the trail is
  readable by everyone on the case.
- **Who is told.** Any disposition other than `CLEAR` raises an in-app
  alert for every officer on the case, the same banner the tamper check
  uses. `RECAPTURE` counts as flagged: on evidence already filed, "too
  poor to judge" is a finding, not a request to retake the photo.
- **When it fails.** The version is marked `failed`, so an unscreened
  document is never mistaken for a clean one. Nothing is retried
  automatically; re-upload as a new version to screen again.

The four dispositions come from the screening service:
`CLEAR`, `SECONDARY_INSPECTION`, `REFER_TO_SUPERVISOR`, `RECAPTURE`.

## 8. Configuration

Everything is environment variables; `backend/.env.example` is the
annotated list. The ones that change behaviour:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | - | Required. |
| `MASTER_KEY` | - | Required, 64 hex chars. Decrypts every document. |
| `SMS_PROVIDER` | none | `console` (development) or `twilio`. **No default**: until set, no sign-in code can be sent. `console` refuses when `NODE_ENV=production`. |
| `TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_FROM` | - | For `twilio`. Indian numbers need a DLT-registered sender. |
| `TRUST_PROXY_HOPS` | `0` | Reverse proxies in front of the app. Decides `req.ip`, which the rate limits count. Set `1` behind Render/nginx. Never more hops than exist. |
| `STORAGE_BACKEND` | `disk` | `minio` for anything real - a cloud host wipes local disk on restart. |
| `LEDGER_BACKEND` | `stub` | `stub` is in-memory and proves nothing. `fabric` is the real two-org network (`fabric/README.md`). |
| `REDACTION_ENABLED` | `false` | Leave off until you have looked at real redacted output. Protected cases refuse to export either way. |
| `RUN_WORKER_INLINE` | `false` | Runs OCR inside the web process, for hosts with no separate worker. |
| `OCR_LANGS` | `eng+hin` | Tesseract languages. |
| `CORS_ORIGIN` | `http://localhost:5173` | Only needed when the screens are served from another origin. |
| `SCREENING_URL` | none | Where the screening service lives, e.g. `http://localhost:8000`. Unset means identity documents are filed but not screened. |
| `SCREENING_TIMEOUT_MS` | `60000` | How long to wait for a verdict. The first request after a cold start also loads the models. |
| `SCREENING_DATA_DIR` | `/data` | Screening service only: where its watchlist and audit database live. |

---

## 9. Testing

```bash
npm test
```

```bash
npm run test:integration
```

The unit suite (52 checks, no database or network) covers the pure logic:
entity extraction, redaction, watermarking, the certificate, the audit
payload hash, mobile number normalisation, OTP hashing, and the integrity
classifier.

The integration suite (21 checks, needs `npm start` running) covers what
only breaks once the pieces are wired together, and every test in it is
there because something was once wrong: protected-case search leaks, the
assignment check, audit chain integrity across interleaved documents, the
whole OTP flow (wrong code, replay, exhausted, expired, attacks through the
phone number), the legacy authenticator path, the sensitivity ratchet, a
full case lifecycle with attribution, concurrent evidence numbering, the
tamper sweep with its alerts, and the database-level guarantees.

**It writes to whatever database `.env` points at** - it creates `TEST/...`
cases, and their audit rows cannot be deleted afterwards. Do not point it
at anything real. It also spends 5 of the 10-per-15-minutes per-IP
sign-in-code allowance, so more than two runs in that window needs a server
restart.

---

## 9a. Deploying it

Whatever the target, three things must be true: the two services can
reach each other, PostgreSQL is real and backed up, and `MASTER_KEY`
survives restarts. Losing that key makes every stored document
permanently unreadable.

### One box with Docker (simplest)

```bash
git clone <this repo> && cd securedocs-platform
export MASTER_KEY=$(node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")
docker compose up --build -d
```

Put a reverse proxy (nginx, Caddy) in front of port 5000 with TLS, and do
**not** expose port 8000 publicly - the screening service has no
authentication of its own and is meant to be reachable only from the
evidence system. Then set `TRUST_PROXY_HOPS=1` so the sign-in rate limits
count the real client address.

Before anything real, change from the demo defaults:
`MASTER_KEY` (generate your own), the PostgreSQL password, the MinIO
credentials, `SMS_PROVIDER=twilio` with its credentials, and
`NODE_ENV=production`.

### Split hosting (what the free tiers allow)

The evidence system fits a small web host; the screening service needs
about 2 GB of RAM and 61 MB of models, which rules out most free tiers.

| Piece | Where | Notes |
|---|---|---|
| PostgreSQL | Render, Neon, Supabase, RDS | Managed. Take backups. |
| Object storage | MinIO or S3 | `STORAGE_BACKEND=minio`. A host with an ephemeral disk will lose files, and those documents then fail verification. |
| SecureDocs | Render (`render.yaml` is included), Railway, a VM | Set `DATABASE_URL`, `MASTER_KEY`, `SCREENING_URL`, `SMS_PROVIDER`, `TRUST_PROXY_HOPS=1`. |
| Screening | A VM or container host with >=2 GB RAM | `screening/Dockerfile` builds it. Keep it on a private network. |

The screening service holds no case data - it screens an image and
answers - so it can sit anywhere the evidence system can reach it over
HTTPS.

### After deploying

```bash
npm run db:setup      # creates the schema and applies every migration
npm run seed          # ONLY on a demo instance: it wipes documents and the audit log
npm run preflight     # checks the environment before a demo
```

Then confirm: sign in, file an identity document, and check the case
audit trail shows a `screening` entry. `GET /health` on port 8000 reports
whether the screening models loaded.

## 10. Known gaps

Honest list, roughly by how likely each is to bite.

- **No officer administration.** Accounts and mobile numbers exist only via
  `npm run seed`. Registering a real officer, or changing a phone number,
  needs a screen and an endpoint - and an officer whose number is missing
  cannot sign in at all.
- **The stub ledger proves nothing.** The same process that stores the hash
  vouches for it, and it re-reads those hashes from the same database on
  restart. Only `LEDGER_BACKEND=fabric` is a real second opinion.
- **Rate limits for sign-in codes are per process and in memory.** Two
  server processes mean two independent allowances. Move them to Redis or a
  table before running more than one.
- **`verifyChain()` rehashes the whole audit log.** Cached for 30 seconds,
  but still O(all rows) per check. Verify forward from a stored known-good
  checkpoint when the log gets large.
- **No evidence metadata editing.** A new version is the only way to change
  a document, which is deliberate, but there is no way to correct a
  description or reclassify a type.
- **Opening a case is not audited**, only opening a document.
- **`otp_challenges` rows are never purged.**
- **An alert SMS is attempted once**; a failure is logged, not retried. The
  in-app alert is the reliable one.
- **Audit `detail` can carry an officer-typed description**, so a careless
  upload description on a protected case ends up in a trail that everyone
  on the case can read.
- **ICJS is a mock.** Do not remove the `mock: true` flag to make a demo
  look better.
- The console SMS provider prints codes to stdout, which is why it refuses
  to run in production.

From joining the two systems:

- **The screening service has no authentication.** Anything that can
  reach port 8000 can screen a document or read its watchlist. Keep it on
  a private network, behind the evidence system.
- **A failed screening is not retried.** The version is marked `failed`
  and stays that way until someone files a new version.
- **The screening service's own audit log is separate** from the case
  audit trail. The trail records the verdict; the service records the
  screening.
- **Only `identity_document` uploads are screened.** Filing a passport
  photograph as `other` skips screening entirely.
- **Two databases.** PostgreSQL for the evidence system, the screening
  service's own store for its watchlist and audit. They are not joined,
  and nothing reconciles them.
- **The 49-page Word document** describing the screening system
  (`docs/Project_Documentation.docx`) was never pushed from the machine it
  was written on, so it is not in this repository.

---

## 11. Where to look first

| Question | File |
|---|---|
| What does this endpoint return? | `openapi.yaml` |
| Who is allowed to do this? | `backend/middleware/policy.js` |
| How is a document encrypted? | `backend/services/storage.js` |
| Why is this audit entry shaped like that? | `backend/services/audit.js` |
| What does the database enforce? | `backend/db/schema.sql`, then `db/migrations/` |
| How does the frontend call the API? | `frontend/src/api/client.js` |
| How does screening decide? | `screening/src/risk.py`, `screening/src/validation.py` |
| How is the seam wired? | `backend/services/screening.js` |
| How do I run the demo? | `RUNNING.md` |
| How do I host it? | `DEPLOY.md` |

The code is commented with the reasoning, not the mechanics - most comments
explain why something is the way it is, usually because the obvious
alternative was tried and was wrong.
