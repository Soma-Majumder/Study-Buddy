"""Log initial offline fixture scores. No provider calls or publication changes."""
from contextlib import closing
from datetime import datetime, timezone
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['STUDY_BUDDY_ALLOW_PAID_CLAUDE']='false'
from scripts.check_backend_offline import SCENARIOS
from scripts.validate_quiz import check
from study_buddy.option_balance import option_balance_issues
from study_buddy.quality_review import candidate_hash, validate_review, ReviewRejected


def main():
    fixture_dir=ROOT/'tests/fixtures/quiz_quality'
    context=json.loads((fixture_dir/'context.json').read_text())
    balance=[];handoffs=[]
    fixtures=[fixture_dir/name for name in ('pass.json','ambiguous_answers.json','inaccurate_rationale.json','uncertain.json','stale_hash.json')]
    fixtures+=sorted(fixture_dir.glob('benchmark_*.json'))
    for path in fixtures:
        case=json.loads(path.read_text());ctx=copy.deepcopy(context)
        if 'candidate_override' in case:ctx['candidate']=case['candidate_override']
        quiz=ctx['candidate'];errors=check(quiz,ctx['handoff'])
        binding=dict(fixture=path.name,run_type='fixture',operation_id=ctx['operation_id'],
                     candidate_version=ctx['candidate_version'],candidate_hash=candidate_hash(quiz))
        handoffs.append(dict(binding,route='Assessment → Reviewer',accepted=not errors))
        if errors:
            balance.append(dict(binding,status='invalid_candidate',flagged_positions=[]))
            continue
        flags=option_balance_issues(quiz)
        balance.append(dict(binding,status='flagged' if flags else 'no_length_flags',
                            question_count=len(quiz['quiz']['questions']),threshold=1.3,flags=flags))
        try:
            verdict=validate_review(case['report'],ctx)
            handoffs.append(dict(binding,route='Reviewer → controller',accepted=True,content_decision=verdict['decision']))
        except ReviewRejected:
            handoffs.append(dict(binding,route='Reviewer → controller',accepted=False,failure_reason='report_validation_rejected'))

    sessions=[];test_result=unittest.TestResult()
    def observe(test):
        def read(conn):
            status=conn.execute('SELECT status FROM study_sessions').fetchone()[0]
            saved=conn.execute('SELECT summary FROM session_summaries').fetchone()
            summary=json.loads(saved[0]) if saved else None
            sessions.append(dict(test=test.id(),run_type='fixture',status=status,summary_saved=saved is not None,
                end_to_end_success=status=='completed' and saved is not None,
                scores=[a['score'] for a in summary['attempts']] if summary else []))
        if hasattr(test,'path'):
            with closing(sqlite3.connect(test.path.as_uri()+'?mode=ro',uri=True)) as conn:read(conn)
        else:read(test.conn)
    def attach(suite):
        for test in suite:
            if isinstance(test,unittest.TestSuite):attach(test);continue
            teardown=test.tearDown
            def finish(test=test,teardown=teardown):
                try:observe(test)
                finally:teardown()
            test.tearDown=finish
    for _,_,names in SCENARIOS:
        suite=unittest.defaultTestLoader.loadTestsFromNames(names);attach(suite);suite.run(test_result)
    if not test_result.wasSuccessful() or test_result.skipped or len(sessions)!=4:
        for test,error in test_result.errors+test_result.failures:print(str(test)+'\n'+error,file=sys.stderr)
        print('Fixture execution failed; scores not logged as valid.',file=sys.stderr);return 1
    totals={}
    for route in ('Assessment → Reviewer','Reviewer → controller'):
        items=[h for h in handoffs if h['route']==route]
        totals[route]=dict(accepted=sum(h['accepted'] for h in items),attempted=len(items))
    successes=sum(s['end_to_end_success'] for s in sessions)
    report=dict(created_at=datetime.now(timezone.utc).isoformat(),run_type='fixture',paid_calls=0,
        end_to_end=dict(successful=successes,attempted=len(sessions),rate=successes/len(sessions),
            note='Includes the intentionally blocked scenario as an end-to-end failure; expected safeguard tests all passed.'),
        sessions=sessions,balance=balance,handoff_validation=handoffs,handoff_totals=totals,
        per_agent_contribution=dict(status='not_measured',revision_recovery_rate=None,
            reason='No agent generation or paired before/after agent revision was run. Authored Reviewer fixtures do not measure Reviewer detection accuracy.'),
        limitations=['Only fixture contract validation, not live delivery or educational quality.',
            'Tutor → Assessment and feedback → Tutor handoff rates are not instrumented here.',
            'Cases can reuse identical candidates; repeated hashes are not independent quiz samples.',
            'Length flags are heuristic warnings, not proof of answer clues or approval. Runtime gate unchanged.'])
    folder=ROOT/'backend/evals/results';folder.mkdir(exist_ok=True)
    path=folder/(report['created_at'].replace(':','-')+'-fixture-scores.json')
    with path.open('x') as f:os.chmod(path,0o600);json.dump(report,f,indent=2)
    valid=[x for x in balance if x['status']!='invalid_candidate']
    print(f'Fixture sessions completed: {successes}/{len(sessions)} ({successes/len(sessions):.0%}); 4/4 expected scenario checks passed.')
    for row in balance:
        print(row['fixture']+': '+row['status']+'; flagged positions '+str([i['position'] for i in row.get('flags',[])]))
    print(f'Balance: {sum(x["status"]=="no_length_flags" for x in valid)}/{len(valid)} fixture cases without length flags.')
    for route,total in totals.items():print(f'{route}: {total["accepted"]}/{total["attempted"]} accepted fixture validations.')
    print('Per-agent contribution: not measured; no paid calls. Live handoff rates not measured.')
    print('Saved local scores: '+str(path));return 0


if __name__=='__main__':raise SystemExit(main())
