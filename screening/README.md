# AI-Based Fake Identity & Document Screening System — SIH 26188

Screens passports, visas, national ID cards, driving licences and residence/work permits at a border
checkpoint in a few seconds. It reads every document, validates it against ICAO 9303 and screening policy,
looks for forgery, matches the holder's face, checks blacklists and duplicate identities, and returns an
explainable risk score with a recommended action. Every decision is written to a tamper-evident audit trail.

> All documents in this repository are **synthetic and watermarked** ("SYNTHETIC SPECIMEN"), issued by the
> ICAO specimen state *Utopia* (`UTO`). No real identity data is used.

## Results

Measured on 2026-09-17 (RTX 5060 laptop). Full details: `outputs/BENCHMARK.md`, `outputs/benchmark_report.json`.

**Original sample set** (`samples/generated`, 162 passport captures; never used for training or model selection,
and its MRZ font is held out from training):

| Metric | Original build | This build |
|---|---|---|
| Fully correct MRZ reads (156 readable captures) | 48 (30.8%) | **151 (96.8%)** |
| Forged documents detected (72) | 0 | **18 (25.0%)** — all 18 altered expiry dates |
| False forgery alarms (90 genuine captures) | 6 | **1** |
| Time per document | 9.5 s | **1.2 s** |
| Acceptance script / pytest | FAIL / 1 failed | **PASS / 31 passed** |

The legacy set's other forgeries (blacked-out photo, a drawn stamp circle, extra text beside the passport number)
are **not** detected. Two rules that helped here — "printed dates must lie within the MRZ birth-to-expiry range" and
"directional blur makes an MRZ read unreliable" — were added after the first benchmark run exposed those failures.

**Fresh synthetic captures** (150 documents, all five types, random capture conditions):

| | All | Severity 0 | Severity 3 |
|---|---|---|---|
| Document type classified | 96.7% | 97.7% | 89.7% |
| MRZ fully exact (every character, incl. names) | 77.5% | 94.1% | 61.3% |
| Printed-field accuracy | 60.9% | 82.1% | 43.9% |

Printed fields on clean scans: 81.9%. Driving licences on captures remain weak (19% field accuracy) because their
small labels do not survive noise.

**Forgery detection model** (600 unseen synthetic test documents): AUC 0.77; at a 1.6% false-alarm rate it
detects 77% of photo replacements, 44% of fabricated stamps and 28% of cloned stamps, but only 11–15% of subtle
text/date/MRZ edits. Those are covered instead by MRZ check digits, MRZ ↔ printed-zone comparison and the
printed-date rule. Stamp copy-move detection: 45/60 cloned stamps found, 1/60 false alarms.

**Face verification**: 10/10 genuine pairs matched (lowest similarity 0.92, threshold 0.363). Impostor rejection
could not be measured offline (only one real face photo available).

**Speed** (whole screening incl. face match, registry and audit write): median 1.4 s on GPU, 2.5 s CPU-only
(p95 2.1 s / 3.2 s).

## Requirement coverage

| Requirement | Implementation |
|---|---|
| **M1 OCR** – passport, visa, national ID, driving licence, permit | `src/mrz_reader.py` trained neural MRZ reader (all ICAO formats: TD1, TD2, TD3, MRV-A, MRV-B); `src/viz_ocr.py` printed-zone field extraction; `src/document.py` document detection, rectification, type classification |
| Passport: name, number, nationality, DOB, expiry, gender | MRZ (check-digit protected) + printed zone |
| Visa: number, type, entry validation, stay duration | Visa MRZ + printed fields `visa_type`, `number_of_entries`, `valid_from`, `duration_of_stay_days`, linked `passport_number` |
| **M2 Validation** against official standards | `src/validation.py`: ICAO check digits (field + composite), ICAO country codes, document-number formats, date logic, expiry, validity-period limits, MRZ ↔ printed-zone consistency, visa entry window / entries used / permitted stay, visa ↔ passport link |
| **M3 Tampering** – photo replacement, text manipulation, stamp forgery, metadata | `src/tampering.py`: trained forgery-localisation U-Net, stamp copy-move detection, EXIF/XMP/JPEG metadata forensics, semantic evidence (MRZ contradictions, ghost-image mismatch) |
| **M4 Face verification** | `src/face_verification.py`: YuNet + SFace, document portrait vs live capture, ghost-image check |
| Expired / blacklisted documents, multiple identities | `src/registry.py`: lost/stolen, revoked, blacklist, wanted lists; same face with different identity; one document number used by different people |
| Risk score, standardised decisions | `src/risk.py` + `config/policy.json` (versioned; every decision records the policy version) |
| Digital trail | `src/audit.py`: SHA-256 hash-chained append-only log with verification |
| Better input at the counter | `src/document.py` capture tips (glare over the MRZ, blur, darkness, perspective, distance) and a `RECAPTURE` disposition when a read is unreliable. A trained read-success predictor (`src/capture_advisor.py`) reached only AUC 0.60 and is gated off below AUC 0.75 |

