"""Explainable risk scoring.

Every finding from every module becomes an evidence item with a category and severity. Within a
category, weights add up to a cap; categories combine like independent probabilities
(1 - prod(1 - p)), so several moderate problems raise risk without any single weak signal
dominating. Critical findings (watchlist hit, face mismatch, broken MRZ composite digit,
identity conflict) force a HIGH band. The output lists the reasons behind the score."""
from __future__ import annotations

from .config import load_policy

DISPOSITIONS = {
    'CLEAR': 'No adverse findings. Standard processing.',
    'SECONDARY_INSPECTION': 'Findings need an officer to examine the document and holder.',
    'REFER_TO_SUPERVISOR': 'Serious findings: hold the traveller and refer for detailed examination.',
    'RECAPTURE': 'The capture is too poor to assess. Re-scan the document before deciding.',
}


def evidence(category: str, severity: str, code: str, message: str, source: str, status: str = 'FAIL', **data) -> dict:
    return dict(category=category, severity=severity, code=code, message=message, source=source, status=status, data=data)


def assess_risk(items: list[dict], *, capture_quality: float | None = None, mrz_expected: bool = False,
                mrz_verified: bool = False, face_checked: bool = False, policy: dict | None = None) -> dict:
    rp = (policy or load_policy())['risk']
    weights, caps = rp['weights'], rp['category_caps']
    per_cat: dict[str, float] = {}
    scored = []
    for it in items:
        w = weights.get(it['severity'], 0) * (0.5 if it.get('status') == 'WARN' else 1.0)
        if w <= 0:
            continue
        cat = it['category']
        per_cat[cat] = min(caps.get(cat, 50), per_cat.get(cat, 0.0) + w)
        scored.append((w, it))
    remaining = 1.0
    for v in per_cat.values():
        remaining *= 1 - min(v, 100) / 100
    score = round(100 * (1 - remaining), 1)

    critical = [it for it in items if it['severity'] == 'critical' and it.get('status', 'FAIL') == 'FAIL']
    bands = rp['bands']
    band = 'HIGH' if score >= bands['HIGH'] else 'MEDIUM' if score >= bands['MEDIUM'] else 'LOW'
    if critical:
        band, score = 'HIGH', max(score, float(bands['HIGH']))

    recapture = capture_quality is not None and capture_quality < rp['recapture_quality_below'] and \
        (mrz_expected and not mrz_verified)
    if band == 'HIGH':
        disposition = 'REFER_TO_SUPERVISOR'
    elif recapture:
        disposition = 'RECAPTURE'
    elif band == 'MEDIUM' or (mrz_expected and not mrz_verified):
        disposition = 'SECONDARY_INSPECTION'
    else:
        disposition = 'CLEAR'

    completeness = []
    if mrz_expected:
        completeness.append(mrz_verified)
    completeness.append(face_checked)
    coverage = round(100 * sum(completeness) / len(completeness), 1) if completeness else 100.0
    reasons = [dict(severity=it['severity'], category=it['category'], code=it['code'], message=it['message'],
                    source=it['source'], status=it.get('status', 'FAIL'))
               for _, it in sorted(scored, key=lambda s: -s[0])]
    return dict(risk_score=score, risk_band=band, disposition=disposition, disposition_meaning=DISPOSITIONS[disposition],
                category_scores={k: round(v, 1) for k, v in sorted(per_cat.items(), key=lambda kv: -kv[1])},
                critical_findings=len(critical), reasons=reasons, assessment_coverage=coverage,
                policy_version=(policy or load_policy()).get('policy_version'))
