
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
    tokens=lex(pattern,language,True)
    combined=[];i=0
    while i<len(tokens):
        value,start,end,kind=tokens[i]
        if language=='python' and value=='$':
            if i+1>=len(tokens) or tokens[i+1][3]!='name' or tokens[i+1][1]!=end:raise ValueError('invalid metavariable')
            following=tokens[i+1]
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',following[0]):raise ValueError('invalid metavariable')
            combined.append(('$'+following[0],start,following[2],'hole'));i+=2
        else:
            if value.startswith('$') and kind=='name':
                if not re.fullmatch(r'\$[A-Za-z_][A-Za-z_0-9]*',value):raise ValueError('invalid metavariable')
                kind='hole'
            combined.append((value,start,end,kind));i+=1
    tokens=combined
    if not tokens:raise ValueError('empty pattern')
    if all(t[3]=='hole' or t[0]=='...' for t in tokens):raise ValueError('pattern requires a literal token')
    return tokens

def balanced(tokens):
    stack=[];pairs={')':'(',']':'[','}':'{'}
    for value,*_ in tokens:
        if value==';':return False
        if value in ('(','[','{'):stack.append(value)
        elif value in pairs:
            if not stack or stack.pop()!=pairs[value]:return False
    return not stack

def find(source,tokens,pattern):
    special=any(t[3]=='hole' or t[0]=='...' for t in pattern);result=[]
    def walk(index,p,captures):
        if p==len(pattern):return index,captures
        item=pattern[p]
        if item[0]=='...':
            for end in range(index,len(tokens)+1):
                if balanced(tokens[index:end]):
                    found=walk(end,p+1,dict(captures))
                    if found is not None:return found
            return None
        if index>=len(tokens):return None
        token=tokens[index]
        if item[3]=='hole':
            key=item[0][1:]
            if token[3]!='name' or key in captures and captures[key]!=token[0]:return None
            captures=dict(captures);captures[key]=token[0]
        elif item[0]!=token[0]:return None
        return walk(index+1,p+1,captures)
    for start in range(len(tokens)):
        found=walk(start,0,{})
        if found is None:continue
        end,captures=found
        if end<=start:continue
        left,right=tokens[start][1],tokens[end-1][2];match={'start':left,'end':right,'text':source[left:right]}
        if special:match['captures']=captures
        result.append(match)
    return result

def handle(request):
    if not isinstance(request,dict) or set(request)-{'files','pattern','pattern_mode'} or not {'files','pattern'}<=set(request) or not isinstance(request['files'],list) or not isinstance(request['pattern'],str) or not request['pattern'].strip():raise ValueError('invalid request')
    mode=request.get('pattern_mode','exact')
    if mode not in ('exact','structural'):raise ValueError('invalid pattern mode')
    files=copy.deepcopy(request['files']);names=set();matches=[]
    for file in files:
        if not isinstance(file,dict) or set(file)!={'path','language','source'} or not isinstance(file['path'],str) or not file['path'] or file['path'] in names:raise ValueError('invalid/duplicate file')
        names.add(file['path'])

        tokens=lex(file['source'],file['language'])
        if mode=='structural':pattern=pattern_tokens(request['pattern'],file['language'])
        else:
            pattern=lex(request['pattern'],file['language'])
            if not pattern:raise ValueError('empty pattern')
        if mode=='structural':found=find(file['source'],tokens,pattern)
        else:
            found=[];size=len(pattern)
            for start in range(len(tokens)-size+1):
                chosen=tokens[start:start+size]
                if [t[0] for t in chosen]==[t[0] for t in pattern]:
                    left,right=chosen[0][1],chosen[-1][2]
                    found.append({'start':left,'end':right,'text':file['source'][left:right]})
        for match in found:matches.append({'path':file['path'],**match})
    matches.sort(key=lambda m:(m['path'],m['start'],m['end']))
    result={'matches':matches}

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
