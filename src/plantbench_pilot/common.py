"""Deterministic identities and durable, immutable request artifacts."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def encoded(value):
    return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def stable_seed(*parts):
    if not all(isinstance(part,str) for part in parts):
        raise ValueError('SEED_PARTS_MUST_BE_ORDERED_STRINGS')
    raw=json.dumps(list(parts),ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return int.from_bytes(hashlib.sha256(raw).digest()[:8],'big')%(2**31-1)


def safe_id(value):
    if not isinstance(value,str) or re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}',value) is None:
        raise ValueError('INVALID_ARTIFACT_ID')
    return value


def write_immutable(path,body):
    """Atomic exclusive publication; identical resumed writes are idempotent."""
    path=Path(path)
    if not isinstance(body,bytes):
        raise TypeError('EXACT_BYTES_REQUIRED')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():
        raise ValueError('ARTIFACT_SYMLINK_REJECTED')
    if path.exists():
        if path.read_bytes()!=body:
            raise ValueError('IMMUTABLE_ARTIFACT_CHANGED')
        return
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as stream:
        temporary=Path(stream.name)
        stream.write(body);stream.flush();os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary,path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes()!=body:
                raise ValueError('IMMUTABLE_ARTIFACT_CHANGED')
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(path,value):
    write_immutable(path,encoded(value)+b'\n')
