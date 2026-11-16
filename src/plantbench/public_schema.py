"""Task-specific output vocabulary, derived exclusively from public TaskContract."""
import copy
from pydantic import TypeAdapter
from .contracts import AgentAction

def action_schema(task):
    schema=TypeAdapter(AgentAction).json_schema()
    if task.protocol_version!='plantenv-2.0.0':return schema
    definitions=schema['$defs']; prototype=definitions['Answer']; branches=[]
    names={'category':'Category','category_set':'CategorySet','number':'Number','comparison':'Comparison','boolean':'Boolean'}
    # The Python value classes have convenience defaults, but the union parser
    # needs an explicit discriminator. JSON Schema must not suggest omitting it.
    for name in names.values():
        definitions[name]['required']=list(dict.fromkeys(['kind',*definitions[name].get('required',[])]))
    for slot in task.assessment_slots:
        if slot.output_location!='answers':continue
        for determination in ['supported','undetermined']:
            branch=copy.deepcopy(prototype); props=branch['properties']
            props['slot_id']={'type':'string','const':slot.slot_id}
            props['determination']={'type':'string','const':determination}
            props['value']={'$ref':'#/$defs/'+names[slot.value_kind]} if determination=='supported' else {'type':'null'}
            if determination=='undetermined':branch['required']=list(dict.fromkeys(branch['required']+['determination']))
            if not any(r.require_relations for r in slot.citation_requirements):props.pop('evidence_links')
            branches.append(branch)
    definitions['Answer']={'oneOf':branches} if branches else {'not':{}}
    if not task.require_decision:
        # Null is an optional no-op, accepted by the same parser.
        definitions['FinalReport']['properties']['decision']={'type':'null'}
    from .care_protocol import constrain_schema
    constrain_schema(task,definitions)
    # Remove unused definitions, including irrelevant scientific value types.
    used=set()
    def visit(node):
        if isinstance(node,dict):
            ref=node.get('$ref')
            if ref and ref.startswith('#/$defs/'):
                name=ref.rsplit('/',1)[-1]
                if name not in used:
                    used.add(name);visit(definitions[name])
            for key,item in node.items():
                if key!='$defs':visit(item)
        elif isinstance(node,list):
            for item in node:visit(item)
    visit(schema);schema['$defs']={k:v for k,v in definitions.items() if k in used}
    return schema
