const assert = require("assert");
const { PDFDocument, StandardFonts } = require("pdf-lib");

const dsc = require("../services/dsc");
const watermark = require("../services/watermark");
const certificate = require("../services/certificate");
const icjs = require("../services/icjs");

// ---------------------------------------------------------------
// Self-check for the backend services that have real logic in them.
// No database and no server - everything here is pure or in-memory.
//
//   node test/services.test.js
//
// Routes and SQL are not covered; those need a live Postgres and the
// demo script exercises them.
// ---------------------------------------------------------------

const HASH = "a".repeat(64);
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ---- F11: DSC / eSign ----

test("dsc: a signature it just made verifies", () => {
    const s = dsc.sign(HASH, "user-1");
    assert.strictEqual(dsc.verify(HASH, s.signature, s.certificate), true);
});

test("dsc: signature does not verify against a different hash", () => {
    const s = dsc.sign(HASH, "user-1");
    assert.strictEqual(dsc.verify("b".repeat(64), s.signature, s.certificate), false);
});

test("dsc: a garbage signature is false, not a thrown error", () => {
    const s = dsc.sign(HASH, "user-1");
    assert.strictEqual(dsc.verify(HASH, "not-base64-at-all", s.certificate), false);
    assert.strictEqual(dsc.verify(HASH, s.signature, "not-a-pem"), false);
});

test("dsc: reports the algorithm and carries its certificate", () => {
    const s = dsc.sign(HASH, "user-1");
    assert.strictEqual(s.algorithm, "ecdsa-p256-sha256");
    assert.ok(s.certificate.includes("BEGIN PUBLIC KEY"));
});

// ---- F8: watermarking ----

test("watermark: Devanagari is stripped, not thrown on", () => {
    // Helvetica cannot encode these. An officer named in Hindi must not
    // break the download path.
    assert.strictEqual(watermark.toWinAnsi("राजेश Kumar"), "Kumar");
    assert.ok(watermark.buildLabel({ name: "राजेश", serviceNumber: "MH-1" }).length > 0);
});

test("watermark: label carries every identifying field", () => {
    const label = watermark.buildLabel({
        serviceNumber: "MH-INS-4471",
        name: "A Deshmukh",
        caseNumber: "FIR/0142/2026",
        timestamp: "2026-09-07T10:00:00Z",
    });
    for (const part of ["MH-INS-4471", "A Deshmukh", "FIR/0142/2026"]) {
        assert.ok(label.includes(part), `label missing ${part}`);
    }
});

test("watermark: never empty, even with no metadata at all", () => {
    assert.strictEqual(watermark.buildLabel({}), "RELEASED COPY");
});

test("watermark: non-PDF passes through byte-identical", async () => {
    const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x01, 0x02]);
    const out = await watermark.apply(png, "image/png", { name: "X" });
    assert.ok(out.equals(png));
});

test("watermark: PDF is stamped and stays a valid PDF", async () => {
    const doc = await PDFDocument.create();
    doc.addPage([300, 300]);
    doc.addPage([300, 300]);
    const original = Buffer.from(await doc.save());

    const out = await watermark.apply(original, "application/pdf", {
        serviceNumber: "MH-INS-4471",
        name: "A Deshmukh",
        caseNumber: "FIR/0142/2026",
        timestamp: new Date().toISOString(),
    });

    assert.ok(!out.equals(original), "pdf was not modified");
    assert.strictEqual(out.subarray(0, 4).toString(), "%PDF");

    // Still parseable, and no pages were lost.
    const reloaded = await PDFDocument.load(out);
    assert.strictEqual(reloaded.getPageCount(), 2);
});

test("watermark: unparseable PDF still returns bytes rather than failing", async () => {
    const junk = Buffer.from("%PDF-1.7 this is not really a pdf");
    const out = await watermark.apply(junk, "application/pdf", { name: "X" });
    assert.ok(out.equals(junk));
});

// ---- F2: BSA s.63 certificate ----

