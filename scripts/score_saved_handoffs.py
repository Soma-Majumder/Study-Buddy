"""Measure observed handoff acceptance in saved runs; no provider or DB writes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import jsonschema

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['STUDY_BUDDY_ALLOW_PAID_CLAUDE']='false'
SCHEMA=json.loads((ROOT/'schemas/v1/tutor_to_assessment.schema.json').read_text())


def extract(path):
    log=json.loads(path.read_text());stages=log.get('stages',[]);records=[];missing=[]
    for index,stage in enumerate(stages):
        name=stage['stage']
        if name not in ('tutor','reused_live_tutor_output','tutor_reteach','tutor_first','tutor_retry','first_tutor'):continue
        live=name in ('tutor','tutor_reteach') and stage.get('live',True) is not False
        replay=not live
        out=stage.get('output');handoff=out.get('handoff') if isinstance(out,dict) else None
        round_='retry' if name in ('tutor_reteach','tutor_retry') else 'first'
        later=stages[index+1:]
        reached=any(s['stage'] in (('assessment_retry','quiz_retry') if round_=='retry' else ('assessment','quiz_first','first_quiz')) for s in later)
        explicit_rejected=stage.get('status')=='failed'
        if handoff is not None:
            jsonschema.Draft202012Validator(SCHEMA).validate(handoff)
            handoff_id=handoff['handoff_id'];repair=out.get('repairCount')
            digest=hashlib.sha256(handoff['payload']['explanation_text'].encode()).hexdigest()
        else:handoff_id=None;repair=None;digest=None
        accepted=True if reached else False if explicit_rejected else None
        row=dict(route='Tutor → Assessment',run_type='live' if live else 'replay',round=round_,source=str(path),
                 accepted=accepted,technical_repairs=repair,explanation_sha256=digest,
                 handoff_id=handoff_id,evidence='Assessment callback reached' if reached else 'Tutor failed' if explicit_rejected else 'No downstream acceptance evidence')
        if accepted is None:missing.append(row)
        else:records.append(row)

    steps=log.get('result',{}).get('steps',[]) if isinstance(log.get('result'),dict) else []
    routed=any(s.get('step')=='route' and s.get('round')=='first' and s.get('next_step')=='reteach' for s in steps)
    tutor_stage=next((s for s in stages if s['stage'] in ('tutor_reteach','tutor_retry')),None)
    if routed and tutor_stage is not None:
        live=tutor_stage['stage']=='tutor_reteach'
        feedback=next((s for s in stages if s['stage']=='feedback_first'),None)
        output=feedback.get('output') if feedback else None
        handoff=tutor_stage.get('output',{}).get('handoff')
        if output and handoff:
            weak={w['concept_id'] for w in output['weak_concepts']}
            assert weak=={c['concept_id'] for c in handoff['payload']['concepts']},'Saved feedback/reteaching scope differs.'
            assert weak==set(handoff['payload']['focus_concept_ids']),'Saved retry focus differs.'
        records.append(dict(route='Feedback → Tutor',run_type='live' if live else 'replay',round='first_to_retry',source=str(path),
            accepted=True,technical_repairs=None,evidence='Saved route selects reteach and Tutor callback is recorded',
            payload_validation='Saved weak-concept set matches returned taught/focus set' if output and handoff else 'Original handoff envelope not captured in JSON log'))
    elif any(s['stage']=='feedback_first' for s in stages):
        missing.append(dict(route='Feedback → Tutor',source=str(path),accepted=None,evidence='No recorded route plus Tutor dispatch; not counted as an attempted handoff'))
    return records,missing


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('reports',nargs='+',type=Path);args=parser.parse_args()
    records=[];missing=[];seen=set()
    for path in args.reports:
        path=path.resolve()
        if path in seen:continue
        seen.add(path);rows,unknown=extract(path);records+=rows;missing+=unknown
    totals=[]
    for kind in ('live','replay'):
        for route in ('Tutor → Assessment','Feedback → Tutor'):
            rows=[r for r in records if r['run_type']==kind and r['route']==route]
            n=len(rows);success=sum(r['accepted'] for r in rows)
            totals.append(dict(run_type=kind,route=route,accepted=success,attempted=n,rate=success/n if n else None,
                first_attempt_acceptance=sum(r['accepted'] and r['technical_repairs']==0 for r in rows),
                repair_evidence_missing=sum(r['technical_repairs'] is None for r in rows)))
    report=dict(created_at=datetime.now(timezone.utc).isoformat(),paid_calls=0,totals=totals,handoffs=records,unmeasured_or_not_attempted=missing,
        limitations=['Observed controller acceptance from saved stage/route evidence, not a new end-to-end test or full historical request replay.',
            'Original controller inputs and feedback-to-Tutor envelopes were not captured in every JSON log; do not claim independent validation of missing fields.',
            'No eligible handoff attempt is inferred from a quiz-only run or from a stage that never reached routing.',
            'Live vs replay identifies the content at this boundary, not whether the entire parent session was fully live.',
            'Repeated explanations and saved replays are correlated; small historical sample does not establish a reliability estimate.',
            'Unknown repair counts remain unknown; accepted after repair is not first-attempt acceptance.',
            'A successful handoff does not imply the downstream quiz passed content review or the session completed.'])
    folder=ROOT/'backend/evals/results';folder.mkdir(exist_ok=True);path=folder/(report['created_at'].replace(':','-')+'-saved-handoffs.json')
    with path.open('x') as f:os.chmod(path,0o600);json.dump(report,f,indent=2)
    for row in totals:print(f"{row['run_type']} | {row['route']}: {row['accepted']}/{row['attempted']} observed accepted; repair evidence missing: {row['repair_evidence_missing']}")
    print(f'Unmeasured/not-attempted observations: {len(missing)}. No paid calls.\nSaved: {path}')


if __name__=='__main__':main()
