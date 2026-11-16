"""Native function definitions derived only from the public per-task action schema.

One function per PlantEnv action name. Each function's parameters are exactly
that action's branch of `plantbench.public_schema.action_schema(task)` minus the
`action` discriminator, with `$ref`s inlined. Nothing here reads the episode,
gold, or private selection data, and no tool is hidden or reordered per task:
every task sees the same eight functions, and PlantEnv decides which targets
are actually available, exactly as in the raw-JSON protocol.
"""
import copy
import json

ACTIONS = ['INSPECT', 'QUERY', 'ASK', 'SEARCH_SOURCE', 'ASSESS', 'ADVANCE_RECORD', 'SUBMIT', 'STOP']
BRANCH = {'INSPECT': 'GetAction', 'QUERY': 'GetAction', 'ASK': 'GetAction', 'SEARCH_SOURCE': 'SearchAction',
          'ASSESS': 'AssessAction', 'ADVANCE_RECORD': 'AdvanceAction', 'SUBMIT': 'SubmitAction', 'STOP': 'StopAction'}
# Neutral wording restating the public protocol; no strategy or ordering hints.
DESCRIPTIONS = {
    'INSPECT': 'PlantEnv INSPECT action on a listed target whose action_type is INSPECT.',
    'QUERY': 'PlantEnv QUERY action on a listed target whose action_type is QUERY.',
    'ASK': 'PlantEnv ASK action on a listed target whose action_type is ASK.',
    'SEARCH_SOURCE': 'PlantEnv SEARCH_SOURCE action: search the frozen general knowledge library when the task offers it.',
    'ASSESS': 'PlantEnv ASSESS action: record a checkpoint assessment for the current phase.',
    'ADVANCE_RECORD': 'PlantEnv ADVANCE_RECORD action: move to the next record phase of a phased task.',
    'SUBMIT': 'PlantEnv SUBMIT action: submit the final report and end the episode.',
    'STOP': 'PlantEnv STOP action: end the episode, optionally with a report.',
}

INTERFACE_NOTE = '''
Function-calling interface (this deployment): every PlantEnv action is a function call.
The function name is the "action" value; the function arguments are the remaining
fields of that same JSON action object, for example {"action":"QUERY","target_id":"t_x"}
is the call QUERY with arguments {"target_id":"t_x"}, and SUBMIT takes {"report":{...}}.
Call exactly one function per response. Its result is the next public environment
observation. Arguments follow the same public action schema; nothing is repaired.'''

# v2: placed BEFORE the harness prompt. In the v1 pilot 3/10 episodes followed the
# harness's "Return exactly one JSON object" wording and wrote actions as message
# text for the whole episode; this states which instruction governs the channel.
INTERFACE_PREAMBLE = '''Interface for this deployment (it takes precedence over any later instruction to
return the action as a JSON object or text): emit every PlantEnv action as a native
function call, never as JSON in the message text. The function name is the "action"
value and the function arguments are the remaining fields of that JSON action object;
the JSON syntax and schema below describe those arguments.

'''
INTERFACE_REMINDER = '\nReminder: emit each action only as one function call, never as message text.'



# Moonshot Flavored JSON Schema (MFJS) keyword set. `oneOf`/`const` are not
# officially supported and `title` is rejected, so the public schema is
# translated without changing which JSON objects it describes for these
# tasks: oneOf->anyOf (branches are disjoint by slot_id/determination) and
# const->single-value enum. String length/pattern and integer bounds are not
# MFJS keywords; they are dropped from the function signature only, remain in
# the full public_action_schema message in context, and are still enforced
# by PlantEnv's frozen parser.
SCHEMA_KEYWORDS = {'type', 'properties', 'additionalProperties', 'items', 'required', 'description', 'default', 'enum', 'anyOf'}
DROPPED_KEYWORDS = {'title', 'pattern', 'minLength', 'maxLength', 'minimum', 'maximum', 'discriminator', '$defs'}


