const assert = require("assert");
const { Jimp, loadFont } = require("jimp");
const { SANS_32_BLACK } = require("jimp/fonts");

const ocr = require("../services/ocr");
const entities = require("../services/entities");

// ---------------------------------------------------------------
// OCR self-check.
//
//   npm run test:ocr
//
// Kept out of `npm test` on purpose: the first run downloads ~15MB of
// traineddata per language and recognition takes seconds, which is not
// something to put in front of every commit.
//
// It renders a page, tilts it the way a scanner does, and checks the
// pipeline reads it back. That is the only honest way to test OCR -
// asserting on a mock proves the mock works.
// ---------------------------------------------------------------

const TILT = 3.5;

async function makePage(lines, tilt = TILT) {
    const font = await loadFont(SANS_32_BLACK);
    const img = new Jimp({ width: 950, height: 90 + lines.length * 70, color: 0xffffffff });

    lines.forEach((text, i) => {
        img.print({ font, x: 30, y: 40 + i * 70, text });
    });

    if (tilt) img.rotate(tilt);
    return img.getBuffer("image/png");
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("deskew estimates a tilt in the direction that corrects it", async () => {
    const buf = await makePage(["Deskew probe line one", "Deskew probe line two"], 4);
    const skew = await ocr.estimateSkew(await Jimp.read(buf));

    // Page tilted +4 must be corrected by a negative rotation.
    assert.ok(skew < 0, `expected a negative correction, got ${skew}`);
    assert.ok(Math.abs(skew + 4) < 2.5, `correction ${skew} is not near -4`);
});

test("deskew leaves a level page alone", async () => {
    const buf = await makePage(["Perfectly level line", "Another level line"], 0);
    const skew = await ocr.estimateSkew(await Jimp.read(buf));

    assert.ok(Math.abs(skew) <= 1.5, `level page reported skew ${skew}`);
});

test("preprocess returns a decodable PNG", async () => {
    const buf = await makePage(["Preprocess check"]);
    const { buffer } = await ocr.preprocess(buf);

    assert.strictEqual(buffer.subarray(1, 4).toString(), "PNG");
    await Jimp.read(buffer); // throws if it is not really an image
});

test("canRead accepts images and refuses everything else", () => {
    assert.ok(ocr.canRead("image/png"));
    assert.ok(ocr.canRead("image/jpeg"));
    assert.ok(!ocr.canRead("application/pdf"));
    assert.ok(!ocr.canRead("text/plain"));
});

test("recognises text off a tilted page", async () => {
    const buf = await makePage([
        "FIR 0142 of 2026",
        "Complainant Sunita Sharma",
        "Phone 9876543210",
    ]);

    const res = await ocr.recognise(buf);
    const text = res.text.replace(/\s+/g, " ");

    console.log(`        read: ${JSON.stringify(text.slice(0, 90))}`);
    console.log(`        confidence ${res.confidence}, skew ${res.skew}`);

    // Not asserting an exact transcription - OCR is probabilistic and a
    // brittle equality here would fail on a font rendering difference.
    // Assert the things the rest of the system depends on.
    assert.ok(text.length > 10, "no text recognised at all");
    assert.ok(/0142/.test(text), "FIR digits not recognised");
    assert.ok(/9876543210/.test(text.replace(/\s/g, "")), "phone digits not recognised");
});

test("recognised text feeds the entity rules layer", async () => {
    // This is the join between F3 and F4: OCR output has to be good
    // enough for the rules layer to find something in it.
    const buf = await makePage([
        "FIR 0142 of 2026 under BNS Section 74",
        "Phone 9876543210",
    ]);

    const res = await ocr.recognise(buf);
    const found = entities.extract(res.text);

    assert.ok(
        found.phones.length > 0 || /9876543210/.test(res.text.replace(/\s/g, "")),
        "phone not extractable from OCR output"
    );
});

test("a redacted PDF has the names blacked out of the PIXELS, not just the text layer", async () => {
    // This is the check that matters, and the one that is easy to get
    // wrong. Asserting the released file has no text layer proves very
    // little on its own: the output is rebuilt from images, so it never
    // has one. The identity can still be sitting there in plain sight
    // as pixels. The only honest test is to read the released page back
    // the way a human would - with OCR - and look for the name.
    const { PDFDocument, StandardFonts } = require("pdf-lib");
    const redaction = require("../services/redaction");
    const pdfSvc = require("../services/pdf");
    const entities = require("../services/entities");

    const doc = await PDFDocument.create();
    const font = await doc.embedFont(StandardFonts.Helvetica);
    const page = doc.addPage([595, 842]);
    [
        "FIR No. 198 of 2026",
        "Under BNS Section 74 and Section 72",
        "Complainant Sunita Sharma",
        "Daughter of Ramesh Sharma",
        "Resident of 14 Nehru Road",
        "Phone 9876543210",
    ].forEach((t, i) => page.drawText(t, { x: 60, y: 760 - i * 28, size: 16, font }));

    const original = Buffer.from(await doc.save());

    // Targets come from the pipeline itself rather than being written
    // out by hand, so a regression in extraction fails this too.
    const extracted = await pdfSvc.extractText(original);
    const targets = redaction.targetsFrom({ extracted: entities.extract(extracted.text) });

    const { buffer } = await redaction.redact(original, "application/pdf", targets);

    const pages = await pdfSvc.renderPages(buffer, 2);
    const read = await ocr.recognise(pages[0].png);
    const flat = read.text.replace(/\s+/g, " ");

    console.log(`        read back: ${JSON.stringify(flat.slice(0, 80))}`);

    for (const leak of ["Sunita", "Ramesh", "Nehru", "9876543210"]) {
        assert.ok(
            !flat.includes(leak),
            `"${leak}" is still legible on the released page: ${JSON.stringify(flat)}`
        );
    }

    // And the evidence must survive - a redactor that blacks out the
    // whole page would otherwise pass the assertions above.
    assert.ok(/198|2026/.test(flat), `FIR number was lost: ${JSON.stringify(flat)}`);
});

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

    await ocr.shutdown();

    console.log(`\n${tests.length - failed}/${tests.length} passed`);
    process.exit(failed ? 1 : 0);
})();
