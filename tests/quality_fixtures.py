"""Synthetic passing Reviewer for routing tests, NOT educational-quality labels."""
import json
from pathlib import Path


def passing_review(context, **timeouts):
    report=json.loads((Path(__file__).parent/'fixtures/quiz_quality/pass.json').read_text())['report']
    report.update(context['controller_context'])
    candidate=context['candidate']
    questions={q['position']:q for q in candidate['quiz']['questions']}
    keys={k['position']:k for k in candidate['answer_key']}
    for q in report['questions']:
        q['concept_id']=questions[q['position']]['concept_id']
        q['defensible_options']=[keys[q['position']]['correct_option']]
        q['support_quotes']=[context['teaching']['explanation_text']]
    return report
