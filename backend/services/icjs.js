// ---------------------------------------------------------------
// ICJS (Interoperable Criminal Justice System) adapter - MOCK.
//
// There is no API access to CCTNS without a signed MoU and credentials
// issued by NCRB. Rather than pretend otherwise, this adapter speaks
// the real ICJS exchange shapes and is backed by fixtures.
//
// Say this plainly when demonstrating: the connection is mocked, the
// shapes are real, and moving to production needs credentials and an
// endpoint - not a rewrite. Every function here is the seam.
//
// The three exchanges ICJS actually cares about:
//   1. FIR reference lookup      (CCTNS -> us)
//   2. Case linkage              (us <-> CCTNS / eCourts)
//   3. Document metadata push    (us -> CCTNS)
//
// Nothing here transmits a document. Metadata and hashes only.
// ---------------------------------------------------------------

const MOCK = true;

// Fictional records. No real person, FIR, or officer appears here.
const FIR_FIXTURES = {
    "FIR/0142/2026": {
        fir_number: "FIR/0142/2026",
        cctns_id: "MH01-2026-0000142",
        police_station: "Sadar Bazar",
        district: "Central",
        state: "Maharashtra",
        registered_on: "2026-02-11T09:24:00+05:30",
        act: "Bharatiya Nyaya Sanhita, 2023",
        sections: ["115(2)", "352"],
        investigating_officer: {
            name: "Inspector A. Deshmukh",
            service_number: "MH-INS-4471",
            rank: "inspector",
        },
        status: "under_investigation",
        accused_count: 2,
    },
    "FIR/0198/2026": {
        fir_number: "FIR/0198/2026",
        cctns_id: "MH01-2026-0000198",
        police_station: "Sadar Bazar",
        district: "Central",
        state: "Maharashtra",
        registered_on: "2026-03-02T18:05:00+05:30",
        act: "Bharatiya Nyaya Sanhita, 2023",
        // A S.72 case - victim identity is protected by statute.
        sections: ["74", "72"],
        investigating_officer: {
            name: "Sub-Inspector R. Kulkarni",
            service_number: "MH-SI-8820",
            rank: "sub_inspector",
        },
        status: "under_investigation",
        accused_count: 1,
    },
};

/**
 * FIR reference lookup.
 * Production: GET against the CCTNS FIR search service.
 */
async function fetchFir(firNumber) {
    const record = FIR_FIXTURES[String(firNumber).trim()];
    if (!record) return null;

    return { ...record, source: "cctns", mock: MOCK };
}

/**
 * Link one of our cases to an FIR held in CCTNS.
 * Production: a linkage call that returns a durable correlation id.
 */
async function linkCase({ caseNumber, firNumber }) {
    const fir = await fetchFir(firNumber);
    if (!fir) {
        return { linked: false, reason: "fir_not_found", mock: MOCK };
    }

    return {
        linked: true,
        case_number: caseNumber,
        fir_number: fir.fir_number,
        cctns_id: fir.cctns_id,
        correlation_id: `ICJS-LNK-${fir.cctns_id}`,
        linked_at: new Date().toISOString(),
        mock: MOCK,
    };
}

/**
 * Build the exact payload we would push to ICJS for a document.
 *
 * Pure - it sends nothing. Keeping it separate means the demo can show
 * the wire format without a network call, and the shape can be
 * reviewed by anyone who knows ICJS without reading transport code.
 *
 * Deliberately excludes: the file, extracted text, and any entity or
 * victim data. ICJS receives a pointer and a hash, nothing more.
 */
function documentMetadataPayload({ document, caseRecord, version }) {
    return {
        message_type: "DOCUMENT_METADATA",
        version: "1.0",
        source_system: "SecureDocs",
        case: {
            case_number: caseRecord.case_number,
            sensitivity: caseRecord.sensitivity,
        },
        document: {
            external_id: document.id,
            title: document.title,
            doc_type: document.doc_type,
            version: version.version,
        },
        integrity: {
            algorithm: "SHA-256",
            hash: version.sha256,
            ledger_tx_id: version.ledger_tx_id,
            anchored_at: version.anchored_at,
        },
        generated_at: new Date().toISOString(),
        mock: MOCK,
    };
}

/**
 * Push document metadata.
 * Production: POST to the ICJS document exchange endpoint.
 */
async function pushDocumentMetadata(payload) {
    return {
        accepted: true,
        receipt_id: `ICJS-RCP-${Date.now()}`,
        received_at: new Date().toISOString(),
        echo: payload.document.external_id,
        mock: MOCK,
    };
}

module.exports = {
    fetchFir,
    linkCase,
    documentMetadataPayload,
    pushDocumentMetadata,
    MOCK,
};
