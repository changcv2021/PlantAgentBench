"""Pre-freeze corrections, independent of model answers or performance.

Raw construction packs remain intact. This transformation is applied once to
every catalog entry and is recorded in the final adaptation log.
"""
import copy
from plantbench.contracts import EpisodeSpec, logical_hash
from .references import extract_advice, clean_text
from .taskpack import digest

VERSION = 'guide06-normalization-1.1.0'
SCOPE_SLOTS = {'G03': 'specific_cause', 'E02': 'plan_execution_verified',
               'E03': 'causal_diagnosis', 'D03': 'diagnosis_determination'}


def normalize_task(episode, gold, claims):
    """Return a corrected task, gold, claims, and traceable change list.

    A None episode means the source plan no longer has a safely mapped positive
    item; it remains a material candidate with a reason, never fabricated gold.
    """
    episode = copy.deepcopy(episode.model_dump(mode='json') if isinstance(episode, EpisodeSpec) else episode)
    gold, claims = copy.deepcopy(gold), copy.deepcopy(claims)
    changes = []
    template = episode['agentic']['template_id']
    scope_slot = SCOPE_SLOTS.get(template)
    for rule in gold['slots']:
        if rule['slot_id'] != scope_slot:
            continue
        rule.update(citation_policy='not_required', citation_required=False, support_routes=[])
        rule['scope_basis'] = 'Explicit task statement lacks a diagnostic/causal reference or a plan execution log'
        changes.append('public_scope_limitation_does_not_require_measurement_citation')
    if scope_slot:
        for slot in episode['task']['assessment_slots']:
            if slot['slot_id'] == scope_slot:
                slot['question'] += ' This scope limitation is stated in the task; no measurement citation is required for this slot.'
    if template in {'C01', 'C03'}:
        if len(claims) != 1:
            raise ValueError('Expected one traceable source plan per task')
        old = claims[0]
        text = old.get('source_text', old.get('cleaned_source_text'))
        if not text:
            raise ValueError('Missing original source plan text')
        advice = extract_advice(text)
        claims[0].update(advice, cleaned_source_text=clean_text(text), cleaned_text_sha256=digest(clean_text(text)),
                         extraction_version=VERSION)
        groups = advice['required_action_groups']
        changes.append('source_advice_reextracted_with_negation_and_alternative_scope')
        if not groups:
            return None, gold, claims, changes + ['retained_reference_material:no_safely_mapped_positive_action']
        expected = sorted({code for group in groups for code in group})
        for rule in gold['slots']:
            if rule['slot_id'] not in {'actions', 'decision_actions'}:
                continue
            rule.update(expected=expected, explicit_contradictions=advice['explicit_contradictions'])
            if template == 'C01':
                rule.update(match='groups', groups=groups)
            else:
                rule.update(match='nonempty_subset')
            if rule.get('location') == 'decision.actions':
                rule['conditions_by_action'] = advice['conditions_by_action']
        gold['reference_scope'] = advice['reference_scope']
    # Images without a validated pixel predicate cannot make a deterministic
    # record comparison an investigative visual task by merely adding tools.
    if template in {'G01', 'G03', 'E01', 'E02'}:
        record_choices = sum(t['action_type'] == 'QUERY' for t in episode['catalog'])
        initial = set(episode['phases'][0]['initial_evidence_refs'])
        supported = [r for r in gold['slots'] if r.get('mandatory', True)
                     and r.get('citation_policy') == 'verified_support']
        initial_sufficient = bool(supported) and all(any(set(route) <= initial for route in r['support_routes']) for r in supported)
        if initial_sufficient and record_choices:
            kind = 'stop_control'
        elif not record_choices or (template == 'G03' and record_choices == 1):
            kind = 'component'
        else:
            kind = 'investigative'
        if kind != episode['agentic']['task_kind']:
            changes.append('task_kind_uses_relevant_record_choices_not_decorative_images')
        episode['agentic']['task_kind'] = kind
        episode['admission']['role'] = 'component_only' if kind == 'component' else 'record_agent'
        gold['stopping_class'] = 'initial_sufficient' if initial_sufficient else 'resolvable_with_queries'
    episode['builder_version'] = '0.3.1'
    episode['agentic']['human_review_status'] = gold['human_review_status'] = 'pending'
    gold['human_review_blocks_execution'] = False
    gold['normalization_version'] = VERSION
    episode['content_hash'] = ''
    episode = EpisodeSpec.model_validate(episode)
    episode.content_hash = logical_hash(episode)
    if gold.get('schema_version')=='agent-gold-2.0.0':
        from .evaluation_contract import compile_evaluation
        episode,gold=compile_evaluation(episode,gold)
    return episode, gold, claims, sorted(set(changes))
