"""Backend-only single-call Assessment bridge; controller owns validation/repair."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


class AssessmentFailure(Exception):
    def __init__(self, stage='provider adapter'):
        super().__init__('Assessment could not create a quiz.')
        self.stage = stage


def run_assessment(handoff, *, repair_request=None):
    """Return unvalidated private quiz output, never a learner-facing response.

    Keys remain in the environment. No local retries or writes occur here.
    The caller must supply controller-verified context and reserve repairs.
    """
    try:
        process = subprocess.run(
            [os.environ.get('STUDY_BUDDY_NODE', 'node'), str(ROOT / 'backend/assessment-command.mjs')],
            input=json.dumps(dict(handoff=handoff, repair_request=repair_request)),
            text=True, capture_output=True, cwd=ROOT, timeout=40,
        )
        body = json.loads(process.stdout)
        if not isinstance(body, dict) or process.returncode != 0 or body.get('ok') is not True:
            stage = body.get('error', {}).get('stage') if isinstance(body, dict) else None
            raise AssessmentFailure(stage if stage in ('input preparation', 'model call') else 'provider adapter')
        return body['result']['output']
    except AssessmentFailure:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError):
        raise AssessmentFailure() from None