## Quick start (Windows)

```powershell
pip install -r requirements.txt
# PyTorch runs the trained networks (GPU build shown; CPU build also works)
pip install torch --index-url https://download.pytorch.org/whl/cu128
python run_tests.py          # acceptance samples
python -m pytest -q          # unit + API integration tests
python generate_demo_documents.py
uvicorn app:app --port 8000  # officer console at http://127.0.0.1:8000
```

Tesseract OCR must be installed for printed-zone OCR (`TESSERACT_CMD` can point to it). Face models
(`models/face_detection_yunet_2023mar.onnx`, `models/face_recognition_sface_2021dec.onnx`) come from
[OpenCV Zoo](https://github.com/opencv/opencv_zoo) (Apache-2.0).

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/screen` | Multipart: `document` (required), `live_photo`, `companion` (e.g. the visa), `document_type`, `checkpoint_id`, `officer_id`, `entry_date`, `intended_stay_days`, `include_images` |
| `GET` | `/health` | Model availability, policy version, registry counts, audit-chain status |
| `GET/POST` | `/api/v1/watchlist` | List or add lost/stolen, revoked, blacklist, wanted entries |
| `DELETE` | `/api/v1/watchlist/{id}` | Deactivate an entry (audited) |
| `GET` | `/api/v1/audit`, `/api/v1/audit/verify` | Read the audit trail; verify the hash chain |

The response contains `risk` (score, band, disposition `CLEAR` / `SECONDARY_INSPECTION` /
`REFER_TO_SUPERVISOR` / `RECAPTURE`, and the reasons), and per document: classification, capture guidance,
extracted fields with their source (MRZ or printed zone), every validation check, tampering findings per use
case with image regions, watchlist hits and timings.

## Training the models

Everything is reproducible from synthetic data (`synthdocs/`): five document types, eight forgery types with
pixel masks, and thirteen capture conditions (low light, glare, shadow, blur, noise, JPEG, low resolution,
perspective, occlusion, rotation).

```powershell
python -m training.mrz_data --out data/mrz_lines --train 160000      # MRZ text lines
python -m training.train_mrz_reader --data data/mrz_lines --epochs 14
python -m training.tamper_data --out data/tamper --train 7000 --val 800
python -m training.train_tamper --epochs 25
python -m training.fit_tamper_aggregator
python -m training.train_capture_advisor --n 1500
python -m evaluation.benchmark
```

| Model | Architecture | Output |
|---|---|---|
| MRZ reader | Fully convolutional CTC recogniser, 3.1 M parameters, 32×512 line input. Round 2 deployed: added slashed/dotted zeros and 35 fonts after round 1 failed on the unseen font; round 3 (motion-blur augmentation) did not beat it on the selection set | `models/mrz_reader.pt`, `models/history/` |
| Forgery localiser | U-Net with fixed SRM noise-residual filters, 2.9 M parameters. v2: v1 learned "stamp = forged" from trace-free synthetic forgeries and was discarded | `models/tamper.pt` |
| Forgery decision | Logistic regression over heatmap statistics, threshold at 3% validation false alarms | `models/tamper_aggregator.joblib` |
| Capture advisor | Gradient-boosted trees on 15 capture-quality features (gated off: AUC 0.60) | `models/capture_advisor.joblib` |

`python -m training.train_mrz_reader --init models/mrz_reader.pt` fine-tunes the deployed reader. The generated
datasets under `data/` (about 5.6 GB) are only needed for retraining and can be deleted. PyTorch weights run
through `src/nets.py`; installing the `onnx` package enables ONNX export for a PyTorch-free deployment.

Held-out evaluation: the Consolas MRZ font (used by the original sample set) is never used in training, and
the original `samples/generated` set was never used for training or model selection.

## Limitations

* Trained and measured on synthetic documents only. Before deployment, fine-tune and recalibrate on
  authorised real data (e.g. MIDV-2020, see `dataset_sources.md`) and genuine document specimens.
* Face verification uses a published model; impostor rates were not measured locally because no consented
  face dataset is available offline.
* The registry is a local SQLite stand-in for authorised government databases, and the audit trail stores
  personal data: production use needs encryption at rest, access control and retention policy.
* The system recommends; the officer decides.
