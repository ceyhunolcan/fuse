"""Bounded trusted Docker CLI bridge, sent as inline source with Python isolation."""

# This code runs in the selected isolated container; candidate stdout is captured
# separately and cannot be mistaken for the bridge's JSON result envelope.
DOCKER_CODE = r'''
import json,os,signal,subprocess,sys,threading
args=json.loads(sys.argv[1]);timeout=float(sys.argv[2]);capture=int(sys.argv[3])
if not 1 <= capture <= 16*1024**2:raise ValueError('invalid trusted grader capture allowance')
p=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                   start_new_session=True,close_fds=True)
buffers=[bytearray(),bytearray()];truncated=[False,False]
def drain(stream,index):
    while data:=stream.read(4096):
        space=capture-len(buffers[index]);buffers[index].extend(data[:space]);truncated[index]|=len(data)>space
threads=[threading.Thread(target=drain,args=(stream,index),daemon=True)
         for index,stream in enumerate([p.stdout,p.stderr])]
for t in threads:t.start()
expired=False
try:
    p.wait(timeout=timeout)
except subprocess.TimeoutExpired:
    expired=True
finally:
    try:os.killpg(p.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    p.wait(timeout=2)
    for t in threads:t.join(timeout=2)
if any(t.is_alive() for t in threads):raise RuntimeError('candidate output pipe remained open')
print(json.dumps(dict(returncode=p.returncode,stdout=buffers[0].decode(errors='replace'),
    stderr=buffers[1].decode(errors='replace'),timed_out=expired,truncated=any(truncated))))
'''
