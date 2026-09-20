# Running SecureDocs

Everything needed to get the demo up, and an honest note on which parts
are real.

> **Just want to run it?** Double-click `start.bat` (Windows) or run
> `./start.sh`, then open **http://localhost:5000**. One command, one
> URL, no IDE. See **DEPLOY.md** for that and for putting it online.
>
> The rest of this file is the manual setup - useful when something is
> wrong, or when you are developing.

## 1. Database

```bash
createdb sih_dms

cd backend
npm install
npm run db:setup          # schema + every migration
node scripts/seed.js      # demo users and cases
```

`db:setup` is safe to re-run: it skips the schema once the tables exist
and every migration is idempotent.

**Not psql.** `psql -U postgres` authenticates as the postgres OS user
and on Windows it stops and waits for a password, so it looks like it has
frozen. `db:setup` goes through the same `DATABASE_URL` the app uses.

## 2. Backend

`backend/.env`:

```
DATABASE_URL=postgres://postgres:postgres@localhost:5432/sih_dms

# 64 hex characters. Generate: openssl rand -hex 32
# Lose this and every stored document is unreadable - that is the point.
MASTER_KEY=<64 hex chars>

PORT=5000
CORS_ORIGIN=http://localhost:5173

# stub = in-memory, runs anywhere, proves nothing
# fabric = the real ledger, see fabric/README.md
LEDGER_BACKEND=stub

# Leave false until you have looked at real redacted output yourself.
# Verified working for text/*; images and PDFs refuse either way.
REDACTION_ENABLED=false

# disk = local filesystem (default), minio = S3 WORM object store
STORAGE_BACKEND=disk

# Only read when STORAGE_BACKEND=minio
# MINIO_ENDPOINT=http://localhost:9000
# MINIO_ACCESS_KEY=minioadmin
# MINIO_SECRET_KEY=minioadmin
# MINIO_BUCKET=securedocs

# OCR languages. Only add one you have run a sample through.
OCR_LANGS=eng+hin
```

```bash
cd backend
npm install
npm test        # 39 checks, no database or server needed
npm run dev
```

The startup log prints which ledger backend is live and whether
redaction is on. Check both before demoing.

## 2b. OCR worker

Text recognition runs in a separate process so an upload returns
immediately instead of waiting seconds for OCR:

```bash
cd backend
npm run worker
```

It polls for `ocr_status='pending'`, decrypts through the storage layer
**in memory only**, recognises the text, extracts entities, and writes
`extracted_text` + `entities` back. Without it running, uploads stay
`pending` and search finds nothing.

First run downloads ~15MB of Tesseract language data per language.

## 2c. Object storage (optional)

```bash
docker run -p 9000:9000 -p 9001:9001 minio/minio server /data --console-address :9001
cd backend && npm run init:minio      # creates the bucket WITH Object Lock
```

Then set `STORAGE_BACKEND=minio`. Object Lock must be enabled at bucket
creation — it cannot be added later, so a bucket made by hand without it
has to be recreated.

