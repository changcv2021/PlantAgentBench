"""Opt-in generation-time syntax constraints, independent of private answers.

This is a new runtime condition, never a repair of archived model text. It
keeps every public action available and leaves semantic validation to PlantEnv.
The production full-catalog runner does not enable it implicitly.
"""
import copy

from pydantic import TypeAdapter

from .contracts import AgentAction


POLICY = 'public_action_json_schema_generation_v1'


def response_format(action=None, *, task=None):
    if task is not None:
        from .public_schema import action_schema
        schema=action_schema(task)
        if action is not None:
            if action not in {'ASSESS','SUBMIT'}:raise ValueError('Unsupported fixed baseline endpoint')
            schema={'$defs':schema['$defs'],'$ref':'#/$defs/'+('AssessAction' if action=='ASSESS' else 'SubmitAction')}
        return {'type':'json_schema','json_schema':{'name':'public_task_action_v2','strict':True,'schema':schema}}
    schema = TypeAdapter(AgentAction).json_schema()
    for name in ['Category','CategorySet','Number','Comparison','Boolean']:
        definition=schema['$defs'][name]
        definition['required']=list(dict.fromkeys(['kind',*definition.get('required',[])]))
    # Pydantic's model validator is not represented in its JSON Schema.
    # Encode the *public* determination/value relationship for the decoder.
    answer = schema['$defs']['Answer']
    branches = []
    for determination in ('supported', 'undetermined'):
        branch = copy.deepcopy(answer)
        branch['properties']['determination'] = {'const': determination, 'type': 'string'}
        if determination=='undetermined':branch['required']=list(dict.fromkeys(branch['required']+['determination']))
        branch['properties']['value'] = (
            {'type': 'null'} if determination == 'undetermined' else
            next(item for item in answer['properties']['value']['anyOf'] if item.get('type') != 'null')
        )
        branches.append(branch)
    schema['$defs']['Answer'] = {'oneOf': branches}
    if action is not None:
        # Only fixed non-agent baselines use these public stage endpoints.
        # Full-Agent always retains the complete action vocabulary.
        if action not in {'ASSESS', 'SUBMIT'}:
            raise ValueError('Unsupported fixed baseline endpoint')
        definition = 'AssessAction' if action == 'ASSESS' else 'SubmitAction'
        schema = {'$defs': schema['$defs'], '$ref': '#/$defs/' + definition}
    return {'type': 'json_schema', 'json_schema': {
        'name': POLICY + (('_baseline_' + action.lower()) if action else ''), 'strict': True, 'schema': schema,
    }}


def validate_response_format(value, *, task=None):
    if value not in [response_format(task=task), response_format('ASSESS',task=task), response_format('SUBMIT',task=task)]:
        raise ValueError('Unregistered structured action format')
