# Benchmark report

Generated 2026-09-17 00:50 on NVIDIA GeForce RTX 5060 Laptop GPU. All documents are synthetic.

## Original sample set (never used for training; unseen MRZ font)

| Metric | Original system | This system |
|---|---|---|
| Fully correct MRZ reads (readable captures) | 48/156 (30.8%) | 151/156 (96.8%) |
| Forged documents detected | 0/72 | 18/72 (25.0%) |
| False forgery alarms on genuine captures | 6/90 | 1/90 (1.1%) |
| Seconds per document | 9.52 | 1.219 |

Detection by forgery type: {'date_patch': '18/18', 'photo_patch': '0/18', 'stamp_clone': '0/18', 'text_patch': '0/18'}

## Forgery localiser on held-out synthetic test documents

600 documents, AUC 0.7426, recall 31.5% at 2.6% false alarms (threshold 0.9943 calibrated on the validation split), pixel IoU 0.252.

| Forgery type | Recall % |
|---|---|
| date_replace | 25.0 |
| glyph_copy_move | 14.6 |
| legacy_patch | 89.5 |
| mrz_edit | 15.6 |
| photo_replace | 55.9 |
| stamp_clone | 16.0 |
| stamp_forge | 37.2 |
| text_replace | 14.9 |

## Fresh synthetic captures (all document types, random capture conditions)

End-to-end document analysis latency: p50 1051 ms, p95 1640 ms.

| Slice | Documents | Type classified % | MRZ exact % | Field accuracy % |
|---|---|---|---|---|
| all | 150 | 96.7 | 77.5 | 60.9 |
| driving_licence | 30 | 93.3 | None | 19.3 |
| holdout_font | 0 | None | 83.3 | None |
| national_id | 30 | 96.7 | 70.0 | 77.1 |
| passport | 30 | 96.7 | 86.7 | 75.8 |
| residence_permit | 30 | 100.0 | 73.3 | 66.7 |
| severity_0 | 43 | 97.7 | 94.1 | 82.1 |
| severity_1 | 29 | 100.0 | 86.4 | 66.8 |
| severity_2 | 39 | 100.0 | 69.7 | 49.3 |
| severity_3 | 39 | 89.7 | 61.3 | 43.9 |
| train_font | 0 | None | 71.7 | None |
| visa | 30 | 96.7 | 80.0 | 63.1 |

## Face verification

Genuine pairs matched: 100.0% (lowest similarity 0.916, threshold 0.363). Only one real face photo is available offline (matplotlib sample); impostor rates need a consented face dataset. SFace published LFW accuracy: 99.40%.
