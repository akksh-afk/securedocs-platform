const { PDFDocument, StandardFonts, rgb, degrees } = require("pdf-lib");

// ---------------------------------------------------------------
// Visible export watermarking.
//
// Every released copy carries who pulled it, when, and from which
// case. The X-Released-To header already records this; the watermark
// puts it on the page itself so a photograph or a printout of a leaked
// document still points back to one officer.
//
// Visible only. This is NOT steganographic or invisible watermarking -
// do not claim that it is. A visible mark is buildable and honest, and
// it survives the screenshot-and-forward path that leaks actually take.
// ---------------------------------------------------------------

// Helvetica's WinAnsi encoding cannot encode Devanagari. An officer
// named in Hindi would otherwise throw here and break the download,
// so anything outside the encodable range is stripped rather than
// allowed to fail the request.
function toWinAnsi(text) {
    return String(text || "")
        .replace(/[^\x20-\x7E\xA0-\xFF]/g, "")
        .trim();
}

function buildLabel({ serviceNumber, name, caseNumber, timestamp }) {
    const parts = [
        toWinAnsi(serviceNumber),
        toWinAnsi(name),
        toWinAnsi(caseNumber),
        toWinAnsi(timestamp),
    ].filter(Boolean);

    return parts.join("  |  ") || "RELEASED COPY";
}

/**
 * Stamp a diagonal, low-opacity mark across every page.
 *
 * Returns the buffer unchanged for anything that is not a PDF. Images
 * and office formats need their own renderers and this is the export
 * path - a download must not fail because we could not decorate it.
 *
 * ponytail: PDF only. Rasterise-and-stamp for images if the demo needs
 * watermarked JPEG evidence photos.
 */
async function apply(buffer, mimeType, meta) {
    if (mimeType !== "application/pdf") return buffer;

    const label = buildLabel(meta);

    // The whole pipeline is guarded, not just load(). A malformed PDF
    // parses without complaint and then throws on getPages(), so
    // catching only the load leaves a corrupt file 500-ing the download
    // instead of being released.
    try {
        const pdfDoc = await PDFDocument.load(buffer, { ignoreEncryption: true });
        const font = await pdfDoc.embedFont(StandardFonts.Helvetica);
        const size = 16;
        const width = font.widthOfTextAtSize(label, size);

        for (const page of pdfDoc.getPages()) {
            const { width: pw, height: ph } = page.getSize();

            // Three passes up the page. One diagonal across the middle is
            // trivially cropped out; a tiled mark is not.
            for (let i = 0; i < 3; i++) {
                page.drawText(label, {
                    x: 40,
                    y: ph * (0.18 + i * 0.3),
                    size,
                    font,
                    color: rgb(0.55, 0.1, 0.1),
                    opacity: 0.16,
                    rotate: degrees(35),
                    maxWidth: Math.max(pw, width) * 2,
                });
            }
        }

        return Buffer.from(await pdfDoc.save());
    } catch (e) {
        // A PDF we cannot stamp still gets released - the audit entry
        // and the X-Released-To header remain the traceability record.
        console.error("watermark: could not stamp PDF, releasing unmarked", e.message);
        return buffer;
    }
}

module.exports = { apply, buildLabel, toWinAnsi };