const CERT_FIXTURE = {
    document: { id: "doc-1", title: "First Information Report", doc_type: "fir" },
    caseRecord: {
        case_number: "FIR/0142/2026",
        title: "State v. Unknown",
        sensitivity: "normal",
    },
    version: {
        version: 2,
        sha256: HASH,
        size_bytes: 48210,
        mime_type: "application/pdf",
        uploaded_at: "2026-02-11T09:24:00.000Z",
        ledger_tx_id: "stub-deadbeef",
        anchored_at: "2026-02-11T09:24:03.000Z",
        anchor_status: "anchored",
    },
    producedBy: {
        id: "user-1",
        name: "A Deshmukh",
        rank: "inspector",
        service_number: "MH-INS-4471",
        station: "Sadar Bazar",
    },
    verification: { verified: true, verified_at: "2026-09-07T10:00:00.000Z" },
};

test("certificate: produces a loadable multi-page PDF", async () => {
    const pdf = await certificate.build(CERT_FIXTURE);

    assert.strictEqual(pdf.subarray(0, 4).toString(), "%PDF");

    const reloaded = await PDFDocument.load(pdf);
    // Part A plus the blank Part B does not fit on one page.
    assert.ok(reloaded.getPageCount() >= 2, "certificate should run past one page");
});

test("certificate: survives a name it cannot encode", async () => {
    const pdf = await certificate.build({
        ...CERT_FIXTURE,
        producedBy: { ...CERT_FIXTURE.producedBy, name: "राजेश कुमार" },
    });
    assert.strictEqual(pdf.subarray(0, 4).toString(), "%PDF");
});

test("certificate: tolerates a document that was never anchored", async () => {
    const pdf = await certificate.build({
        ...CERT_FIXTURE,
        version: {
            ...CERT_FIXTURE.version,
            ledger_tx_id: null,
            anchored_at: null,
            anchor_status: "pending",
        },
    });
    assert.strictEqual(pdf.subarray(0, 4).toString(), "%PDF");
});

test("certificate: states the statutory operating condition", () => {
    assert.ok(certificate.OPERATION_STATEMENT.includes("operating properly"));
});

// ---- F10: ICJS mock adapter ----

test("icjs: known FIR returns a record flagged as mock", async () => {
    const fir = await icjs.fetchFir("FIR/0142/2026");
    assert.strictEqual(fir.cctns_id, "MH01-2026-0000142");
    assert.strictEqual(fir.mock, true);
});

test("icjs: unknown FIR is null, not an invented record", async () => {
    assert.strictEqual(await icjs.fetchFir("FIR/9999/2026"), null);
});

test("icjs: linkage fails cleanly for an unknown FIR", async () => {
    const r = await icjs.linkCase({ caseNumber: "C-1", firNumber: "FIR/9999/2026" });
    assert.strictEqual(r.linked, false);
    assert.strictEqual(r.reason, "fir_not_found");
});

test("icjs: linkage returns a correlation id for a known FIR", async () => {
    const r = await icjs.linkCase({ caseNumber: "C-1", firNumber: "FIR/0142/2026" });
    assert.strictEqual(r.linked, true);
    assert.ok(r.correlation_id.includes("MH01-2026-0000142"));
});

test("icjs: metadata payload carries the hash and never the content", () => {
    const payload = icjs.documentMetadataPayload({
        document: { id: "doc-1", title: "FIR", doc_type: "fir" },
        caseRecord: { case_number: "FIR/0142/2026", sensitivity: "protected" },
        version: {
            version: 1,
            sha256: HASH,
            ledger_tx_id: "stub-1",
            anchored_at: "2026-02-11T09:24:03.000Z",
        },
    });

    assert.strictEqual(payload.integrity.hash, HASH);

    // ICJS gets a pointer and a hash. If the document text, entities, or
    // any victim data ever appear in this payload, that is a leak across
    // an organisational boundary.
    const wire = JSON.stringify(payload);
    for (const forbidden of ["extracted_text", "entities", "wrapped_key", "storage_path"]) {
        assert.ok(!wire.includes(forbidden), `payload leaked ${forbidden}`);
    }
});

// ---- audit: the hashed payload must not depend on key order ----

const { canonicalJson } = require("../services/audit");

