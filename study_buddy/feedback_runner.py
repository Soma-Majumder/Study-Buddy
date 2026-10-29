"""Backend-only single-call Assessment feedback bridge; controller owns validation/repair."""
import json
import math
import os
from pathlib import Path
import subprocess
from study_buddy.cancellation import run_agent_process

from study_buddy.assessment import AssessmentFailure

ROOT = Path(__file__).resolve().parent.parent


def run_feedback(context, *, repair_request=None, provider_timeout_ms=60_000, process_timeout_seconds=70):
    """Return unvalidated result-explanation output, never a learner-facing response.

    No local retries or writes occur here. explain_results validates the output
    against the saved grading and owns the single repair.
    """
    if (type(provider_timeout_ms) is not int or not 0 < provider_timeout_ms <= 60_000 or
            type(process_timeout_seconds) not in (int, float) or not math.isfinite(process_timeout_seconds) or
            not provider_timeout_ms / 1000 < process_timeout_seconds <= 70):
        raise AssessmentFailure('input preparation', 'invalid_timeout')
    payload = dict(context=context, repair_request=repair_request)
    if provider_timeout_ms != 60_000:
        payload['provider_timeout_ms'] = provider_timeout_ms
    try:
        process = run_agent_process(
            [os.environ.get('STUDY_BUDDY_NODE', 'node'), str(ROOT / 'backend/feedback-command.mjs')],
            input=json.dumps(payload, allow_nan=False),
            text=True, capture_output=True, cwd=ROOT, timeout=process_timeout_seconds,
        )
        body = json.loads(process.stdout)
        if not isinstance(body, dict) or process.returncode != 0 or body.get('ok') is not True:
            error = body.get('error', {}) if isinstance(body, dict) else {}
            if not isinstance(error, dict):
                raise AssessmentFailure(code='invalid_envelope')
            stage = error.get('stage')
            raise AssessmentFailure(stage if stage in ('input preparation', 'model call') else 'provider adapter',
                                    error.get('code', 'invalid_envelope'), error.get('http_status'), error.get('stop_reason'))
        return body['result']['output']
    except AssessmentFailure:
        raise
    except subprocess.TimeoutExpired:
        raise AssessmentFailure(code='process_timeout') from None
    except OSError:
        raise AssessmentFailure(code='process_unavailable') from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AssessmentFailure(code='invalid_envelope') from None
