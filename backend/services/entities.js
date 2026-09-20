// ---------------------------------------------------------------
// Rules-based entity extraction.
//
// This is the regex half of F4. It ships ahead of the model half on
// purpose: for structured fields - FIR numbers, statute references,
// phone numbers - rules beat any NER model and are auditable, which
// matters when a judge asks why something was or was not redacted.
//
// Two groups, and the difference is the whole point:
//
//   REDACT  things that identify a victim (phone, Aadhaar, email)
//   KEEP    things a court needs (FIR number, sections, dates)
//
// A redactor that eats the FIR number and the statute references has
// destroyed the document's evidentiary value. Keeping them is as much
// a requirement as removing the identity.
// ---------------------------------------------------------------

// ---- identifying, must be removed ----

// Indian mobile numbers, with or without a +91 country code.
const PHONE = /(?:\+?91[-\s]?)?\b[6-9]\d{9}\b/g;

// Aadhaar is 12 digits, usually grouped 4-4-4.
const AADHAAR = /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/g;

const EMAIL = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g;

// ---- evidentiary, must be preserved ----

// Two forms, both common and both seen on real paperwork:
//   FIR/0142/2026          the slashed form
//   FIR No. 142 of 2026    the longhand form, which is what most
//                          station registers and OCR'd scans produce
const FIR_NUMBER =
    /\bFIR\s*(?:No\.?|Number)?\s*\d{1,5}\s*(?:of|\/|-)\s*\d{4}\b|\b(?:FIR[\/\s-]?)?\d{1,5}\/\d{4}\b/gi;

// BNS / BNSS / BSA / IPC / CrPC references, and bare "Section 115(2)".
const SECTION =
    /\b(?:BNS|BNSS|BSA|IPC|CrPC)\s*(?:S(?:ec)?\.?|Section)?\s*\d+[A-Z]?(?:\(\d+\))?|\bSection\s+\d+[A-Z]?(?:\(\d+\))?/gi;