test("audit: detail hashing is independent of key order", () => {
    // Postgres JSONB does not preserve the order keys were written in.
    // An entry written as {sha256, title} comes back as {title, sha256},
    // so plain JSON.stringify produced a different string on read and
    // the entry failed its own hash check - a false "chain BROKEN" with
    // nothing actually wrong.
    const written = { sha256: "abc", title: "Scanned FIR" };
    const readBack = { title: "Scanned FIR", sha256: "abc" };

    assert.notStrictEqual(
        JSON.stringify(written),
        JSON.stringify(readBack),
        "this test is pointless if plain stringify already matches"
    );
    assert.strictEqual(canonicalJson(written), canonicalJson(readBack));
});

test("audit: canonical form is stable for nested objects and arrays", () => {
    const a = { b: [3, { y: 1, x: 2 }], a: { d: 4, c: 3 } };
    const b = { a: { c: 3, d: 4 }, b: [3, { x: 2, y: 1 }] };

    assert.strictEqual(canonicalJson(a), canonicalJson(b));
});

test("audit: canonical form still distinguishes different content", () => {
    // Sorting keys must not make two genuinely different entries hash
    // the same - that would make tampering undetectable.
    assert.notStrictEqual(
        canonicalJson({ verified: true }),
        canonicalJson({ verified: false })
    );
    assert.notStrictEqual(canonicalJson({ a: 1 }), canonicalJson({ b: 1 }));
    assert.notStrictEqual(canonicalJson(null), canonicalJson({}));
});

// ---- F5: victim identity redaction ----

const redaction = require("../services/redaction");
const entitiesSvc = require("../services/entities");

const STATEMENT = [
    "FIR/0142/2026 recorded under BNS Section 74 and Section 72.",
    "The complainant Sunita Sharma, daughter of Ramesh Sharma, residing at",
    "14 Nehru Road, stated that on 11/02/2026 the accused followed her.",
    "She can be reached on 9876543210 or sunita.sharma@example.com.",
    "Recorded by Inspector A Deshmukh, Sadar Bazar police station.",
].join("\n");

const VICTIM_TARGETS = ["Sunita Sharma", "Ramesh Sharma", "14 Nehru Road"];

test("redaction: victim name is absent from the released text", async () => {
    const { buffer } = await redaction.redact(
        Buffer.from(STATEMENT),
        "text/plain",
        VICTIM_TARGETS
    );
    const out = buffer.toString();

    assert.ok(!out.includes("Sunita Sharma"), "victim name survived redaction");
    assert.ok(!out.includes("Ramesh Sharma"), "parent name survived redaction");
    assert.ok(!out.includes("14 Nehru Road"), "address survived redaction");
});

test("redaction: phone, email and Aadhaar go without being named", async () => {
    // These come from the rules layer, not the identity list - nobody
    // had to register them.
    const withAadhaar = STATEMENT + "\nAadhaar 2345 6789 0123 on file.";
    const { buffer } = await redaction.redact(Buffer.from(withAadhaar), "text/plain", []);
    const out = buffer.toString();

    assert.ok(!out.includes("9876543210"), "phone number survived");
    assert.ok(!out.includes("sunita.sharma@example.com"), "email survived");
    assert.ok(!out.includes("2345 6789 0123"), "Aadhaar survived");
});

test("redaction: evidentiary fields are preserved", async () => {
    // A redactor that eats the FIR number and the statute references has
    // destroyed the document's value as evidence.
    const { buffer } = await redaction.redact(
        Buffer.from(STATEMENT),
        "text/plain",
        VICTIM_TARGETS
    );
    const out = buffer.toString();

    assert.ok(out.includes("FIR/0142/2026"), "FIR number was redacted");
    assert.ok(out.includes("Section 74"), "statute reference was redacted");
    assert.ok(out.includes("Section 72"), "statute reference was redacted");
    assert.ok(out.includes("11/02/2026"), "date was redacted");
    assert.ok(out.includes("A Deshmukh"), "officer name was redacted");
});

test("redaction: reports how many spans it removed", async () => {
    const { removed } = await redaction.redact(
        Buffer.from(STATEMENT),
        "text/plain",
        VICTIM_TARGETS
    );
    assert.ok(removed >= 5, `expected several removals, got ${removed}`);
});

test("redaction: whole-word only, so a station name survives a victim name", async () => {
    const text = "Sita was found in Sitapur by the Sitapur unit.";
    const { buffer } = await redaction.redact(Buffer.from(text), "text/plain", ["Sita"]);
    const out = buffer.toString();

    assert.ok(!/\bSita\b/.test(out), "the name itself should be gone");
    assert.strictEqual((out.match(/Sitapur/g) || []).length, 2, "Sitapur must survive");
});

