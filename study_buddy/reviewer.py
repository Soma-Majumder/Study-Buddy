"""Private Python -> Node Reviewer bridge; controller owns dispatch and decisions."""
import json
import math
import os
from pathlib import Path
import subprocess
from study_buddy.cancellation import run_agent_process

ROOT = Path(__file__).resolve().parent.parent
STAGES = {'input preparation', 'model call', 'response validation', 'provider adapter'}
CODES = {'live_calls_disabled', 'missing_api_key', 'invalid_input', 'invalid_timeout', 'invalid_output_budget',
         'invalid_effort', 'invalid_output_schema', 'authentication_failed', 'rate_limited',
         'provider_unavailable', 'api_rejected', 'output_truncated', 'incomplete_response',
         'unexpected_response', 'empty_response', 'request_timeout', 'transport_error',
         'adapter_error', 'process_timeout', 'invalid_envelope', 'process_unavailable',
         'invalid_json', 'invalid_report', 'mismatched_report'}


class ReviewerFailure(Exception):
    def __init__(self, stage='provider adapter', code='adapter_error', http_status=None, stop_reason=None):
        super().__init__('Reviewer could not return a valid report.')
        self.stage = stage if isinstance(stage, str) and stage in STAGES else 'provider adapter'
        self.code = code if isinstance(code, str) and code in CODES else 'adapter_error'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.stop_reason = stop_reason if isinstance(stop_reason, str) and stop_reason in (
            'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal', 'unknown') else None


def run_reviewer(context, *, provider_timeout_ms=60_000, process_timeout_seconds=70):
    """Return a private report, not a decision or learner-facing response.

    Pass context from build_private_reviewer_context and timeout allowances
    freshly bounded by the operation deadline. This call does not grant a
    dispatch: the caller must own the original reserved=True result, and must
    validate/store the report or record terminal failure. No local retries.
    """
    if (type(provider_timeout_ms) is not int or not 0 < provider_timeout_ms <= 60_000 or
            type(process_timeout_seconds) not in (int, float) or not math.isfinite(process_timeout_seconds) or
            not provider_timeout_ms / 1000 < process_timeout_seconds <= 70):
        raise ReviewerFailure(stage='input preparation', code='invalid_timeout')
    try:
        process = run_agent_process(
            [os.environ.get('STUDY_BUDDY_NODE', 'node'), str(ROOT / 'backend/reviewer-command.mjs')],
            input=json.dumps(dict(context=context, provider_timeout_ms=provider_timeout_ms), allow_nan=False),
            text=True, capture_output=True, cwd=ROOT, timeout=process_timeout_seconds,
        )
        body = json.loads(process.stdout)
        if not isinstance(body, dict):
            raise ReviewerFailure(code='invalid_envelope')
        if process.returncode != 0 or body.get('ok') is not True:
            error = body.get('error')
            if body.get('ok') is not False or not isinstance(error, dict):
                raise ReviewerFailure(code='invalid_envelope')
            raise ReviewerFailure(error.get('stage'), error.get('code'), error.get('http_status'), error.get('stop_reason'))
        result = body.get('result')
        if not isinstance(result, dict) or not isinstance(result.get('output'), dict):
            raise ReviewerFailure(code='invalid_envelope')
        # The bridge is not the evidence validator. Python's saved-state report
        # validator remains mandatory even after JS's structural checks.
        return result['output']
    except ReviewerFailure:
        raise
    except subprocess.TimeoutExpired:
        raise ReviewerFailure(code='process_timeout') from None
    except OSError:
        raise ReviewerFailure(code='process_unavailable') from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ReviewerFailure(code='invalid_envelope') from None
