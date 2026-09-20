const { createCanvas } = require("@napi-rs/canvas");

// ---------------------------------------------------------------
// PDF reading and rendering.
//
// pdfjs-dist ships as ESM and this project is CommonJS, so it is
// loaded with a dynamic import the first time it is needed and then
// cached. That also keeps it out of the startup path for deployments
// that never touch a PDF.
// ---------------------------------------------------------------

let pdfjsPromise = null;
function pdfjs() {
    if (!pdfjsPromise) {
        pdfjsPromise = import("pdfjs-dist/legacy/build/pdf.mjs");
    }
    return pdfjsPromise;
}

async function open(buffer) {
    const lib = await pdfjs();
    // A fresh copy: pdfjs takes ownership of the array it is given and
    // detaches it, which corrupts the caller's buffer.
    const data = new Uint8Array(Buffer.from(buffer));
    // destroy() lives on the loading task, not the document, and it is
    // what actually frees the worker - so both are returned.
    const task = lib.getDocument({
        data,
        useSystemFonts: true,
        // Never run anything the document asks for.
        isEvalSupported: false,
    });

    return { lib, task, doc: await task.promise };
}

/**
 * The text layer, if the PDF has one.
 *
 * A PDF made by a word processor carries its text and this returns it
 * exactly - far better than running OCR over a picture of perfect text.
 * A scanned PDF is just images and this returns almost nothing, which
 * is how the caller knows to fall back to OCR.
 */
async function extractText(buffer) {
    const { task, doc } = await open(buffer);
    const pages = [];

    for (let n = 1; n <= doc.numPages; n++) {
        const page = await doc.getPage(n);
        const content = await page.getTextContent();

        // Line breaks have to survive. Joining every run with a space
        // turns the page into one long line, and the entity rules then
        // read straight across what were separate lines - "Complainant
        // Sunita Sharma" followed by "Daughter of Ramesh Sharma" became
        // the name "Sunita Sharma Daughter". That name matches nothing
        // when redaction goes looking for it, so the real name was left
        // on the page.
        let text = "";
        for (const item of content.items) {
            if (item.str) text += item.str;
            if (item.hasEOL) text += "\n";
            else if (item.str && !item.str.endsWith(" ")) text += " ";
        }

        pages.push(
            text
                .split("\n")
                .map((line) => line.replace(/[ \t]+/g, " ").trim())
                .join("\n")
                .replace(/\n{3,}/g, "\n\n")
                .trim()
        );
    }

    await task.destroy();
    return { text: pages.join("\n\n").trim(), pageCount: pages.length };
}

/**
 * Render every page to a PNG.
 * Used for OCR of scanned PDFs and for redaction.
 */
async function renderPages(buffer, scale = 2) {
    const { task, doc } = await open(buffer);
    const out = [];

    for (let n = 1; n <= doc.numPages; n++) {
        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale });

        const canvas = createCanvas(
            Math.ceil(viewport.width),
            Math.ceil(viewport.height)
        );
        const ctx = canvas.getContext("2d");

        // White behind the page - a PDF page is transparent otherwise and
        // OCR on a transparent background reads nothing.
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        await page.render({ canvasContext: ctx, viewport, canvas }).promise;
        out.push({ png: canvas.toBuffer("image/png"), width: canvas.width, height: canvas.height });
    }

    await task.destroy();
    return out;
}

/**
 * Every text fragment with where it sits on the rendered page, in
 * pixels of an image rendered at the same scale.
 *
 * This is what makes redaction possible: it is the missing piece that
 * lets a name be located on the page rather than merely found in a
 * string.
 */
async function textBoxes(buffer, scale = 2) {
    const { lib, task, doc } = await open(buffer);
    const pages = [];

    for (let n = 1; n <= doc.numPages; n++) {
        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale });
        const content = await page.getTextContent();

        const items = [];
        for (const item of content.items) {
            if (!item.str || !item.str.trim()) continue;

            // PDF text space is y-up from the bottom left; the viewport
            // transform converts to y-down device pixels.
            const t = lib.Util.transform(viewport.transform, item.transform);
            const height = Math.hypot(t[2], t[3]) || item.height * scale;
            const width = (item.width || 0) * scale;

            items.push({
                str: item.str,
                // t[5] is the baseline, so the box starts a line above it.
                x: t[4],
                y: t[5] - height,
                width,
                height,
            });
        }

        pages.push({ items, width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
    }

    await task.destroy();
    return pages;
}

/**
 * Render every page with black rectangles painted over the given areas,
 * then build a NEW PDF out of those images.
 *
 * This is the whole reason PDF redaction can be trusted here. The output
 * contains images and nothing else - there is no text layer left to
 * select, copy, or search, because the document was rebuilt rather than
 * drawn over. Covering text in the original PDF would leave the words
 * sitting underneath the black box, which is the failure this avoids.
 *
 * rectsByPage[n] is an array of { x, y, width, height } in the pixel
 * space of a page rendered at this scale.
 */
async function renderRedactedPdf(buffer, rectsByPage, scale = 2) {
    const { PDFDocument } = require("pdf-lib");
    const { task, doc } = await open(buffer);

    const source = await PDFDocument.load(buffer, { ignoreEncryption: true });
    const out = await PDFDocument.create();

    let rasterised = 0;

    for (let n = 1; n <= doc.numPages; n++) {
        const rects = rectsByPage[n - 1] || [];

        // A page with nothing to remove is copied across untouched.
        //
        // Rasterising it would cost seconds, double the file size and
        // throw away its text and its sharpness, all to hide nothing.
        // It buys no safety either: a page whose identities were never
        // detected is equally exposed as a picture. Only pages that are
        // actually being changed pay the price.
        if (rects.length === 0) {
            const [copied] = await out.copyPages(source, [n - 1]);
            out.addPage(copied);
            continue;
        }

        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale });

        const canvas = createCanvas(
            Math.ceil(viewport.width),
            Math.ceil(viewport.height)
        );
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        await page.render({ canvasContext: ctx, viewport, canvas }).promise;

        // Paint out the identified areas before the pixels ever leave
        // this canvas.
        ctx.fillStyle = "#000000";
        for (const r of rects) {
            ctx.fillRect(r.x, r.y, r.width, r.height);
        }

        // JPEG, not PNG. A rendered page of text compresses to a small
        // fraction of the size, and the quality difference is invisible
        // next to a black rectangle. PNG here was doubling the size of
        // every export.
        const image = await out.embedJpg(canvas.toBuffer("image/jpeg", 0.82));

        // Keep the original page size so the document still prints and
        // measures the way the original did.
        const original = page.getViewport({ scale: 1 });
        const newPage = out.addPage([original.width, original.height]);
        newPage.drawImage(image, {
            x: 0,
            y: 0,
            width: original.width,
            height: original.height,
        });

        rasterised++;
    }

    await task.destroy();

    out.setProducer("SecureDocs - redacted export");
    return { buffer: Buffer.from(await out.save()), rasterised };
}

/** Does this PDF carry a usable text layer, or is it a scan? */
function hasUsefulText(text) {
    return typeof text === "string" && text.replace(/\s+/g, "").length >= 20;
}

module.exports = {
    extractText,
    renderPages,
    textBoxes,
    renderRedactedPdf,
    hasUsefulText,
};