test("redaction: never mutates the input buffer", async () => {
    // The stored original must stay byte-identical or the hash stops
    // verifying and the whole integrity story collapses.
    const original = Buffer.from(STATEMENT);
    const copy = Buffer.from(original);

    await redaction.redact(original, "text/plain", VICTIM_TARGETS);

    assert.ok(original.equals(copy), "redaction mutated the source buffer");
});


test("redaction: refuses images, which have no locatable text", async () => {
    // OCR gives the words in a photograph but not their coordinates, so
    // there is nothing to draw a box around. Refusing is the only safe
    // answer until it does.
    assert.strictEqual(redaction.canRedact("image/png"), false);
    await assert.rejects(
        () => redaction.redact(Buffer.alloc(4), "image/jpeg", ["X"]),
        redaction.UnsupportedFormatError
    );
});

test("redaction: PDF is a supported format now", () => {
    assert.strictEqual(redaction.canRedact("application/pdf"), true);
    assert.strictEqual(redaction.canRedact("text/plain"), true);
    assert.strictEqual(redaction.canRedact("image/png"), false);
    assert.strictEqual(redaction.canRedact(undefined), false);
});

test("redaction: a redacted PDF has NO text layer left at all", async () => {
    // The guarantee that makes PDF redaction trustworthy. The output is
    // rebuilt from images, so there is nothing to select or copy - not
    // a black box with the words still underneath it.
    const pdfSvc = require("../services/pdf");

    const doc = await PDFDocument.create();
    const font = await doc.embedFont(StandardFonts.Helvetica);
    const page = doc.addPage([595, 842]);
    [
        "FIR No. 198 of 2026",
        "Under BNS Section 74 and Section 72",
        "Complainant Sunita Sharma",
        "Daughter of Ramesh Sharma",
        "Phone 9876543210",
    ].forEach((t, i) => page.drawText(t, { x: 60, y: 760 - i * 28, size: 13, font }));

    const original = Buffer.from(await doc.save());

    // The original really does carry its text - otherwise this proves
    // nothing.
    const before = await pdfSvc.extractText(original);
    assert.ok(before.text.includes("Sunita Sharma"), "fixture has no text layer");

    const { buffer, removed } = await redaction.redact(original, "application/pdf", [
        "Sunita Sharma",
        "Ramesh Sharma",
    ]);

    assert.ok(removed > 0, "nothing was redacted");
    assert.strictEqual(buffer.subarray(0, 4).toString(), "%PDF");

    const after = await pdfSvc.extractText(buffer);
    for (const leak of ["Sunita", "Ramesh", "9876543210"]) {
        assert.ok(
            !after.text.includes(leak),
            `"${leak}" is still extractable from the redacted PDF`
        );
    }
    assert.strictEqual(after.text, "", "the redacted export still has a text layer");
});

test("redaction: the stored PDF is never modified", async () => {
    const doc = await PDFDocument.create();
    const font = await doc.embedFont(StandardFonts.Helvetica);
    doc.addPage([300, 300]).drawText("Complainant Sunita Sharma", {
        x: 20,
        y: 200,
        size: 12,
        font,
    });
    const original = Buffer.from(await doc.save());
    const copy = Buffer.from(original);

    await redaction.redact(original, "application/pdf", ["Sunita Sharma"]);

    // If the original were altered, its hash would stop verifying and
    // every released document would read as TAMPERED afterwards.
    assert.ok(original.equals(copy), "redaction mutated the stored PDF");
});

test("redaction: a one-character target is ignored, not matched everywhere", async () => {
    // "a" as a target must contribute nothing. The rules layer still
    // removes the phone and email, so compare against that baseline
    // rather than against zero.
    const baseline = await redaction.redact(Buffer.from(STATEMENT), "text/plain", []).removed;
    const withTiny = await redaction.redact(Buffer.from(STATEMENT), "text/plain", ["a"]).removed;

    assert.strictEqual(withTiny, baseline);
});

