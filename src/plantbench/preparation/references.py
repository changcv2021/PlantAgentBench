"""Conservative, traceable advice-category extraction from existing source text.

This is ontology-scoped source agreement, not certification of optimal care.
Every extracted statement retains its complete source sentence and modifiers.
"""
import html
import copy
import functools
import re

ACTION_PATTERNS = {
    'monitor_with_followup': r'\b(?:monitor|watch|keep an eye|observe|check (?:again|regularly|weekly)|wait and see)\b',
    'seek_domain_expert': r'\b(?:contact|consult|ask|call|visit)\b.{0,45}\b(?:extension|arborist|expert|professional|nursery|diagnostic|horticultur|specialist)',
    'request_soil_test': r'\b(?:soil test|test (?:the |your )?soil|soil (?:sample|analysis))\b',
    'request_diagnostic_test': r'\b(?:lab(?:oratory)? test|diagnostic (?:test|lab)|send.{0,25}(?:sample|specimen))\b',
    'request_photos': r'\b(?:send|provide|attach|upload|take|need|see)\b.{0,45}\b(?:photo|picture|image)',
    'adjust_water_increase': r'\b(?:water (?:more|deeply|regularly|thoroughly|well)|increase (?:the )?water|keep.{0,20}(?:moist|watered))\b',
    'adjust_water_decrease': r'\b(?:water less|less water|reduce.{0,15}water|cut back.{0,15}water|allow.{0,20}(?:dry|drying))\b',
    'improve_drainage': r'\b(?:improv\w*.{0,15}drainage|well.drained|drainage holes)\b',
    'prune_remove_affected': r'\b(?:prun\w*|cut (?:back|off|out)|remov\w*.{0,35}(?:dead|diseased|infected|damaged|branch|limb|stem|shoot|leaf|leaves))\b',
    'remove_plant_or_weed': r'\b(?:hand.pull\w*|pull(?:ing)? (?:the |these |your )?(?:weed|plant)|dig (?:up|out)|remove (?:the |these |your )?(?:(?:infected|diseased|affected|dead|damaged) )?(?:weed|plant)s?)\b',
    'sanitation_review': r'\b(?:saniti[sz]\w*|disinfect\w*|clean up.{0,30}(?:debris|leaves)|dispose of.{0,30}(?:debris|leaves|plant))\b',
    'isolation_review': r'\b(?:isolat\w*|quarantin\w*|separate.{0,20}(?:plant|pot))\b',
    'adjust_nutrients_review': r'\b(?:fertili[sz]\w*|apply.{0,20}(?:nitrogen|compost|nutrient)|add.{0,20}(?:compost|fertilizer))\b',
    'adjust_light_review': r'\b(?:more (?:sun|light)|less (?:sun|light)|partial shade|full sun|bright.{0,15}light|move.{0,30}(?:sun|shade|light))\b',
    'adjust_environment_review': r'\b(?:air circulation|improve.{0,15}ventilation|increase.{0,15}humidity|reduce.{0,15}humidity)\b',
    'mulch_or_soil_cover': r'\b(?:mulch\w*|ground cover|cover.{0,20}soil)\b',
    'repot_or_transplant': r'\b(?:repot\w*|transplant\w*|larger (?:pot|container))\b',
    'pesticide_label_review': r'\b(?:insecticid\w*|fungicid\w*|herbicid\w*|horticultural oil|neem oil|insecticidal soap)\b',
    'physical_pest_protection': r'\b(?:row cover|netting|fence|barrier|trap\w*|hand.pick\w*)\b',
    'continue_recorded_plan': r'\b(?:no (?:treatment|action).{0,15}(?:needed|required|necessary)|nothing.{0,15}(?:do|needed)|leave.{0,20}alone)\b',
}
ACTION_DESCRIPTIONS={
    'monitor_with_followup':'monitor and reassess', 'seek_domain_expert':'consult an appropriate expert',
    'request_soil_test':'obtain a soil test', 'request_diagnostic_test':'obtain a diagnostic test',
    'request_photos':'obtain clarifying photographs', 'adjust_water_increase':'increase or ensure watering',
    'adjust_water_decrease':'reduce watering or let substrate dry', 'improve_drainage':'improve drainage',
    'prune_remove_affected':'prune or remove affected plant parts', 'remove_plant_or_weed':'remove a plant or weed',
    'sanitation_review':'sanitation and disposal', 'isolation_review':'isolate or separate plants',
    'adjust_nutrients_review':'review nutrient addition', 'adjust_light_review':'adjust light exposure',
    'adjust_environment_review':'adjust humidity or ventilation', 'mulch_or_soil_cover':'mulching or soil cover',
    'repot_or_transplant':'repot or transplant', 'pesticide_label_review':'consider a pesticide subject to its label',
    'physical_pest_protection':'physical barriers or pest removal', 'continue_recorded_plan':'continue observation without new intervention'}
