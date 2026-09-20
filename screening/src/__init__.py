"""AI-based fake identity and document screening system (SIH 26188).

Modules are imported lazily by callers; importing the package itself stays cheap so that data
generation workers do not load OCR engines or neural networks they never use."""
