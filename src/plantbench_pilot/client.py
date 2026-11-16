"""Loopback-only HTTP, fixed sampling and durable first-response semantics."""
import base64
import datetime as dt
import fcntl
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .common import digest,encoded,safe_id,save_json


PROFILE={'temperature':0.7,'top_p':0.8,'top_k':20,'min_p':0.0,
    'presence_penalty':1.5,'frequency_penalty':0.0,'repetition_penalty':1.0,
    'max_tokens':4096,'n':1,'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
ALIAS = 'plantagent-model'  # override with the explicitly registered served model


class TransportFailure(RuntimeError):
    pass


class ResponseFailure(RuntimeError):
    pass


def endpoint(api_base):
    parsed=urllib.parse.urlsplit(api_base)
    if (parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','::1'} or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or parsed.path.rstrip('/')!='/v1'):
        raise ValueError('ONLY_EXPLICIT_LOOPBACK_V1_ENDPOINT_ALLOWED')
    if parsed.port is None or not 1<=parsed.port<=65535:
        raise ValueError('EXPLICIT_LOCAL_SERVICE_PORT_REQUIRED')
    return api_base.rstrip('/')+'/chat/completions'


def request_body(messages,seed,*,profile=None,model_alias=None):
    profile=PROFILE if profile is None else profile
    model_alias=ALIAS if model_alias is None else model_alias
    if type(seed)!=int or not 0<=seed<2**31:
        raise ValueError('INVALID_SAMPLING_SEED')
    body={'model':model_alias,'messages':messages,'seed':seed,**profile}
    validate_body(body,profile=profile,model_alias=model_alias)
    return json.loads(encoded(body))


def validate_body(body,*,profile=None,model_alias=None):
    profile=PROFILE if profile is None else profile
    model_alias=ALIAS if model_alias is None else model_alias
    if set(body)!={'model','messages','seed',*profile} or body['model']!=model_alias:
        raise ValueError('UNDECLARED_REQUEST_FIELDS_OR_MODEL')
    if any(body[key]!=value for key,value in profile.items()) or type(body['seed'])!=int or not 0<=body['seed']<2**31:
        raise ValueError('FIXED_SAMPLING_PROFILE_CHANGED')
    messages=body['messages']
    if not isinstance(messages,list) or not messages:
        raise ValueError('PUBLIC_HISTORY_REQUIRED')
    for message in messages:
        if set(message)!={'role','content'} or message['role'] not in {'system','user','assistant'}:
            raise ValueError('NATIVE_TOOL_OR_UNDECLARED_MESSAGE_FIELDS')
        content=message['content']
        if isinstance(content,str):
            continue
        if not isinstance(content,list):
            raise ValueError('INVALID_MESSAGE_CONTENT')
        for block in content:
            if block.get('type')=='text' and set(block)=={'type','text'} and isinstance(block['text'],str):
                continue
            if block.get('type')=='image_url' and set(block)=={'type','image_url'} and set(block['image_url'])=={'url'}:
                url=block['image_url']['url']
                if isinstance(url,str) and url.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,')):
                    raw=base64.b64decode(url.split(',',1)[1],validate=True)
                    if raw:
                        continue
            raise ValueError('UNVERIFIED_IMAGE_PATH_OR_CONTENT_BLOCK')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def http_once(url,body,*,timeout=300,max_response_bytes=8*1024*1024):
    # Explicitly ignore shell proxy configuration and refuse HTTP redirects.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    request=urllib.request.Request(url,data=encoded(body),headers={'Content-Type':'application/json'},method='POST')
    try:
        try:
            response=opener.open(request,timeout=timeout)
        except urllib.error.HTTPError as exc:
            response=exc
        with response:
            raw=response.read(max_response_bytes+1)
            return {'http_status':response.code,'response_base64':base64.b64encode(raw).decode('ascii'),
                'server_request_id':response.headers.get('x-request-id'),
                'response_size_limit_exceeded':len(raw)>max_response_bytes}
    except (urllib.error.URLError,TimeoutError,ConnectionError,OSError) as exc:
        # Do not persist exception URLs, tokens, credentials or raw headers.
        raise TransportFailure(type(exc).__name__) from None


def decode_response(record,*,model_alias=None):
    model_alias=ALIAS if model_alias is None else model_alias
    if record.get('response_size_limit_exceeded'):
        raise ResponseFailure('HTTP_RESPONSE_SIZE_LIMIT_PREFIX_PRESERVED')
    raw=base64.b64decode(record['response_base64'],validate=True)
    try:
        from plantbench.protocol import unique_keys,reject_constant
        obj=json.loads(raw,object_pairs_hook=unique_keys,parse_constant=reject_constant)
        if not isinstance(obj,dict) or len(obj.get('choices',[]))!=1:
            raise ValueError('SINGLE_RESPONSE_CHOICE_REQUIRED')
        if obj.get('model')!=model_alias:
            raise ValueError('SERVED_MODEL_ALIAS_MISMATCH')
        choice=obj['choices'][0];message=choice['message']
        if message.get('role')!='assistant' or not isinstance(message.get('content'),str) or message.get('tool_calls'):
            raise ValueError('ASSISTANT_CONTENT_REQUIRED_NO_NATIVE_TOOLS')
        usage=obj.get('usage')
        tokens={name:None for name in ('prompt_tokens','completion_tokens')}
        if usage is not None:
            for name in tokens:
                value=usage.get(name)
                if value is not None and (type(value)!=int or value<0):
                    raise ValueError('INVALID_TOKEN_USAGE')
                tokens[name]=value
        return {'content_raw':message['content'],'reasoning_channel':message.get('reasoning',message.get('reasoning_content')),
            'finish_reason':choice.get('finish_reason'),**tokens,'usage_source':'server' if usage is not None else 'unavailable',
            'response_sha256':hashlib.sha256(raw).hexdigest(),'parse_status':'not_parsed_by_client',
            'reported_model':obj.get('model'),'server_request_id':record.get('server_request_id')}
    except (ValueError,TypeError,KeyError,AttributeError,IndexError,UnicodeError,RecursionError) as exc:
        raise ResponseFailure('INVALID_HTTP_RESPONSE_ENVELOPE:'+type(exc).__name__) from None


class RequestManager:
    def __init__(self,root,api_base,*,model_lock_hash,runtime_lock_hash,max_attempts=3,transport=http_once,sleeper=time.sleep):
        self.root=Path(root).resolve();self.url=endpoint(api_base)
        for value in (model_lock_hash,runtime_lock_hash):
            if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('COMPLETE_MODEL_AND_RUNTIME_HASHES_REQUIRED')
        if type(max_attempts)!=int or not 1<=max_attempts<=3:
            raise ValueError('BOUNDED_TRANSPORT_ATTEMPTS_REQUIRED')
        self.identity={'api_endpoint':self.url,'model_lock_hash':model_lock_hash,'runtime_lock_hash':runtime_lock_hash,
            'max_transport_attempts':max_attempts,'response_policy':'first complete HTTP200 envelope; never resample invalid model content'}
        self.max_attempts=max_attempts;self.transport=transport;self.sleeper=sleeper

    def complete(self,request_id,body):
        validate_body(body)
        folder=self.root/safe_id(request_id)
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        if folder.is_symlink() or not folder.resolve().is_relative_to(self.root):
            raise ValueError('REQUEST_DIRECTORY_ESCAPES_RUN_ROOT')
        with (folder/'writer.lock').open('a') as guard:
            fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            prepared={'logical_request_id':request_id,'request_hash':digest(body),'request_body':body,**self.identity}
            save_json(folder/'prepared.json',prepared)
            for attempt in range(1,self.max_attempts+1):
                path=folder/f'attempt_{attempt}.json'
                if path.exists():
                    saved=json.loads(path.read_text())
                    record_hash=saved.pop('record_hash')
                    if digest(saved)!=record_hash:
                        raise ValueError('PERSISTED_ATTEMPT_CONTENT_CHANGED')
                    if saved.get('request_hash')!=prepared['request_hash'] or saved.get('attempt')!=attempt:
                        raise ValueError('PERSISTED_ATTEMPT_IDENTITY_MISMATCH')
                else:
                    start=time.monotonic();stamp=dt.datetime.now(dt.timezone.utc).isoformat()
                    try:
                        response=self.transport(self.url,body)
                        saved={'transport_error':None,**response}
                    except TransportFailure as exc:
                        saved={'transport_error':str(exc),'http_status':None,'response_base64':None,'server_request_id':None}
                    saved.update(request_hash=prepared['request_hash'],attempt=attempt,started_at_utc=stamp,
                        elapsed_seconds=time.monotonic()-start,
                        duplicate_server_computation_possible=attempt>1)
                    save_json(path,{**saved,'record_hash':digest(saved)})
                status=saved['http_status']
                if status==200:
                    normalized=decode_response(saved)
                    result={**normalized,'logical_request_id':request_id,'transport_attempt_id':attempt,
                        'request_hash':prepared['request_hash'],'model_lock_hash':self.identity['model_lock_hash'],
                        'runtime_lock_hash':self.identity['runtime_lock_hash'],'http_status':status,
                        'elapsed_seconds':saved['elapsed_seconds'],'started_at_utc':saved['started_at_utc']}
                    save_json(folder/'accepted_response.json',result)
                    return result
                retry=saved['transport_error'] is not None or status in {408,429,500,502,503,504}
                if not retry:
                    raise ResponseFailure('NONRETRYABLE_HTTP_STATUS:'+str(status))
                if attempt<self.max_attempts:
                    self.sleeper(min(2**attempt,8))
            raise TransportFailure('TRANSPORT_ATTEMPTS_EXHAUSTED')
