"""Synthetic identity-document engine used for training, testing and demos.

Everything produced here is fictional and visibly watermarked as synthetic. Issuers are
the ICAO specimen state "Utopia" (UTO); no layout copies a real country's document.
"""
from .render import SynthDoc, render_document, DOC_TYPES
from .tamper import TAMPER_TYPES, apply_tamper
from .capture import simulate_capture

__all__ = ['SynthDoc', 'render_document', 'DOC_TYPES', 'TAMPER_TYPES', 'apply_tamper', 'simulate_capture']
