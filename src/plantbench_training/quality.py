"""Unchanged teacher quality filter extracted from the completed SFT pipeline."""
QUALITY_VERSION = 'kimi-teacher-quality-1.0.0'
TOOLS = {'INSPECT', 'QUERY', 'ASK', 'SEARCH_SOURCE'}


def report_citations(action):
    """All evidence IDs an ASSESS/SUBMIT/STOP report cites."""
    report = action.get('assessment') if action.get('action') == 'ASSESS' else action.get('report')
    if not isinstance(report, dict):
        return set()
    cited = set()
    for answer in report.get('answers') or []:
        cited |= set(answer.get('evidence_ids') or [])
        cited |= {l.get('evidence_id') for l in answer.get('evidence_links') or []}
    cited |= set((report.get('decision') or {}).get('evidence_ids') or [])
    for lim in report.get('limitations') or []:
        if isinstance(lim, dict):
            cited |= set(lim.get('evidence_ids') or [])
    return cited


def quality(traj, score):
    """Verdict + reasons. strict: usable for reasoning+action SFT as-is.
    masked: success, but some turns are invalid and must be loss-masked."""
    reasons, turn_flags = [], []
    if traj['status'] != 'completed':
        return {'verdict': 'rejected', 'reasons': ['incomplete:' + traj['status']], 'turns': []}
    final = traj['final'] or {}
    if final.get('termination_reason') != 'submitted':
        reasons.append('not_submitted:' + str(final.get('termination_reason')))
    if score is None:
        reasons.append('unscored')
    else:
        if score['task_success'] != 1:
            reasons.append('task_failed')
        required = [m for m in score['milestone_results'] if m['required']]
        if not all(m['passed'] for m in required):
            reasons.append('required_milestone_missed')
        mandatory = [r for r in score['slot_results'] if r['mandatory']]
        if any(r['answer_present'] and not r['citation_validity'] for r in mandatory):
            reasons.append('citation_not_visible_or_nonexistent')
        eg = score['metrics']['evidence_grounding']['value']
        if eg is not None and eg < 1:
            reasons.append('evidence_grounding_incomplete')
    any_invalid = False
    for step in traj['steps']:
        flags = []
        m, a, e = step['model'], step['action'], step.get('environment') or {}
        if not m['reasoning_present']:
            flags.append('missing_reasoning')
        if m['finish_reason'] == 'length':
            flags.append('truncated_generation')
        kind = a['adapter_conversion']['kind']
        if kind != 'spliced':
            flags.append('non_single_tool_call:' + kind)
        if a.get('parse_status') != 'parsed' or a.get('public_error') is not None:
            flags.append('invalid_action:' + str(a.get('public_error')))
            any_invalid = True
        parsed = a.get('parsed_action') or {}
        visible = set(e.get('visible_evidence_ids_before') or [])
        unseen = sorted(report_citations(parsed) - visible) if parsed else []
        if unseen:
            flags.append('cites_unreceived_evidence')
        turn_flags.append({'index': step['index'], 'flags': flags, 'unreceived_citations': unseen,
                           'trainable': not flags})
    all_flags = {f.split(':')[0] for t in turn_flags for f in t['flags']}
    # Structural defects disqualify the whole trajectory: the SFT context
    # would contain fabricated, truncated or reasoning-less teacher turns.
    for hard in ['missing_reasoning', 'truncated_generation', 'non_single_tool_call', 'cites_unreceived_evidence']:
        if hard in all_flags:
            reasons.append(hard)
    if reasons:
        verdict = 'rejected'
    elif any_invalid:
        verdict = 'accepted_masked'   # recovered from invalid actions; those turns get loss=0
    else:
        verdict = 'accepted_strict'
    return {'verdict': verdict, 'reasons': reasons, 'turns': turn_flags, 'quality_version': QUALITY_VERSION}
