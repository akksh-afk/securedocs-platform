const os = require("os");
const { PDFDocument, StandardFonts, rgb } = require("pdf-lib");

const dsc = require("./dsc");
const { toWinAnsi } = require("./watermark");

// ---------------------------------------------------------------
// Certificate under Section 63(4) of the Bharatiya Sakshya Adhiniyam
// 2023 - the successor to Section 65B of the Evidence Act.
//
// Courts reject electronic evidence over a missing certificate, and
// every field Part A requires is already in the database. This file
// only formats what we already hold; it never asserts anything the
// system has not actually verified.
//
// Part B is the expert's section. It is generated BLANK with a
// signature block because a human expert signs it. Say that out loud
// when demonstrating - knowing where the human step is matters.
// ---------------------------------------------------------------

const A4 = [595.28, 841.89];
const MARGIN = 56;

// The statutory declaration. This is the operative sentence of the
// certificate - it is why a court accepts the output at all.
const OPERATION_STATEMENT =
    "The computer output containing the information was produced by the computer system " +
    "described above during the period over which that system was used regularly to store " +
    "and process information for the purposes of activities regularly carried on over that " +
    "period. Throughout the material part of that period the computer system was operating " +
    "properly. Any respect in which it was not operating properly, or was out of operation, " +
    "was not such as to affect the electronic record or the accuracy of its contents.";

function wrap(text, font, size, maxWidth) {
    const words = toWinAnsi(text).split(/\s+/).filter(Boolean);
    const lines = [];
    let line = "";

    for (const word of words) {
        const candidate = line ? `${line} ${word}` : word;
        if (line && font.widthOfTextAtSize(candidate, size) > maxWidth) {
            lines.push(line);
            line = word;
        } else {
            line = candidate;
        }
    }
    if (line) lines.push(line);
    return lines;
}

// A tiny cursor over one or more pages. Anything that runs past the
// bottom margin starts a new page instead of being silently clipped.
function sheet(pdfDoc, fonts) {
    const state = { page: pdfDoc.addPage(A4), y: A4[1] - MARGIN };
    const usable = A4[0] - MARGIN * 2;

    function room(height) {
        if (state.y - height < MARGIN) {
            state.page = pdfDoc.addPage(A4);
            state.y = A4[1] - MARGIN;
        }
    }

    function text(value, { size = 10, font = fonts.regular, gap = 4, color = rgb(0, 0, 0) } = {}) {
        for (const line of wrap(value, font, size, usable)) {
            room(size + gap);
            state.page.drawText(line, { x: MARGIN, y: state.y - size, size, font, color });
            state.y -= size + gap;
        }
    }

    // Label on the left, value on the right, wrapping under itself.
    function field(label, value) {
        const size = 10;
        const labelWidth = 190;
        const clean = toWinAnsi(value === null || value === undefined || value === "" ? "-" : value);
        const lines = wrap(clean, fonts.regular, size, usable - labelWidth);

        room(size + 6);
        state.page.drawText(toWinAnsi(label), {
            x: MARGIN,
            y: state.y - size,
            size,
            font: fonts.bold,
        });
        state.page.drawText(lines[0] || "-", {
            x: MARGIN + labelWidth,
            y: state.y - size,
            size,
            font: fonts.regular,
        });
        state.y -= size + 6;

        for (const line of lines.slice(1)) {
            room(size + 4);
            state.page.drawText(line, {
                x: MARGIN + labelWidth,
                y: state.y - size,
                size,
                font: fonts.regular,
            });
            state.y -= size + 4;
        }
    }

    function heading(value) {
        room(30);
        state.y -= 10;
        state.page.drawText(toWinAnsi(value), {
            x: MARGIN,
            y: state.y - 12,
            size: 12,
            font: fonts.bold,
        });
        state.y -= 18;
        state.page.drawLine({
            start: { x: MARGIN, y: state.y },
            end: { x: A4[0] - MARGIN, y: state.y },
            thickness: 0.75,
            color: rgb(0.3, 0.3, 0.3),
        });
        state.y -= 12;
    }

    function rule(gap = 26) {
        room(gap + 10);
        state.y -= gap;
        state.page.drawLine({
            start: { x: MARGIN, y: state.y },
            end: { x: MARGIN + 240, y: state.y },
            thickness: 0.75,
            color: rgb(0.2, 0.2, 0.2),
        });
        state.y -= 12;
    }

    return { text, field, heading, rule, state };
}

