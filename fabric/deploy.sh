#!/bin/bash
#
# Bring up a two-organisation Fabric network and deploy the anchor
# chaincode onto it.
#
# Prerequisites (see README.md - budget a full day for these the first
# time, especially on Windows):
#   - Docker Desktop running
#   - fabric-samples cloned, with its bin/ on PATH
#   - FABRIC_SAMPLES pointing at that clone
#
#   ./deploy.sh          bring up, create channel, deploy chaincode
#   ./deploy.sh down     tear everything down
#
set -e

FABRIC_SAMPLES="${FABRIC_SAMPLES:-$HOME/fabric-samples}"
CHANNEL="${FABRIC_CHANNEL:-mychannel}"
CC_NAME="${FABRIC_CHAINCODE:-anchor}"
CC_PATH="$(cd "$(dirname "$0")/../chaincode/anchor" && pwd)"

if [ ! -d "$FABRIC_SAMPLES/test-network" ]; then
  echo "fabric-samples not found at $FABRIC_SAMPLES"
  echo "  git clone https://github.com/hyperledger/fabric-samples"
  echo "  then re-run with FABRIC_SAMPLES=/path/to/fabric-samples"
  exit 1
fi

cd "$FABRIC_SAMPLES/test-network"

if [ "$1" = "down" ]; then
  ./network.sh down
  exit 0
fi

# Org1 stands in for PoliceOrg, Org2 for CourtOrg. Two organisations is
# the minimum that demonstrates the actual claim: neither the police nor
# the court can restate a hash on their own.
./network.sh up createChannel -c "$CHANNEL" -ca

# The endorsement policy is the whole point. AND(...) means a
# transaction is only committed if BOTH organisations endorse it, so a
# single compromised org cannot write or rewrite an anchor. Change this
# to OR(...) and you have given away the property you are demonstrating.
./network.sh deployCC \
  -c "$CHANNEL" \
  -ccn "$CC_NAME" \
  -ccp "$CC_PATH" \
  -ccl javascript \
  -ccep "AND('Org1MSP.peer','Org2MSP.peer')"

echo
echo "Deployed '$CC_NAME' to channel '$CHANNEL'."
echo
echo "Point the backend at it by setting, in backend/.env:"
echo
CRYPTO="$FABRIC_SAMPLES/test-network/organizations/peerOrganizations/org1.example.com"
cat <<EOF
LEDGER_BACKEND=fabric
FABRIC_CHANNEL=$CHANNEL
FABRIC_CHAINCODE=$CC_NAME
FABRIC_MSP_ID=Org1MSP
FABRIC_PEER_ENDPOINT=localhost:7051
FABRIC_PEER_HOST_ALIAS=peer0.org1.example.com
FABRIC_TLS_CERT_PATH=$CRYPTO/peers/peer0.org1.example.com/tls/ca.crt
FABRIC_CERT_PATH=$CRYPTO/users/User1@org1.example.com/msp/signcerts
FABRIC_KEY_PATH=$CRYPTO/users/User1@org1.example.com/msp/keystore
EOF