const DATE =
    /\b\d{1,2}[\/-]\d{1,2}[\/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b/g;

// ---- contextual identity patterns (the F4 layer that is not a model) ----
//
// No NER model here, and that is a deliberate trade rather than a
// shortcut. FIRs and statements are formulaic: the victim is introduced
// by a role word or a relationship abbreviation, almost always in the
// same handful of shapes. Matching those shapes is auditable - you can
// show a judge the rule that fired - where a model's answer is not.
//
// A name is one to four capitalised words, or a run of Devanagari.
const NAME = "((?:[A-Z][\\p{L}]+[ \\t]+){0,3}[A-Z][\\p{L}]+|[\\p{Script=Devanagari}]+(?:[ \\t]+[\\p{Script=Devanagari}]+){0,3})";

// The separator is [ \t] and never \s. A name sits on the same line as
// the word that introduces it. Allowing \s let the pattern step over a
// line break and grab whatever the next line began with, so a real form
// reading "reasons for delay ... by the complainant/informant:" then
// "No Delay" on the next line produced the person "No Delay", and a
// signature block produced "Signature". On a thirty-page document that
// noise was the entire output - the actual names were never found, and
// redaction dutifully blacked out the wrong words.
const GAP = "[ \\t]*[:\\-]?[ \\t]*";

const ROLE_NAME = new RegExp(
    `\\b(?:complainant|victim|informant|prosecutrix|deceased)${GAP}${NAME}`,
    "giu"
);

const RELATION_NAME = new RegExp(
    `\\b(?:s\\/o|d\\/o|w\\/o|c\\/o|son of|daughter of|wife of|husband of|father of|mother of)${GAP}${NAME}`,
    "giu"
);

const ADDRESS = /\b(?:r\/o|resident of|residing at|address)\s*[:\-]?\s*([^\n.;]{4,70})/giu;

function matchAll(text, re) {
    // Fresh lastIndex each time - these are module-level /g regexes and
    // reusing them statefully across calls silently skips matches.
    const out = [];
    const rx = new RegExp(re.source, re.flags);
    let m;
    while ((m = rx.exec(text)) !== null) {
        if (m[0]) out.push({ value: m[0], start: m.index, end: m.index + m[0].length });
        if (m.index === rx.lastIndex) rx.lastIndex++;
    }
    return out;
}

const uniq = (spans) => [...new Set(spans.map((s) => s.value))];

// Capture group 1 rather than the whole match: we want the name, not
// the role word that introduced it.
function captureAll(text, re) {
    const out = [];
    const rx = new RegExp(re.source, re.flags);
    let m;
    while ((m = rx.exec(text)) !== null) {
        const value = (m[1] || "").trim().replace(/\s+/g, " ");
        if (value.length > 1) out.push(value);
        if (m.index === rx.lastIndex) rx.lastIndex++;
    }
    return [...new Set(out)];
}

// Role words that get captured as if they were names when two of them
// sit next to each other ("Complainant Victim Sunita").
const NOT_A_NAME = new Set([
    "the", "and", "said", "above", "named", "aforesaid",
    "complainant", "victim", "informant", "accused", "police", "station",
    "inspector", "sub", "constable", "head", "section", "fir",
]);

// A name is written with capitals. The patterns that find names are
// case-insensitive so they match "COMPLAINANT" and "Complainant"
// alike, but that same flag also lets [A-Z] match lowercase - so
// "the complainant stated that the accused" captured "stated that the
// accused" as a person and redaction blanked it out of the sentence.
// The capitalisation rule therefore has to be enforced here rather
// than in the pattern.
const startsUpper = (w) =>
    /^[\p{Lu}]/u.test(w) || /^[\p{Script=Devanagari}]/u.test(w);

// Keep only the opening run of capitalised words.
//
// The capture is greedy and runs on past the name into whatever follows:
// "complainant Sunita Sharma stated" hands back "Sunita Sharma stated".
// Trimming at the first lowercase word gives the name; a capture that
// begins with a lowercase word - "stated that the accused" - trims to
// nothing and is dropped, which is what should happen to prose.
function trimToName(value) {
    const words = String(value).split(/\s+/).filter(Boolean);
    const kept = [];
    for (const w of words) {
        if (!startsUpper(w)) break;
        kept.push(w);
    }
    return kept.join(" ");
}

function looksLikeName(value) {
    const words = String(value).split(/\s+/).filter(Boolean);

    // At least two words. A person on an FIR is written with a given
    // name and a family name; a single capitalised word after a role
    // word is almost always the form itself - "Signature", "Major",
    // "Particulars". Requiring two is what separates a name from a
    // heading, and the cost is a genuinely single-word name, which the
    // officer can register on the case explicitly.
    if (words.length < 2 || words.length > 4) return false;

    // Reject if every word is a stop word - a real name has at least one
    // token that is not vocabulary.
    return words.some((w) => !NOT_A_NAME.has(w.toLowerCase()));
}

/**
 * Identities named by the structure of the document rather than found
 * by a model. Feeds redaction as a safety net beneath the identities an
 * officer registered explicitly - it is additive, never the only source.
 */
function contextualIdentities(text) {
    const src = String(text || "");

    const persons = [
        ...captureAll(src, ROLE_NAME),
        ...captureAll(src, RELATION_NAME),
    ]
        .map(trimToName)
        .filter(looksLikeName);

    return {
        persons: [...new Set(persons)],
        addresses: captureAll(src, ADDRESS),
    };
}

/**
 * Pull every rules-detectable entity out of a block of text.
 * Returns the shape described in migration 002 so F4's model layer can
 * merge into the same object.
 */
function extract(text) {
    const src = String(text || "");
    const contextual = contextualIdentities(src);

    return {
        // identifying - these feed redaction
        persons: contextual.persons,
        addresses: contextual.addresses,
        phones: uniq(matchAll(src, PHONE)),
        emails: uniq(matchAll(src, EMAIL)),
        aadhaar: uniq(matchAll(src, AADHAAR)),

        // evidentiary - these are kept, never redacted
        fir_numbers: uniq(matchAll(src, FIR_NUMBER)),
        sections: uniq(matchAll(src, SECTION)),
        dates: uniq(matchAll(src, DATE)),

        extracted_by: "rules",
    };
}

/** Spans that identify someone and must be removed on a protected export. */
function identifyingSpans(text) {
    return [
        ...matchAll(text, PHONE),
        ...matchAll(text, EMAIL),
        ...matchAll(text, AADHAAR),
    ];
}

/** Spans a court needs, which redaction must never eat. */
function preservedSpans(text) {
    return [
        ...matchAll(text, FIR_NUMBER),
        ...matchAll(text, SECTION),
        ...matchAll(text, DATE),
    ];
}

module.exports = {
    extract,
    contextualIdentities,
    identifyingSpans,
    preservedSpans,
    matchAll,
    patterns: { PHONE, AADHAAR, EMAIL, FIR_NUMBER, SECTION, DATE, ROLE_NAME, RELATION_NAME, ADDRESS },
};