/**
 * Build the certificate PDF.
 *
 * Every value here comes from the caller, which reads it from the
 * database. Nothing is invented in this file.
 */
async function build(data) {
    const { document, caseRecord, version, producedBy, verification } = data;

    const pdfDoc = await PDFDocument.create();
    const fonts = {
        regular: await pdfDoc.embedFont(StandardFonts.Helvetica),
        bold: await pdfDoc.embedFont(StandardFonts.HelveticaBold),
    };

    const s = sheet(pdfDoc, fonts);
    const producedAt = new Date().toISOString();

    // The certificate attests to this hash, so that is what gets signed.
    const signature = dsc.sign(version.sha256, producedBy.id);

    pdfDoc.setTitle(`BSA S.63 Certificate - ${toWinAnsi(document.title)}`);
    pdfDoc.setProducer("SecureDocs Secure Document Management System");

    s.text("CERTIFICATE UNDER SECTION 63(4)", { size: 15, font: fonts.bold, gap: 6 });
    s.text("BHARATIYA SAKSHYA ADHINIYAM, 2023", { size: 11, font: fonts.bold, gap: 2 });
    s.text("Certificate in respect of an electronic record produced from a computer system.", {
        size: 9,
        gap: 6,
    });

    s.heading("PART A - PARTICULARS OF THE ELECTRONIC RECORD");

    if (document.evidence_number) s.field("Evidence number", document.evidence_number);
    s.field("Document title", document.title);
    s.field("Document type", document.doc_type);
    s.field("Case number", caseRecord.case_number);
    s.field("Case title", caseRecord.title);
    s.field("Version", `${version.version}`);
    s.field("File size (bytes)", `${version.size_bytes}`);
    s.field("Media type", version.mime_type);
    s.field("Produced on", producedAt);
    s.field("Record created on", version.uploaded_at);

    s.heading("INTEGRITY");

    s.field("Hashing algorithm", "SHA-256");
    s.field("Hash of the record", version.sha256);
    s.field("Hash verified on", verification.verified_at);
    s.field("Verification result", verification.verified ? "AUTHENTIC - hash matches" : "MISMATCH");
    s.field("Ledger transaction id", version.ledger_tx_id);
    s.field("Anchored on", version.anchored_at);
    s.field("Anchor status", version.anchor_status);

    s.heading("PARTICULARS OF THE COMPUTER SYSTEM");

    s.field("System", "SecureDocs Secure Document Management System");
    s.field("Host", os.hostname());
    s.field("Platform", `${os.type()} ${os.release()} (${process.arch})`);
    s.field("Runtime", `Node.js ${process.version}`);
    s.field("Storage", "AES-256-GCM envelope encryption, append-only versioning");
    s.field("Audit", "SHA-256 hash-linked append-only log");

    s.heading("PERSON IN CHARGE OF THE COMPUTER SYSTEM");

    s.field("Name", producedBy.name);
    s.field("Rank", producedBy.rank);
    s.field("Service number", producedBy.service_number);
    s.field("Station", producedBy.station);

    s.heading("STATEMENT AS TO OPERATION");

    s.text(OPERATION_STATEMENT, { size: 9.5, gap: 3 });

    s.heading("SYSTEM SIGNATURE");

    s.field("Algorithm", signature.algorithm);
    s.field("Signed at", signature.signed_at);
    s.field("Signature", signature.signature);
    s.text(
        "This signature is applied by the system over the hash above. It is not a substitute " +
        "for the signature in Part B.",
        { size: 8.5, gap: 3 }
    );

    // ---- Part B: deliberately empty. A human signs this. ----
    s.heading("PART B - CERTIFICATE OF THE PERSON IN A RESPONSIBLE OFFICIAL POSITION");

    s.text(
        "To be completed and signed by hand by the person occupying a responsible official " +
        "position in relation to the operation of the relevant device or the management of " +
        "the relevant activities. This section is issued blank by the system.",
        { size: 9.5, gap: 3 }
    );

    s.rule(34);
    s.text("Signature", { size: 9, font: fonts.bold, gap: 10 });

    s.rule(20);
    s.text("Name", { size: 9, font: fonts.bold, gap: 10 });

    s.rule(20);
    s.text("Designation and rank", { size: 9, font: fonts.bold, gap: 10 });

    s.rule(20);
    s.text("Date and place", { size: 9, font: fonts.bold, gap: 10 });

    return Buffer.from(await pdfDoc.save());
}

module.exports = { build, OPERATION_STATEMENT };
