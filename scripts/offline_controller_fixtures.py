"""Deterministic demo agents. Synthetic review labels do not assess teaching quality."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / 'schemas/v1/examples'


class OfflineAgents:
    def __init__(self, emit):
        self.emit = emit
        self.calls = dict(tutor=0, assessment=0, reviewer=0, feedback=0)

    def tutor(self, context):
        self.calls['tutor'] += 1
        self.emit('Tutor: ' + ('reteach missed concepts' if context['round'] == 'retry' else 'explain the lesson'))
        name = 'tutor_to_assessment.retry.example.json' if context['round'] == 'retry' else 'tutor_to_assessment.example.json'
        handoff = json.loads((EXAMPLES / name).read_text())
        handoff['handoff_id'] = context['handoffId']
        handoff['context']['learner_session_id'] = context['sessionId']
        handoff['payload']['explanation_id'] = context['explanationId']
        return dict(handoff=handoff, repairCount=0)

    def assessment(self, handoff, **kwargs):
        self.calls['assessment'] += 1
        self.emit('Assessment: create a 10-question fixture quiz')
        sample = json.loads((EXAMPLES / 'quiz_output.example.json').read_text())
        if handoff['context']['round'] == 'retry':
            focus = {c['concept_id'] for c in handoff['payload']['concepts']}
            pairs = [(q, k) for q, k in zip(sample['quiz']['questions'], sample['answer_key']) if q['concept_id'] in focus]
            questions, keys = [], []
            for i in range(10):
                question, key = copy.deepcopy(pairs[i % len(pairs)])
                question['position'] = key['position'] = i + 1
                questions.append(question)
                keys.append(key)
            sample['quiz']['questions'], sample['answer_key'] = questions, keys
        sample.update(handoff_id=handoff['handoff_id'], explanation_id=handoff['payload']['explanation_id'],
                      round=handoff['context']['round'])
        return sample

    def reviewer(self, context, **kwargs):
        self.calls['reviewer'] += 1
        self.emit('Reviewer: use synthetic passing report (not a content-quality judgment)')
        report = json.loads((ROOT / 'tests/fixtures/quiz_quality/pass.json').read_text())['report']
        report.update(context['controller_context'])
        questions = {q['position']: q for q in context['candidate']['quiz']['questions']}
        keys = {k['position']: k for k in context['candidate']['answer_key']}
        for q in report['questions']:
            q['concept_id'] = questions[q['position']]['concept_id']
            q['defensible_options'] = [keys[q['position']]['correct_option']]
            q['support_quotes'] = [context['teaching']['explanation_text']]
        return report

    def feedback(self, context, **kwargs):
        self.calls['feedback'] += 1
        self.emit('Assessment: explain the controller-graded results')
        return dict(schema_version='1.0', quiz_id=context['quiz_id'], attempt_id=context['attempt_id'],
            feedback=[dict(position=item['position'], explanation=item['rationale']) for item in context['graded']['items']],
            weak_concepts=copy.deepcopy(context['graded']['weak_concepts']), summary='Fixture feedback: keep practicing.')

    def runners(self):
        return dict(tutor=self.tutor, assessment=self.assessment, reviewer=self.reviewer,
                    feedback=self.feedback, revision_assessment=forbidden)


def forbidden(*args, **kwargs):
    raise RuntimeError('Unexpected dispatch: offline scenario stopped.')
