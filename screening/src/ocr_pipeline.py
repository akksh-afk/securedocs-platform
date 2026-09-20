"""Module 1: OCR extraction.

``OCRPipeline`` keeps the original build's interface (``process(path) -> OCRResult``) but is now
backed by document rectification, the trained neural MRZ reader, and label-aware printed-zone
OCR covering passports, visas, ID cards, driving licences and permits."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import cv2

from .capture_robustness import quality_metrics
from .document import classify_document, locate_document, rectify
from .mrz_reader import read_mrz
from .runtime_utils import find_tesseract
from .validation import merge_fields
from .viz_ocr import extract_fields


@dataclass
class OCRResult:
    text: str
    confidence: float
    variants_used: int
    mrz: dict[str, Any]
    fields: dict[str, Any]
    warnings: list[str]
    capture_quality: dict[str, Any]
    orientation_degrees: int
    rectified: bool
    engine: str = 'neural-mrz+tesseract-viz'
    engine_confidence: float = 0.0
    mrz_confidence: float = 0.0
    document_type: str = ''
    normalized_fields: dict[str, Any] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0

    def to_dict(self):
        return asdict(self)


class OCRPipeline:
    def __init__(self, tesseract_cmd: str | None = None, fast_mode: bool = False, viz: bool = True):
        self.tesseract_cmd = find_tesseract(tesseract_cmd)
        self.fast_mode = fast_mode
        self.viz = viz and self.tesseract_cmd is not None

    def process(self, path, document_type: str | None = None, today: date | None = None) -> OCRResult:
        t0 = time.perf_counter()
        img = path if not isinstance(path, (str, Path)) else cv2.imread(str(path))
        if img is None:
            raise ValueError(f'Could not read image: {path}')
        q = quality_metrics(img)
        quad = locate_document(img)
        rect, _ = rectify(img, quad)
        read = read_mrz(rect)
        if not read.mrz.overall_check_digit_valid:
            alt = read_mrz(img)
            if alt.confidence > read.confidence:
                read = alt
        m = read.mrz
        viz = extract_fields(rect, document_type) if self.viz and not self.fast_mode else None
        cls = classify_document(m.to_dict(), viz.doc_type_scores if viz else None, rect.shape, document_type)
        normalized, sources = merge_fields(cls['document_type'], m.to_dict(), viz.fields if viz else {}, today or date.today())
        # Legacy field view: MRZ values verbatim (YYMMDD dates) when the MRZ verified.
        fields = dict(viz.fields) if viz else {}
        if m.valid_format and m.overall_check_digit_valid:
            fields.update(passport_number=m.document_number, document_number=m.document_number, nationality=m.nationality,
                          date_of_birth=m.date_of_birth, gender=m.gender, date_of_expiry=m.date_of_expiry,
                          surname=m.surname, given_names=m.given_names)
        warnings = list(q.warnings)
        if not m.valid_format:
            warnings.append('Machine readable zone not found or unreadable.')
        elif not m.overall_check_digit_valid:
            warnings.append('MRZ check digits failed: altered document or misread; manual review required.')
        elif m.repairs:
            warnings.append('MRZ needed check-digit-confirmed glyph repairs; retained for audit.')
        if quad is None:
            warnings.append('Document boundary not detected; the image was assumed to contain only the document.')
        confidence = read.confidence if m.valid_format else (viz.mean_confidence if viz else 0.0)
        return OCRResult(text=(viz.text if viz else '\n'.join(read.raw_lines)), confidence=round(float(confidence), 2),
                         variants_used=read.attempts, mrz=m.to_dict(), fields=fields, warnings=warnings,
                         capture_quality=q.to_dict(), orientation_degrees=read.rotation, rectified=quad is not None,
                         engine_confidence=round(viz.mean_confidence, 2) if viz else 0.0,
                         mrz_confidence=round(read.confidence, 2), document_type=cls['document_type'],
                         normalized_fields=normalized, field_sources=sources,
                         elapsed_ms=round((time.perf_counter() - t0) * 1000))