test("redaction: merges F4 entities with registered identities", () => {
    const targets = redaction.targetsFrom({
        identities: [{ value: "Sunita Sharma" }],
        extracted: { persons: ["Ramesh Sharma"], addresses: ["14 Nehru Road"], sections: ["72"] },
    });

    assert.ok(targets.includes("Sunita Sharma"));
    assert.ok(targets.includes("Ramesh Sharma"));
    assert.ok(targets.includes("14 Nehru Road"));
    // Sections are evidence, never a redaction target.
    assert.ok(!targets.includes("72"));
});

test("redaction: tolerates entities being absent entirely", () => {
    assert.deepStrictEqual(redaction.targetsFrom({}), []);
    assert.deepStrictEqual(
        redaction.targetsFrom({ identities: [], extracted: null }),
        []
    );
});

test("entities: contextual layer names the complainant and her relations", () => {
    const found = entitiesSvc.contextualIdentities(STATEMENT);

    assert.ok(
        found.persons.some((p) => p.includes("Sunita Sharma")),
        `complainant not found, got ${JSON.stringify(found.persons)}`
    );
    assert.ok(
        found.persons.some((p) => p.includes("Ramesh Sharma")),
        `parent not found, got ${JSON.stringify(found.persons)}`
    );
});

test("entities: contextual layer reads s/o d/o w/o abbreviations", () => {
    const found = entitiesSvc.contextualIdentities(
        "Statement of Meena Devi w/o Suresh Kumar r/o 22 Gandhi Marg."
    );

    assert.ok(found.persons.some((p) => p.includes("Suresh Kumar")));
    assert.ok(found.addresses.some((a) => a.includes("Gandhi Marg")));
});

test("entities: contextual layer handles Devanagari names", () => {
    const found = entitiesSvc.contextualIdentities("complainant सुनीता शर्मा aged 24");
    assert.ok(
        found.persons.some((p) => p.includes("सुनीता")),
        `expected a Devanagari name, got ${JSON.stringify(found.persons)}`
    );
});

test("entities: lowercase prose after a role word is not a name", () => {
    // The patterns are case-insensitive so they match COMPLAINANT and
    // Complainant alike - but that flag also lets [A-Z] match lowercase,
    // and "the complainant stated that the accused" captured "stated
    // that the accused" as a person. Redaction then blanked that phrase
    // out of the middle of a sentence.
    const found = entitiesSvc.contextualIdentities(
        "On 11/02/2026 the complainant stated that the accused followed her."
    );
    assert.deepStrictEqual(
        found.persons,
        [],
        `captured prose as a name: ${JSON.stringify(found.persons)}`
    );
});

test("entities: capitalised names are still found after the same role word", () => {
    // The fix must not throw out the real ones.
    const found = entitiesSvc.contextualIdentities("the complainant Sunita Sharma stated");
    assert.ok(
        found.persons.some((p) => p.includes("Sunita Sharma")),
        `real name lost: ${JSON.stringify(found.persons)}`
    );
});

test("entities: a name is never read across a line break", () => {
    // From a real 34-page FIR form. The role word ends one line and the
    // form's own answer begins the next; a pattern that steps over the
    // newline captures the answer as a person. On that document the
    // entire output was ["No Delay", "Signature", "Major"] - no real
    // name was found at all, and redaction blacked out the form
    // furniture instead of the victim.
    const form = [
        "8. Reasons for delay in reporting by the complainant/informant:",
        "No Delay",
        "9. Particulars of properties stolen",
    ].join("\n");

    assert.deepStrictEqual(
        entitiesSvc.contextualIdentities(form).persons,
        [],
        "captured the next line as a name"
    );
});

test("entities: a single capitalised word after a role word is not a name", () => {
    // "14. Signature/Thumb Impression of the complainant" and the like.
    // A person on an FIR has at least a given name and a family name.
    const found = entitiesSvc.contextualIdentities(
        "copy given to the complainant / informant, free of cost. 14. Signature"
    );
    assert.ok(
        !found.persons.includes("Signature"),
        `captured form furniture: ${JSON.stringify(found.persons)}`
    );
});

test("entities: role words alone are not mistaken for a name", () => {
    const found = entitiesSvc.contextualIdentities("The complainant said the accused fled.");
    assert.ok(
        !found.persons.some((p) => /^(the|said|accused)$/i.test(p)),
        `stop words captured as a name: ${JSON.stringify(found.persons)}`
    );
});