CONDITION_PATTERNS={
    'after_confirmation':r'\b(?:if (?:confirmed|you (?:confirm|identify))|once.{0,20}(?:confirmed|identified))',
    'if_symptoms_persist':r'\b(?:if.{0,30}(?:persists?|continues?|worsens?|does not improve))',
    'before_flowering':r'\b(?:before.{0,25}(?:flower|bloom|seed))',
    'during_dormancy':r'\b(?:dormant|dormancy|late winter)',
    'after_flowering':r'\b(?:after.{0,25}(?:flower|bloom))',
    'avoid_heat_or_drought':r'\b(?:not.{0,25}(?:hot|heat|drought)|avoid.{0,25}(?:heat|drought))',
    'follow_product_label':r'\b(?:label directions|follow.{0,20}label|according to.{0,20}label|read.{0,15}label)',
    'only_if_affected':r'\b(?:if(?:(?!\bnot\b|\bno\b).){0,25}\b(?:infected|diseased|damaged|dead)|affected (?:branches|leaves|parts))',
    'as_needed':r'\b(?:as needed|if needed|if necessary|when necessary)',
}
NEGATION=re.compile(r"\b(?:do not|don't|avoid|never|not recommend(?:ed|ing)?|should not|shouldn't|no need to|not necessary|unnecessary)\b",re.I)
CONDITIONAL=re.compile(r'\b(?:if|unless|when|before|after|until|only|provided|depending)\b',re.I)
PRESCRIPTIVE=re.compile(r"\b(?:recommend|suggest|should|could|can|need to|must|try|consider|best to|important to|be sure|make sure|I would|you may|you might|will want)\b",re.I)
IMPERATIVE=re.compile(r'^(?:(?:also|then|first|please|instead|next)[,:]?\s+)*(?:ensure|maintain|protect|install|water|keep|allow|reduce|increase|improve|prune|cut|remove|pull|dig|sanitize|disinfect|clean|dispose|isolate|quarantine|apply|add|use|move|mulch|repot|transplant|monitor|watch|observe|check|wait|contact|consult|ask|call|visit|test|send|provide|attach|upload|take|avoid|do not|follow|read|leave)\b',re.I)


def clean_text(text):
    text=html.unescape(re.sub(r'<[^>]+>',' ',str(text)))
    text=re.sub(r'https?://\S+','[source link]',text)
    text=re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b','[contact redacted]',text)
    return re.sub(r'\s+',' ',text).strip()


def extract_advice(text):
    return copy.deepcopy(_extract_advice(text))


