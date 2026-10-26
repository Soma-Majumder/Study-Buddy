"""Backend-only single-call Assessment bridge; controller owns validation/repair."""
import json
import math
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


class AssessmentFailure(Exception):
    def __init__(self, stage='provider adapter', code='adapter_error', http_status=None, stop_reason=None):
        super().__init__('Assessment could not create a quiz.')
        self.stage = stage
        allowed = {'missing_api_key', 'invalid_input', 'invalid_timeout', 'invalid_output_budget', 'invalid_effort', 'invalid_output_schema', 'authentication_failed',
                   'rate_limited', 'provider_unavailable', 'api_rejected', 'output_truncated',
                   'incomplete_response', 'unexpected_response', 'empty_response', 'request_timeout',
                   'transport_error', 'adapter_error', 'process_timeout', 'invalid_envelope', 'process_unavailable'}
        self.code = code if code in allowed else 'adapter_error'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.stop_reason = stop_reason if stop_reason in ('max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal', 'unknown') else None


def run_assessment(handoff, *, repair_request=None):
    """Return unvalidated private quiz output, never a learner-facing response.

    Keys remain in the environment. No local retries or writes occur here.
    The caller must supply controller-verified context and reserve repairs.
    """
    return _run_command(dict(handoff=handoff, repair_request=repair_request))


def run_assessment_revision(context, *, technical_repair=False, provider_timeout_ms=60_000, process_timeout_seconds=70):
    """One reserved content revision/correction; controller validates and stores."""
    if (type(technical_repair) is not bool or type(provider_timeout_ms) is not int or not 0 < provider_timeout_ms <= 60_000 or
            type(process_timeout_seconds) not in (int, float) or not math.isfinite(process_timeout_seconds) or
            not provider_timeout_ms / 1000 < process_timeout_seconds <= 70):
        raise AssessmentFailure('input preparation', 'invalid_input')
    return _run_command(dict(revision_context=context, technical_repair=technical_repair, provider_timeout_ms=provider_timeout_ms),
                        process_timeout_seconds)


def _run_command(payload, process_timeout_seconds=70):
    try:
        process = subprocess.run(
            [os.environ.get('STUDY_BUDDY_NODE', 'node'), str(ROOT / 'backend/assessment-command.mjs')],
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