test("entities: contextual output feeds redaction directly", async () => {
    // F3 -> F4 -> F5. Whatever the rules find must be a usable target.
    const targets = redaction.targetsFrom({
        extracted: entitiesSvc.extract(STATEMENT),
    });
    const { buffer } = await redaction.redact(Buffer.from(STATEMENT), "text/plain", targets);

    assert.ok(
        !buffer.toString().includes("Sunita Sharma"),
        "victim name survived redaction driven by extracted entities alone"
    );
});

test("entities: FIR numbers in longhand as well as slashed form", () => {
    // "FIR No. 142 of 2026" is what station registers and OCR'd scans
    // actually produce; only matching FIR/0142/2026 misses most of them.
    for (const form of [
        "FIR/0142/2026",
        "FIR 0142 of 2026",
        "FIR No. 142 of 2026",
        "FIR No 142/2026",
    ]) {
        const found = entitiesSvc.extract(`Recorded under ${form} at the station.`);
        assert.ok(
            found.fir_numbers.length > 0,
            `no FIR number found in ${JSON.stringify(form)}`
        );
    }
});

test("entities: a longhand FIR number is preserved through redaction", async () => {
    const text = "FIR 0142 of 2026. Complainant Sunita Sharma, phone 9876543210.";
    const { buffer } = await redaction.redact(Buffer.from(text), "text/plain", [
        "Sunita Sharma",
    ]);
    const out = buffer.toString();

    assert.ok(out.includes("FIR 0142 of 2026"), "longhand FIR number was redacted");
    assert.ok(!out.includes("Sunita Sharma"), "victim name survived");
});

test("entities: rules layer finds the structured fields", () => {
    const found = entitiesSvc.extract(STATEMENT);

    assert.ok(found.phones.includes("9876543210"));
    assert.ok(found.emails.includes("sunita.sharma@example.com"));
    assert.ok(found.fir_numbers.some((f) => f.includes("0142/2026")));
    assert.ok(found.sections.length >= 2);
    assert.ok(found.dates.includes("11/02/2026"));
});

// ---- F9: route ordering ----

test("routes: /search is registered before /:document_id", () => {
    // Express matches in order. If these ever swap, every search becomes
    // a lookup for a document whose id is the word "search" and the
    // failure is a confusing 404, not an error anyone can trace.
    const router = require("../routes/documents");
    const paths = router.stack.filter((l) => l.route).map((l) => l.route.path);

    const search = paths.indexOf("/search");
    const byId = paths.indexOf("/:document_id");

    assert.ok(search !== -1, "/search route is missing");
    assert.ok(byId !== -1, "/:document_id route is missing");
    assert.ok(search < byId, "/search must be registered before /:document_id");
});

// ---- mobile OTP sign-in ----

test("otp: mobile numbers normalise to one E.164 form", () => {
    const sms = require("../services/sms");
    for (const typed of ["9876543210", "98765 43210", "+91 98765-43210", "+919876543210"]) {
        assert.strictEqual(sms.normalizeMobile(typed), "+919876543210", typed);
    }
    for (const bad of ["", "12345", "abcdefghij", "+0123456789", null]) {
        assert.strictEqual(sms.normalizeMobile(bad), null, String(bad));
    }
});

test("otp: masked number keeps neither the whole number nor its middle", () => {
    const masked = require("../services/sms").maskMobile("+919876543210");
    assert.strictEqual(masked, "+91********10");
});

test("otp: codes are six digits and hashes are bound to their challenge", () => {
    process.env.MASTER_KEY = process.env.MASTER_KEY || "ab".repeat(32);
    const { generateOtp, hashOtp, otpMatches } = require("../services/crypto");

    for (let i = 0; i < 200; i++) assert.match(generateOtp(), /^\d{6}$/);

    const h = hashOtp("challenge-a", "123456");
    assert.ok(!h.includes("123456"));
    assert.strictEqual(otpMatches("challenge-a", "123456", h), true);
    assert.strictEqual(otpMatches("challenge-a", "123457", h), false);
    // The same code on another challenge is a different hash, so one
    // challenge's row says nothing about another's.
    assert.strictEqual(otpMatches("challenge-b", "123456", h), false);
    assert.strictEqual(otpMatches("challenge-a", "123456", "not-hex"), false);
});

