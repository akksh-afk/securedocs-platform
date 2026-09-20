# Running and deploying SecureDocs

Three ways, from least to most effort. Pick by what you actually need.

| You want | Use | Needs |
|---|---|---|
| To run it on this laptop, no IDE | **Option A** - double-click | Node + PostgreSQL |
| To run it anywhere, nothing installed | **Option B** - Docker | Docker Desktop |
| A public link to share | **Option C** - Render | A GitHub account |

---

## Option A - double-click (simplest)

**Windows:** double-click `start.bat`
**macOS / Linux:** `./start.sh`

It installs what is missing, prepares the database, builds the screens,
starts the text reader and the server, and opens your browser at
**http://localhost:5000**.

One URL. No IDE, no three terminals, no CORS.

**Before the first run**, create the settings file:

```
copy backend\.env.example backend\.env
```

Then open `backend\.env` and put a key on the `MASTER_KEY=` line.
Generate one with:

```
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

**Signing in:**

| Field | Value |
|---|---|
| Service number | `DL-INS-1001` |
| Password | `Test@1234` |
| 6-digit code | double-click `get-code.bat` |

On a real deployment that six-digit code comes from an authenticator app
on the officer's phone. `get-code.bat` exists so a demo on one laptop
does not need five phones set up first.

To stop everything: `stop.bat`, or close the two minimised windows.

---

## Option B - Docker (one command, nothing else installed)

```bash
docker compose up
```

Then open **http://localhost:5000**.

This starts the database and the write-once file store as well, so
PostgreSQL does not need to be installed. Data is kept in named volumes,
so stopping and starting loses nothing.

This is the closest thing to how it would really be deployed: files go
to object storage rather than a local folder, and the text reader runs
as its own process.

---

## Option C - a public link

```
1. render.com  ->  New  ->  Blueprint
2. Pick this repository. It reads render.yaml and wires everything up.
3. Wait for the build. Render gives you a URL.
```

`render.yaml` creates the database, connects it, generates the master
key, and runs the text reader inside the web process because the free
plan has no separate background worker.

### Read this before you share the link

Putting this on the public internet has consequences that do not apply
on a laptop. None of them is a reason not to do it - they are things to
know first.

**The demo accounts have a published password.** `Test@1234` is in this
file and in the guide. Anyone with the link can sign in as an inspector.
For anything beyond a demo, change the passwords in `scripts/seed.js`
before deploying, or delete the seeded accounts afterwards.

**Uploaded files do not survive a restart on the free plan.** Free
instances have no persistent disk. The database row survives but the
file does not, so those documents will then report **TAMPERED** - the
system is correctly telling you the file is no longer what was stored.
It is not a bug, and it is a bad surprise mid-presentation. Fix it by
setting `STORAGE_BACKEND=minio` with a real object store, or re-upload
after any restart.

**Free instances sleep.** The first visit after a quiet period takes
thirty to sixty seconds to wake. Open the link a minute before
presenting.

**Text recognition is slower and heavier in the cloud.** It downloads
language data on first use and competes with the web process for CPU.
`render.yaml` sets `OCR_LANGS=eng` for that reason - add `+hin` back if
you need Hindi and can accept the cold start.

**The seeded case data is fictional but realistic.** It contains
invented victim names and addresses, deliberately, because that is what
redaction is demonstrated on. No real person's data is in this
repository, and none should ever be added to it.

### Making it production-shaped

| Change | Why |
|---|---|
| `STORAGE_BACKEND=minio` with real credentials | Files must outlive a restart |
| Text reader as its own paid service, drop `RUN_WORKER_INLINE` | Recognition stops competing with requests |
| `LEDGER_BACKEND=fabric` | The anchoring becomes independently verifiable |
| Real accounts, seeded ones removed | The published demo password stops working |
| A backup of `MASTER_KEY`, kept off the server | Lose it and every stored document is unreadable forever |

---

## What runs where

| Piece | What it does | Required? |
|---|---|---|
| Server | Serves the screens and the API on one port | Yes |
| PostgreSQL | All records | Yes |
| Text reader | Reads scanned pages so they can be searched | No - without it, scans stay unread and search finds nothing |
| Object storage | Keeps files safely | Only in the cloud, where local disk is wiped |
| Fabric ledger | Independent record of fingerprints | No - falls back to the in-memory stand-in |

## If something is wrong

```
cd backend && npm run preflight
```

It checks everything and prints the exact command to fix whatever is
missing.
