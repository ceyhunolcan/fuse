
import copy, html, json, re
from urllib.parse import urlsplit

def empty():return {'version':1,'next_id':1,'notes':{}}

def validate(state):
    if not isinstance(state,dict) or set(state)!={'version','next_id','notes'} or type(state['version']) is not int or state['version']!=1 or type(state['next_id']) is not int or state['next_id']<1 or not isinstance(state['notes'],dict):raise ValueError('invalid store')
    for key,note in state['notes'].items():
        if not isinstance(note,dict) or set(note)!={'id','title','body','format'} or type(note['id']) is not int or key!=str(note['id']) or not 0<note['id']<state['next_id']:raise ValueError('invalid note')
        content(note)


def content(note):
    if not isinstance(note.get('title'),str) or not 1<=len(note['title'])<=120 or not isinstance(note.get('body'),str) or len(note['body'])>10000 or note.get('format') not in ('plain','markdown'):raise ValueError('invalid content')

def render(note):
    if note['format']=='plain':return '<pre>'+html.escape(note['body'])+'</pre>'
    def inline(text):
        token=re.compile(r'\[([^\]\n]+)\]\(([^()\n]+)\)|(?<!\*)\*\*([^*\n]+)\*\*(?!\*)|(?<!\*)\*([^*\n]+)\*(?!\*)')
        parts=[];position=0
        for match in token.finditer(text):
            parts.append(html.escape(text[position:match.start()]))
            label,url,bold,italic=match.groups()
            if label is not None:
                try:parsed=urlsplit(url)
                except ValueError:parsed=None
                if parsed is not None and parsed.scheme in ('http','https') and parsed.netloc:
                    parts.append('<a href="'+html.escape(url)+'">'+html.escape(label)+'</a>')
                else:parts.append(html.escape(match.group()))
            elif bold is not None:parts.append('<strong>'+html.escape(bold)+'</strong>')
            else:parts.append('<em>'+html.escape(italic)+'</em>')
            position=match.end()
        parts.append(html.escape(text[position:]))
        return ''.join(parts)
    lines=note['body'].splitlines();parts=[];paragraph=[];i=0
    def flush():
        if paragraph:parts.append('<p>'+inline('\n'.join(paragraph))+'</p>');paragraph.clear()
    while i<len(lines):
        line=lines[i]
        if line=='```':
            flush();i+=1;code=[]
            while i<len(lines) and lines[i]!='```':code.append(lines[i]);i+=1
            if i==len(lines):raise ValueError('unclosed code fence')
            parts.append('<pre><code>'+html.escape('\n'.join(code))+'</code></pre>')
        elif not line.strip():flush()
        elif re.match(r'^#{1,3} ',line):
            flush();level=len(line)-len(line.lstrip('#'));parts.append(f'<h{level}>'+inline(line[level+1:])+f'</h{level}>')
        else:paragraph.append(line)
        i+=1
    flush();return '\n'.join(parts)
    raise ValueError('unsupported rendering')

def handle(request):
    if not isinstance(request,dict) or set(request)-{'state','command'} or 'command' not in request:raise ValueError('invalid request')

    state=copy.deepcopy(empty() if request.get('state') is None else request['state']);validate(state)
    command=request['command']
    if not isinstance(command,dict):raise ValueError('invalid command')
    op=command.get('op')

    fields={'create':{'op','title','body','format'},'get':{'op','id'},'list':{'op'},'delete':{'op','id'},'update':{'op','id','title','body','format'},'render':{'op','id'},'export':{'op'},'import':{'op','serialized'}}
    if op not in fields or set(command)-fields[op]:raise ValueError('invalid command fields')
    if op=='create':
        if not {'title','body'}<=set(command):raise ValueError('missing content')
        identifier=state['next_id'];note={'id':identifier,'title':command['title'],'body':command['body'],'format':command.get('format','plain')}

        content(note);state['notes'][str(identifier)]=note;state['next_id']+=1;result=copy.deepcopy(note)
    elif op=='list':
        result=[copy.deepcopy(note) for _,note in sorted(state['notes'].items(),key=lambda item:int(item[0])) ]
    elif op in ('get','delete','update','render'):
        if type(command.get('id')) is not int or str(command['id']) not in state['notes']:raise ValueError('missing note')
        note=state['notes'][str(command['id'])]

        if op=='get':result=copy.deepcopy(note)
        elif op=='delete':del state['notes'][str(command['id'])];result={'deleted':command['id']}
        elif op=='update':
            if not set(command)&{'title','body','format'}:raise ValueError('empty update')
            for key in ('title','body','format'):
                if key in command:note[key]=command[key]
            content(note);result=copy.deepcopy(note)
        elif op=='render':result={'html':render(note)}
    elif op=='export':result={'serialized':json.dumps(state,ensure_ascii=False,sort_keys=True)}
    elif op=='import':
        if not isinstance(command.get('serialized'),str):raise ValueError('invalid serialized store')
        state=json.loads(command['serialized']);validate(state);result={'imported':len(state['notes'])}
    validate(state)
    output={'state':state,'result':result}

    return output

if __name__ == '__main__':
    import argparse, sys
    parser=argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args=parser.parse_args()
    try:
        output=handle(json.loads(args.request))
        print(json.dumps(output,ensure_ascii=False,sort_keys=True))
    except (ValueError,TypeError,KeyError,IndexError) as error:
        print(type(error).__name__ + ': ' + str(error),file=sys.stderr)
        sys.exit(2)
