"""Compare saved agent revisions offline. Never calls providers or loads manual edits."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['STUDY_BUDDY_ALLOW_PAID_CLAUDE']='false'
from scripts.validate_quiz import check
from study_buddy.option_balance import option_balance_issues
from study_buddy.quality_review import validate_review, candidate_hash, CRITERIA


def output(log, stage):
    return next((s.get('output') for s in log['stages'] if s['stage']==stage and s.get('output') is not None),None)


def blocking_findings(review, verdict):
    """Count reported position/code pairs, not truth or proven revision-caused defects."""
    reasons=set(verdict['reasons']);items=set()
    for q in review['questions']:
        items.update((q['position'],i['code']) for i in q['issues'] if i['code'] in reasons)
        if 'review_uncertainty' in reasons and (q['uncertainty_notes'] or any(q[c]=='uncertain' for c in CRITERIA)):
            items.add((q['position'],'review_uncertainty'))
    items.update((0,i['code']) for i in review['quiz_review']['issues'] if i['code'] in reasons)
    if review['quiz_review']['uncertainty_notes']:items.add((0,'review_uncertainty'))
    return items


def score(path):
    log=json.loads(path.read_text());mode=log.get('mode','')
    if mode=='targeted_revision_then_review':
        source=Path(log['source']);original_log=json.loads(source.read_text())
        h=next(s['handoff'] for s in original_log['stages'] if s['stage']=='controller_validation')
        before=output(original_log,'assessment');before_r=output(original_log,'reviewer')
        after=output(log,'revision');after_r=output(log,'review_revision')
        revision_stage=next(s for s in log['stages'] if s['stage']=='revision')
        kind='saved_agent_candidate'
    elif mode=='saved_approved_first_quiz_live_learning_retry':
        h=output(log,'tutor_reteach')['handoff'];before=output(log,'assessment_retry');before_r=output(log,'review_retry')
        after=output(log,'revision_retry');after_r=output(log,'review_retry_revision')
        revision_stage=next(s for s in log['stages'] if s['stage']=='revision_retry');kind='live_retry_after_saved_first_quiz'
    else:
        revision_stage=next((s for s in log['stages'] if s['stage']=='content_revision'),None)
        if revision_stage is None:return dict(source=str(path),excluded='No agent revision attempted.')
        h=next(s['handoff'] for s in log['stages'] if s['stage']=='controller_validation')
        before=output(log,'assessment');before_r=output(log,'reviewer');after=revision_stage.get('output')
        reviews=[s.get('output') for s in log['stages'] if s['stage']=='reviewer' and s.get('output') is not None]
        after_r=reviews[1] if len(reviews)>1 else None
        kind='live_first_round' if mode=='live_current_prompts_no_fixtures' else 'fixed_saved_tutor'
    def validate(r,c,version):
        return validate_review(r,dict(operation_id=before_r['operation_id'],candidate_version=version,handoff=h,candidate=c))
    first=validate(before_r,before,0)
    if first['decision']!='needs_revision':raise ValueError('Original review did not authorize content revision.')
    row=dict(source=str(path),run_type=kind,manual_corrections_included=False,original_hash=candidate_hash(before),
             before=first,revision_status=revision_stage['status'],revision_recovered=False,
             reviewer_comparison_available=after is not None and after_r is not None,
             revision_usage=[u for u in log.get('usage_records',[]) if u.get('phase')=='revision'])
    if after is None or after_r is None:
        row['outcome']='revision_failed' if after is None else 'final_review_unavailable'
        row['after_quality']='unknown';return row
    if check(after,h):raise ValueError('Saved revised candidate fails validation.')
    final=validate(after_r,after,1)
    if any(before_r[k]!=after_r[k] for k in ('rubric_version','hash_version')):
        raise ValueError('Rubric/hash version changed within comparison.')
    old=blocking_findings(before_r,first);new=blocking_findings(after_r,final)
    pack=lambda values:[dict(position=p,code=c) for p,c in sorted(values)]
    row.update(after=final,revised_hash=candidate_hash(after),outcome=final['decision'],revision_recovered=final['decision']=='pass',
        no_longer_reported=pack(old-new),still_reported=pack(old&new),newly_reported=pack(new-old),
        understanding_before=first['understanding_count'],understanding_after=final['understanding_count'],
        balance_flags_before=option_balance_issues(before),balance_flags_after=option_balance_issues(after))
    oldq={q['position']:q for q in before['quiz']['questions']};oldk={k['position']:k for k in before['answer_key']}
    newk={k['position']:k for k in after['answer_key']}
    row['changed_positions']=[q['position'] for q in after['quiz']['questions'] if q!=oldq[q['position']] or newk[q['position']]!=oldk[q['position']]]
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports',nargs='+',type=Path,help='Private saved agent-run JSON reports; no manual-review reports')
    args=parser.parse_args();rows=[];seen=set()
    for path in args.reports:
        path=path.resolve()
        if path in seen:continue
        seen.add(path);rows.append(score(path))
    included=[r for r in rows if 'excluded' not in r];complete=[r for r in included if r['reviewer_comparison_available']]
    success=sum(r['revision_recovered'] for r in included)
    result=dict(created_at=datetime.now(timezone.utc).isoformat(),paid_calls=0,manual_corrections_excluded=True,
        revision_recovery=dict(recovered=success,attempted=len(included),rate=success/len(included) if included else None),
        complete_comparisons=len(complete),incomplete_attempts=len(included)-len(complete),comparisons=rows,
        limitations=['Small historical convenience sample with different prompt versions; not a controlled ablation or population accuracy.',
            'Each pair reuses its Tutor explanation and validates saved hashes, bindings and rubric versions; no Reviewer calls made.',
            'Newly/no-longer reported findings reflect model reviews, not independent proof a defect was introduced/fixed.',
            'Incomplete revisions/reviews are unrecovered attempts, not inferred content verdicts.',
            'Elapsed/token comparisons are unavailable when original evidence did not capture them; missing usage is not zero.',
            'All saved manual corrections and their passing reviews are excluded from agent contribution.'])
    folder=ROOT/'backend/evals/results';folder.mkdir(exist_ok=True)
    path=folder/(result['created_at'].replace(':','-')+'-assessment-contribution.json')
    with path.open('x') as f:os.chmod(path,0o600);json.dump(result,f,indent=2)
    print(f'Assessment revision recovery: {success}/{len(included)} observed attempts. Complete review pairs: {len(complete)}; incomplete: {len(included)-len(complete)}.')
    for i,r in enumerate(included,1):
        print(f"{i}. {r['run_type']}: needs_revision → {r['outcome']}")
        if r['reviewer_comparison_available']:
            print(f"   Understanding: {r['understanding_before']} → {r['understanding_after']}; findings no longer/still/newly reported: {len(r['no_longer_reported'])}/{len(r['still_reported'])}/{len(r['newly_reported'])}")
    print('Historical observations only. Manual corrections excluded. No paid calls.\nSaved: '+str(path))


if __name__=='__main__':main()
