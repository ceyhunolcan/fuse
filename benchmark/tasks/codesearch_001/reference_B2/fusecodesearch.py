import difflib

import copy, io, json, re, tokenize

JS=re.compile(r"\s+|//[^\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`|(?:[$A-Za-z_]|[^\W\d])[$\w]*|\d+(?:\.\d+)?|===|!==|=>|==|!=|<=|>=|&&|\|\||\+\+|--|\.\.\.|[{}()\[\].,;:+*/%=!<>?&|^-]",re.UNICODE)

def lex(source,language,pattern=False):
    if not isinstance(source,str):raise ValueError('source must be text')
    if language=='javascript':
        result=[];pos=0
        while pos<len(source):
            m=JS.match(source,pos)
            if m is None:raise ValueError('invalid JavaScript token')
            text=m.group();start=pos
            if text=='/' and source.startswith('/*',pos):raise ValueError('unclosed block comment')
            pos=m.end()
            if text.isspace() or text.startswith('//') or text.startswith('/*'):continue
            kind='name' if re.fullmatch(r'(?:[$A-Za-z_]|[^\W\d])[$\w]*',text) else 'value'
            result.append((text,start,pos,kind))
        return result
    if language!='python':raise ValueError('unsupported language')
    offsets=[0]
    for line in source.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    result=[]
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.NEWLINE,tokenize.NL,tokenize.INDENT,tokenize.DEDENT,tokenize.COMMENT,tokenize.ENDMARKER):continue
            if token.type==tokenize.ERRORTOKEN:
                if token.string.isspace():continue
                if not (pattern and token.string=='$'):raise ValueError('invalid Python token')
            start=offsets[token.start[0]-1]+token.start[1];end=offsets[token.end[0]-1]+token.end[1]
            result.append((token.string,start,end,'name' if token.type==tokenize.NAME else 'value'))
    except (tokenize.TokenError,IndentationError,SyntaxError) as error:raise ValueError('invalid Python source') from error
    return result

def pattern_tokens(pattern,language):
    tokens=lex(pattern,language,False)

    if not tokens:raise ValueError('empty pattern')

    return tokens


def find(source,tokens,pattern):
    result=[];size=len(pattern)
    for start in range(len(tokens)-size+1):
        chosen=tokens[start:start+size]
        if [t[0] for t in chosen]==[t[0] for t in pattern]:
            left,right=chosen[0][1],chosen[-1][2];result.append({'start':left,'end':right,'text':source[left:right]})
    return result

def handle(request):
    if not isinstance(request,dict) or set(request)-{'files','pattern','replacement','apply'} or not {'files','pattern'}<=set(request) or not isinstance(request['files'],list) or not isinstance(request['pattern'],str) or not request['pattern'].strip():raise ValueError('invalid request')
    files=copy.deepcopy(request['files']);names=set();matches=[]
    for file in files:
        if not isinstance(file,dict) or set(file)!={'path','language','source'} or not isinstance(file['path'],str) or not file['path'] or file['path'] in names:raise ValueError('invalid/duplicate file')
        names.add(file['path'])

        tokens=lex(file['source'],file['language']);pattern=pattern_tokens(request['pattern'],file['language'])
        for match in find(file['source'],tokens,pattern):matches.append({'path':file['path'],**match})
    matches.sort(key=lambda m:(m['path'],m['start'],m['end']))
    result={'matches':matches}
    if 'replacement' in request:
        if not isinstance(request['replacement'],str) or type(request.get('apply',False)) is not bool:raise ValueError('invalid replacement/apply')
        changes=[];rewritten=copy.deepcopy(files)
        for file in rewritten:
            selected=[m for m in matches if m['path']==file['path']]
            if any(a['end']>b['start'] for a,b in zip(selected,selected[1:])):raise ValueError('overlapping replacements')
            before=file['source'];after=before
            for match in reversed(selected):after=after[:match['start']]+request['replacement']+after[match['end']:]
            lex(after,file['language'])
            if before!=after:changes.append({'path':file['path'],'before':before,'after':after,'diff':''.join(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),fromfile=file['path'],tofile=file['path']))})
            file['source']=after
        changes.sort(key=lambda c:c['path']);result.update(changes=changes,files=rewritten if request.get('apply',False) else files)
    elif 'apply' in request:raise ValueError('apply requires replacement')
    return result

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
