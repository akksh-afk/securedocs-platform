# Fabric ledger (F1)

Replaces the in-memory anchoring stub with a real permissioned ledger.

## What is on the ledger

Per document version:

```
versionId, documentId, sha256, signedBy, signedAt, station, action
```

Never the document. Never any personal data. You should be able to say
that in one sentence and have it be true — if a field is ever added
here, ask whether you would be comfortable with every organisation on
the channel reading it forever.

Custody events (`transfer`, `share`, `export`) update the same key, so
`GetHistoryForKey` returns the complete chain of custody.

## Two organisations

Org1 stands in for **PoliceOrg**, Org2 for **CourtOrg**. The chaincode
is deployed with the endorsement policy:

```
AND('Org1MSP.peer','Org2MSP.peer')
```

Both must endorse before an anchor commits. That is the demonstrable
claim: no single party can write or rewrite history. Weakening this to
`OR(...)` gives away the entire property.

## Setup

The first run takes a while. Budget a full day, and more on Windows —
run it under WSL2 rather than Git Bash.

```bash
# 1. Docker Desktop must be running
docker ps

# 2. Fabric samples + binaries
git clone https://github.com/hyperledger/fabric-samples
cd fabric-samples
curl -sSL https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/install-fabric.sh | bash -s -- binary
export PATH=$PWD/bin:$PATH

# 3. Bring up the network and deploy the chaincode
cd /path/to/SIH2026/fabric
FABRIC_SAMPLES=/path/to/fabric-samples ./deploy.sh
```

`deploy.sh` prints the exact block to paste into `backend/.env`.

Tear down with `./deploy.sh down`.

## Switching the backend

The backend defaults to the stub so a machine with no Docker still runs
end to end:

```
LEDGER_BACKEND=stub     in-memory, single process (default)
LEDGER_BACKEND=fabric   the real thing
```

The startup log prints which one is active. Check it before demoing —
presenting the stub as a blockchain is a claim you cannot defend, and
someone will ask.

`services/ledger.js` is a facade over both. `anchor()` and `lookup()`
keep identical signatures across backends, so no route changes when you
switch.

## Acceptance checks

With `LEDGER_BACKEND=fabric` and the network up:

```bash
# Upload a document through the API, then read it back off the ledger
peer chaincode query -C mychannel -n anchor \
  -c '{"Args":["Lookup","<version_id>"]}'

# Full chain of custody
peer chaincode query -C mychannel -n anchor \
  -c '{"Args":["History","<version_id>"]}'
```

- Upload → `Lookup` returns the hash, `verify` returns `verified: true`
  with a real `ledger_tx_id`
- Corrupt the blob on disk → `verify` returns `verified: false`
- Stop one peer → reads still work
- Re-anchoring the same version is rejected by the chaincode

## Known limits

- Anchoring is fire-and-forget. A ledger outage marks the version
  `anchor_status = 'failed'` rather than failing the user's upload;
  nothing retries it yet. A sweeper over `anchor_status = 'failed'` is
  the obvious next step.
- The gateway identity is a single test-network user. Production wants
  one identity per station, issued by the org CA.