@functools.lru_cache(maxsize=1024)
def _extract_advice(text):
    """Map source spans, retaining unmodeled conditions rather than erasing them."""
    clean=clean_text(text); claims=[]; groups=[]; prohibited=set(); unscored=[]
    for match in re.finditer(r'[^.!?;]+(?:[.!?;]|$)',clean):
        sentence=match.group(0).strip()
        if not sentence:continue
        hits=[];positions={}
        for code,pattern in ACTION_PATTERNS.items():
            hit=re.search(pattern,sentence,re.I)
            if not hit:continue
            if code=='prune_remove_affected':
                whole=re.search(ACTION_PATTERNS['remove_plant_or_weed'],sentence,re.I)
                if whole and whole.start()<=hit.start()<whole.end() and not re.search(r'\b(?:branch|limb|stem|shoot|leaf|leaves)\b',hit.group()):
                    continue
            # A source question or retrospective account is not automatically advice.
            if sentence.endswith('?'):
                unscored.append({'text':sentence,'reason':'question_not_prescriptive'});continue
            conditional_imperative=(bool(re.match(r'^(?:if|when|unless|before|after|once)\b',sentence,re.I))
                and ',' in sentence and bool(IMPERATIVE.search(sentence.split(',',1)[1].strip())))
            if not (PRESCRIPTIVE.search(sentence) or IMPERATIVE.search(sentence) or conditional_imperative or NEGATION.search(sentence) or code=='continue_recorded_plan'):
                unscored.append({'text':sentence,'reason':'prescription_not_established'});continue
            prefix=sentence[:hit.end()]
            boundaries=list(re.finditer(r'\b(?:but|however|instead|whereas)\b',prefix,re.I))
            if boundaries:prefix=prefix[boundaries[-1].end():]
            prefix=prefix[-100:]
            after=sentence[hit.end():hit.end()+60]
            negative=bool(NEGATION.search(prefix) or re.match(r'\s*(?:(?:is|are|would be|will be|may be)\s+)?(?:not recommended|not necessary|unnecessary)\b',after,re.I))
            condition_matches={key:list(re.finditer(pattern,sentence,re.I)) for key,pattern in CONDITION_PATTERNS.items()}
            conditions=[key for key,matches in condition_matches.items() if matches]
            covered=[]
            for matches in condition_matches.values():
                for condition_match in matches:
                    start,end=condition_match.span()
                    prefix_only=re.search(r'\bonly\s*$',sentence[:start],re.I)
                    covered.append((prefix_only.start() if prefix_only else start,end))
            # One recognized condition does not license discarding another
            # condition in the same sentence (e.g. temperature AND dormancy).
            unknown_condition=any(not any(start<=cue.start()<end for start,end in covered)
                                  for cue in CONDITIONAL.finditer(sentence))
            claim={'code':code,'polarity':'negative' if negative else 'positive',
                'condition_codes':conditions,'condition_text':sentence if CONDITIONAL.search(sentence) else None,
                'certainty':'source_recommendation_not_guaranteed_outcome',
                'source_span':{'start':match.start(),'end':match.end(),'text':match.group(0)},
                'contains_quantity':bool(re.search(r'\d',sentence)),
                'condition_semantics_scored':not unknown_condition}
            claims.append(claim)
            if unknown_condition:
                unscored.append({'text':sentence,'reason':'condition_retained_but_not_in_closed_ontology'})
                continue
            if negative:prohibited.add(code)
            else:hits.append(code);positions[code]=(hit.start(),hit.end())
        if hits:
            ordered=sorted(set(hits),key=lambda code:positions[code])
            between=sentence[positions[ordered[0]][1]:positions[ordered[-1]][0]]
            if len(ordered)>1 and re.search(r'\bor\b',between,re.I) and not re.search(r'\band\b',between,re.I):
                groups.append(sorted(ordered))
            else:
                groups.extend([[code] for code in ordered])
    # Contradictory or context-dependent recommendations are preserved as source
    # claims, not converted to an unconditional automatic success requirement.
    conflict={code for group in groups for code in group}&prohibited
    groups=[group for group in groups if not set(group)&conflict]
    unique={tuple(group) for group in groups}
    applicable={code for group in unique for code in group}
    conditions={code:sorted({c for claim in claims if claim['code']==code and claim['polarity']=='positive'
                             and claim['condition_semantics_scored'] for c in claim['condition_codes']}) for code in applicable}
    return {'claims':claims,'required_action_groups':[list(g) for g in sorted(unique)],
        'explicit_contradictions':sorted(prohibited-conflict),'conditions_by_action':conditions,
        'unscored_spans':unscored,'conflicting_action_codes':sorted(conflict),
        'reference_scope':'Only mapped source action categories and mapped conditions; quantities and unmodeled source semantics are not certified by this checker'}