## 3. Frontend

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173
```

Set `VITE_API_URL` if the backend is not on `localhost:5000/api/v1`. To
work without a backend at all:

```bash
npx @stoplight/prism mock openapi.yaml
VITE_API_URL=http://127.0.0.1:4010 npm run dev
```

## 4. Before you demo: preflight

```bash
cd backend && npm run preflight
```

One command that answers "will this work if I start it now". It checks
the database, the migrations, the master key, the seed data, the OCR
queue, and whether an unassigned officer exists for the closing beat.
FAIL blocks the demo. WARN still runs but changes what you can honestly
claim while standing in front of people.

## 5. The demo

```bash
cd backend && ./demo.sh
```

Uploads a document, verifies it, issues the S.63 certificate, searches,
shows the ICJS payload, corrupts the blob on disk, verifies again
(TAMPERED), and shows the certificate now refusing with 409.

The ninety-second story, in the UI:

1. Log in as an inspector — password, then MFA
2. Open a case, upload an FIR
3. Document view — the hash and its ledger transaction id
4. Download — watermarked; on a protected case, redacted
5. Corrupt the stored blob directly on disk
6. Verify — **TAMPERED**, full width, both hashes side by side
7. Audit trail — every action, hash-linked, chain intact
8. Log in as an unassigned constable — the case is not there at all

Step 8 is the strongest and the one most teams forget. Correct rank,
valid login, still nothing.

## What is real, and what is not

| Area | State |
|---|---|
| Auth, MFA, sessions, RBAC∩ABAC | Real |
| Envelope encryption, tamper detection | Real |
| Hash-linked audit log | Real, append-only enforced in Postgres |
| Versioning, integrity verification | Real |
| BSA S.63 certificate | Real |
| Export watermarking | Real, PDF only, visible (not steganographic) |
| Search | Real, full-text over OCR output |
| OCR | Real, images and **PDF** (text layer, or rendered and recognised). Tesseract WASM, **English and Hindi tested — do not claim 22 languages** |
| Entity extraction | Real, **rules and document structure, not an NER model** |
| Object storage | Real MinIO WORM path; **runs on local disk unless `STORAGE_BACKEND=minio`** |
| Ledger | Real chaincode; **runs on the in-memory stub unless `LEDGER_BACKEND=fabric`** |
| Redaction | Real for `text/*` and **PDF**; refuses images rather than half-redacting |
| ICJS | **Mock.** Fixtures. Every response says `"mock": true` |
| DSC / eSign | **Stub.** Correct interface, throwaway ECDSA key, not a legal signature |
| PWA (F12) | **Not built** |

Say these out loud when demonstrating. Every one of them is defensible
as an engineering decision; none of them survives being oversold.

## Known limits worth stating before someone finds them

- **Redaction covers text files and PDFs. Images still refuse.** A PDF
  is redacted by rendering each page, painting out the identified areas,
  and rebuilding the document from those images — so the released file
  has no text layer at all and copy-pasting from it returns nothing.
  The classic failure (a black box with the words still live underneath)
  is avoided by construction rather than by care. Images refuse because
  OCR returns the words but not their coordinates, so there is nothing
  to draw a box around; a 503 beats a half-redaction.
- **A protected document is not released until it has been analysed.**
  Most of what redaction removes comes from entity extraction, so
  releasing before the worker has finished would strip the phone number
  and leave every name. Both release paths refuse while `ocr_status` is
  anything but `done`.
- **OCR is tested on English and Hindi only.** Adding a scheduled
  language is a traineddata file and an `OCR_LANGS` change, but claiming
  a language nobody has run a sample through is how a demo falls apart
  in front of someone who speaks it.
- **Entity extraction has no model in it.** It is regex plus the
  formulaic structure of an FIR ("complainant X", "d/o Y", "r/o Z").
  That is auditable — you can show a judge the rule that fired — but it
  will miss a name introduced in an unusual phrasing. Redaction treats
  it as a safety net beneath the identities an officer registers, never
  as the only source.
- **Automatic name detection needs two words on one line.** A name must
  follow its role word on the same line, and have at least a given and a
  family name. Both rules exist because a real 34-page FIR form
  otherwise yielded "No Delay", "Signature" and "Major" as people — the
  pattern was stepping over line breaks into the form's own answers, and
  redaction blacked out the furniture while leaving the actual names.
  **For a protected case, register the victim on the case rather than
  relying on detection.** That path takes any name, including one word.
- **Search snippets are never shown for a protected case**, regardless
  of the redaction flag. `ts_headline` cuts fragments through names, so
  a fragment-level redactor could leave half an identity behind.
- **Anchoring is fire-and-forget.** A ledger outage marks the version
  `anchor_status='failed'` instead of failing the upload, and nothing
  retries it yet.
- **The DSC keypair is per-process**, so signatures do not survive a
  restart.