// ---- scheduled integrity check ----

test("integrity: only real damage counts as tampering, never a pending anchor", () => {
    const { tamperReason } = require("../services/integrity");
    const H = "a".repeat(64);
    const row = (anchor_status) => ({ sha256: H, anchor_status });
    const ok = { failure: null, storedHash: H, chainRecord: { sha256: H } };

    assert.strictEqual(tamperReason(row("anchored"), ok), null);
    // Not anchored yet: no ledger record is expected, so none is not an alarm.
    assert.strictEqual(tamperReason(row("pending"), { ...ok, chainRecord: null }), null);

    assert.strictEqual(
        tamperReason(row("anchored"), { ...ok, failure: "ciphertext_modified", storedHash: null }),
        "ciphertext_modified"
    );
    assert.strictEqual(tamperReason(row("pending"), { ...ok, storedHash: "b".repeat(64) }), "hash_mismatch");
    assert.strictEqual(tamperReason(row("anchored"), { ...ok, chainRecord: null }), "ledger_mismatch");
    assert.strictEqual(
        tamperReason(row("anchored"), { ...ok, chainRecord: { sha256: "c".repeat(64) } }),
        "ledger_mismatch"
    );
});

// ---- the seam to the screening service ----

test("screening: only CLEAR passes without an officer being told", () => {
    const screening = require("../services/screening");

    assert.strictEqual(screening.isFlagged("CLEAR"), false);
    for (const d of ["SECONDARY_INSPECTION", "REFER_TO_SUPERVISOR", "RECAPTURE"]) {
        assert.strictEqual(screening.isFlagged(d), true, d);
        assert.ok(screening.FLAGGED[d], `no wording for ${d}`);
    }
});

test("screening: the summary keeps the verdict and drops the image data", () => {
    const { summarise } = require("../services/screening");

    // This fixture is the shape the screening service actually returns -
    // verdict under `risk`, the type under the primary document. An earlier
    // version of this test asserted a flat shape the service has never
    // produced, so it stayed green while every real verdict was dropped.
    const summary = summarise({
        screening_id: "6ca7d6c3dd4e4e8383a238dd7f6c4563",
        risk: {
            risk_score: 82,
            risk_band: "HIGH",
            disposition: "REFER_TO_SUPERVISOR",
            reasons: [{ code: "MRZ_CHECKSUM" }, { code: "STAMP_COPY" }],
        },
        documents: [
            {
                document_type: "passport",
                role: "primary",
                annotated_image: "data:image/png;base64,AAAA",
                ocr: { fields: { surname: "SHARMA" } },
            },
        ],
    });

    assert.deepStrictEqual(summary, {
        disposition: "REFER_TO_SUPERVISOR",
        risk_score: 82,
        risk_band: "HIGH",
        document_type: "passport",
        findings: 2,
    });
    // The audit detail must not carry the document image or the
    // holder's name into a trail everyone on the case can read.
    assert.ok(!JSON.stringify(summary).includes("base64"));
    assert.ok(!JSON.stringify(summary).includes("SHARMA"));
});

test("screening: a response in an unexpected shape yields no disposition", () => {
    const { summarise } = require("../services/screening");

    // screenVersion treats a null disposition as a failure and marks the
    // version 'failed'. What must never happen is a throw in here or, worse,
    // further down in the alert text - the service has answered by then, so
    // the version would be left on 'pending' and read as never screened.
    for (const body of [{}, { risk: null }, { risk: {}, documents: [] }]) {
        const summary = summarise(body);
        assert.strictEqual(summary.disposition, null);
        assert.strictEqual(summary.document_type, null);
    }
});

// ---------------------------------------------------------------

(async () => {
    let failed = 0;

    for (const [name, fn] of tests) {
        try {
            await fn();
            console.log(`  ok    ${name}`);
        } catch (err) {
            failed++;
            console.error(`  FAIL  ${name}`);
            console.error(`        ${err.message}`);
        }
    }

    console.log(`\n${tests.length - failed}/${tests.length} passed`);
    process.exit(failed ? 1 : 0);
})();
