#!/bin/bash
#
# The demo, end to end, from a terminal.
#
#   npm run dev        (another terminal)
#   npm run worker     (another terminal, for OCR)
#   ./demo.sh
#
# Deliberately no psql anywhere. psql authenticates as the postgres OS
# user and stops to ask for a password on Windows, which means the demo
# hangs with no output in front of an audience. Everything that needs
# the database goes through node, which uses DATABASE_URL from .env.
#
set -e
cd "$(dirname "$0")"

API=localhost:5000/api/v1
USER_SN=DL-INS-1001
PASS='Test@1234'

hr() { echo; echo "=============== $1 ==============="; }
json() { node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{console.log(JSON.stringify(JSON.parse(s),null,2))}catch{console.log(s)}})'; }
field() { node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const j=JSON.parse(s);console.log(j['$1']??'')}catch{console.log('')}})"; }

# ---- database lookups, via node ----
q() { node -e "
require('dotenv').config({quiet:true});
const db=require('./db');
db.query(\"$1\").then(r=>{console.log(r.rows[0]?Object.values(r.rows[0])[0]:'');return db.pool.end()}).catch(e=>{console.error(e.message);process.exit(1)});
"; }

hr "SIGN IN  (password, then MFA)"
CODE=$(node -e '
require("dotenv").config({quiet:true});
const db=require("./db"),crypto=require("crypto");
db.query("SELECT mfa_secret FROM users WHERE service_number=$1",["DL-INS-1001"]).then(r=>{
const A="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";let b=0,v=0,o=[];
for(const c of r.rows[0].mfa_secret){const i=A.indexOf(c);if(i<0)continue;v=(v<<5)|i;b+=5;if(b>=8){o.push((v>>>(b-8))&255);b-=8;}}
const buf=Buffer.alloc(8);buf.writeUInt32BE(Math.floor(Date.now()/1000/30),4);
const h=crypto.createHmac("sha1",Buffer.from(o)).update(buf).digest();const f=h[19]&15;
console.log(String(((((h[f]&127)<<24)|(h[f+1]<<16)|(h[f+2]<<8)|h[f+3])>>>0)%1000000).padStart(6,"0"));
db.pool.end();});')

MFA=$(curl -s -X POST $API/auth/login -H "Content-Type: application/json" \
  -d "{\"service_number\":\"$USER_SN\",\"password\":\"$PASS\"}" | field mfa_token)
TOKEN=$(curl -s -X POST $API/auth/mfa/verify -H "Content-Type: application/json" \
  -d "{\"mfa_token\":\"$MFA\",\"code\":\"$CODE\"}" | field session_token)

if [ -z "$TOKEN" ]; then echo "sign in failed - is the server running?"; exit 1; fi
echo "password accepted, MFA verified, session token ${TOKEN:0:12}..."
AUTH="Authorization: Bearer $TOKEN"

CASE=$(q "SELECT id FROM cases WHERE case_number='FIR/0142/2026'")
PROT=$(q "SELECT id FROM cases WHERE sensitivity='protected' LIMIT 1")
echo "case: $CASE"

hr "UPLOAD  (hash computed, then anchored)"
echo "FIR 0142 of 2026. Complainant Sunita Sharma, d/o Ramesh Sharma, phone 9876543210." > /tmp/fir.txt
UP=$(curl -s -X POST $API/documents -H "$AUTH" \
  -F "case_id=$CASE" -F "title=Demo FIR" -F "doc_type=fir" -F "file=@/tmp/fir.txt")
echo "$UP" | json
DOC=$(echo "$UP" | field document_id)

hr "VERIFY  (clean - recompute the hash, compare to the ledger)"
curl -s -X POST $API/documents/$DOC/verify -H "$AUTH" | json

hr "BSA S.63 CERTIFICATE  (clean - should issue)"
curl -s -o /tmp/bsa63.pdf -w "http %{http_code}, %{size_download} bytes -> /tmp/bsa63.pdf\n" \
  $API/documents/$DOC/certificate -H "$AUTH"

hr "RECOGNISED TEXT AND ENTITIES  (needs: npm run worker)"
curl -s $API/documents/$DOC/text -H "$AUTH" | json

hr "SEARCH  (only cases you are assigned to)"
curl -s -G $API/documents/search --data-urlencode "q=Sharma" -H "$AUTH" | json

hr "PROTECTED CASE  (S.72 - redacted, or refused outright)"
PDOC=$(curl -s $API/cases/$PROT/documents -H "$AUTH" | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const j=JSON.parse(s);console.log(j[0]?j[0].id:'')}catch{console.log('')}})")
if [ -n "$PDOC" ]; then
  echo "--- text (redacted, or 503 when redaction is off) ---"
  curl -s -w "\nhttp %{http_code}\n" $API/documents/$PDOC/text -H "$AUTH" | json
  echo "--- download (an image cannot be redacted safely - must refuse) ---"
  curl -s -o /dev/null -w "http %{http_code}\n" $API/documents/$PDOC/content -H "$AUTH"
else
  echo "(no document in the protected case - run: node scripts/seed-scan.js 'FIR/0198/2026')"
fi

hr "CHAIN OF CUSTODY  (off the ledger)"
curl -s $API/documents/$DOC/custody -H "$AUTH" | json

hr "ICJS  (mock - real shapes, fixture data)"
curl -s -G $API/icjs/fir --data-urlencode "fir_number=FIR/0142/2026" -H "$AUTH" | json
echo "--- what we would send ICJS: a hash, never the file ---"
curl -s $API/icjs/documents/$DOC/payload -H "$AUTH" | json

hr "TAMPER  (edit the encrypted blob directly on disk)"
BLOB=$(ls -t uploads/*.enc | head -1)
printf 'X' | dd of="$BLOB" bs=1 seek=0 conv=notrunc 2>/dev/null
echo "corrupted $BLOB"

hr "VERIFY  (tampered - must report TAMPERED)"
curl -s -X POST $API/documents/$DOC/verify -H "$AUTH" | json

hr "BSA S.63 CERTIFICATE  (tampered - must refuse with 409)"
curl -s -w "\nhttp %{http_code}\n" $API/documents/$DOC/certificate -H "$AUTH" | json

hr "AUDIT TRAIL  (hash-linked, append-only)"
curl -s $API/documents/$DOC/audit -H "$AUTH" | json

hr "RANK IS NOT ACCESS  (a real constable, correct password, not assigned)"
CCODE=$(node -e '
require("dotenv").config({quiet:true});
const db=require("./db"),crypto=require("crypto");
db.query("SELECT mfa_secret FROM users WHERE service_number=$1",["DL-CON-3003"]).then(r=>{
if(!r.rows[0]){console.log("");return db.pool.end();}
const A="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";let b=0,v=0,o=[];
for(const c of r.rows[0].mfa_secret){const i=A.indexOf(c);if(i<0)continue;v=(v<<5)|i;b+=5;if(b>=8){o.push((v>>>(b-8))&255);b-=8;}}
const buf=Buffer.alloc(8);buf.writeUInt32BE(Math.floor(Date.now()/1000/30),4);
const h=crypto.createHmac("sha1",Buffer.from(o)).update(buf).digest();const f=h[19]&15;
console.log(String(((((h[f]&127)<<24)|(h[f+1]<<16)|(h[f+2]<<8)|h[f+3])>>>0)%1000000).padStart(6,"0"));
db.pool.end();});')
CMFA=$(curl -s -X POST $API/auth/login -H "Content-Type: application/json" \
  -d "{\"service_number\":\"DL-CON-3003\",\"password\":\"$PASS\"}" | field mfa_token)
CTOK=$(curl -s -X POST $API/auth/mfa/verify -H "Content-Type: application/json" \
  -d "{\"mfa_token\":\"$CMFA\",\"code\":\"$CCODE\"}" | field session_token)
echo "constable signed in: ${CTOK:0:12}..."
echo "--- their case list ---"
curl -s $API/cases -H "Authorization: Bearer $CTOK" | json
echo "--- the same document, requested directly (404: it is not hidden, it is absent) ---"
curl -s -w "\nhttp %{http_code}\n" $API/documents/$DOC -H "Authorization: Bearer $CTOK" | json

echo
echo "=============== END ==============="
