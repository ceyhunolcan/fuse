"""CPU recovery prototype, not yet wired into the released runner.

Single writer only. Retains failed temporary files and broken journal bytes.
"""
import hashlib
import json
import os
import uuid
from pathlib import Path


def immutable_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
    payload=(json.dumps(value,sort_keys=True,allow_nan=False)+'\n').encode()
    with temporary.open('xb') as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    # Atomic no-overwrite publication on the same local filesystem.
    os.link(temporary,path)
    temporary.unlink()
    descriptor=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)
    return path


def append_event(path, event):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(event,sort_keys=True,allow_nan=False)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        if os.write(fd,data)!=len(data):raise OSError('short journal write; preserve original bytes')
        os.fsync(fd)
    finally:os.close(fd)


def inspect_journal(path):
    data=Path(path).read_bytes();events=[];offset=0;errors=[]
    for line in data.splitlines(keepends=True):
        try:
            if not line.endswith(b'\n'):raise ValueError('incomplete line terminator')
            event=json.loads(line)
            if not isinstance(event,dict):raise ValueError('event must be an object')
        except (ValueError,UnicodeError) as e:
            errors.append(dict(byte_offset=offset,reason=str(e)));break
        events.append(event);offset+=len(line)
    return dict(pass_=not errors,events=events,valid_prefix_bytes=offset,
                unresolved_bytes=len(data)-offset,errors=errors,
                raw_sha256=hashlib.sha256(data).hexdigest(),modified=False)