def _inline(node, defs, stack=()):
    if isinstance(node, list):
        return [_inline(v, defs, stack) for v in node]
    if not isinstance(node, dict):
        return node
    ref = node.get('$ref')
    if ref is not None:
        name = ref.rsplit('/', 1)[-1]
        if name in stack:
            raise ValueError('RECURSIVE_PUBLIC_SCHEMA_UNSUPPORTED:' + name)
        return {**_inline(defs[name], defs, stack + (name,)), **_inline({k: v for k, v in node.items() if k != '$ref'}, defs, stack)}
    if node == {'not': {}}:
        # Public schema for "no ordinary answer slots": any list item is invalid.
        return {'type': 'null', 'description': 'This task has no ordinary answer slots; keep this list empty.'}
    out = {}
    for key, value in node.items():
        if key == 'properties':
            out[key] = {prop: _inline(sub, defs, stack) for prop, sub in value.items()}
        elif key == 'oneOf':
            out['anyOf'] = _inline(value, defs, stack)
        elif key == 'const':
            out['enum'] = [value]
        elif key in SCHEMA_KEYWORDS:
            out[key] = _inline(value, defs, stack)
        elif key not in DROPPED_KEYWORDS:
            raise ValueError('UNMAPPED_PUBLIC_SCHEMA_KEYWORD:' + key)
    if 'enum' in out and 'type' in out and out.get('type') == 'null':
        out.pop('enum')
    return out


def function_definitions(public_action_schema):
    defs = public_action_schema['$defs']
    tools = []
    for name in ACTIONS:
        branch = _inline(copy.deepcopy(defs[BRANCH[name]]), defs)
        props = dict(branch.get('properties', {}))
        props.pop('action', None)
        params = {'type': 'object', 'properties': props,
                  'required': [r for r in branch.get('required', []) if r != 'action'],
                  'additionalProperties': False}
        tools.append({'type': 'function', 'function': {'name': name, 'description': DESCRIPTIONS[name], 'parameters': params}})
    return tools


def action_from_message(message):
    """Map one native assistant message to the raw bytes PlantEnv will parse.

    Returns (raw_action_text, conversion_record). The mapping is textual and
    lossless: the model's own argument text is spliced after the action name,
    so malformed, conflicting or extra fields reach PlantEnv's frozen parser
    unchanged and fail there (e.g. DUPLICATE_JSON_KEY) instead of being fixed.
    """
    calls = message.get('tool_calls') or []
    content = message.get('content')
    if not calls:
        # No function call: the content itself is the action attempt, as in the
        # registered raw-JSON protocol. Empty/None becomes an empty action.
        text = content if isinstance(content, str) else ''
        return text, {'kind': 'content_only' if text.strip() else 'no_action', 'tool_call_count': 0}
    pieces, kinds = [], []
    for call in calls:
        fn = call.get('function') or {}
        name, args = fn.get('name'), fn.get('arguments')
        if not isinstance(name, str) or not isinstance(args, str):
            pieces.append(json.dumps({'action': name, 'unparsed_arguments': args}))
            kinds.append('malformed_call_envelope')
            continue
        try:
            parsed = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            pieces.append(args)  # Not a JSON object: PlantEnv rejects it verbatim.
            kinds.append('arguments_not_json_object')
            continue
        body = args.strip()
        inner = body[1:-1].strip() if body else ''
        pieces.append('{"action":' + json.dumps(name) + (',' + inner if inner else '') + '}')
        kinds.append('action_conflict_left_to_parser' if 'action' in parsed else 'spliced')
    if len(calls) > 1:
        # PlantEnv executes one action per step; a list is an invalid action.
        return '[' + ','.join(pieces) + ']', {'kind': 'multiple_tool_calls', 'tool_call_count': len(calls), 'per_call': kinds}
    return pieces[0], {'kind': kinds[0], 'tool_call_count': 1}
