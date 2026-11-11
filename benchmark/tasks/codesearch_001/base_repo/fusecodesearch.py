
import copy, io, json, re, tokenize


def lex(source,language,pattern=False):
    if not isinstance(source,str):raise ValueError('source must be text')
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

def handle(request):
    if not isinstance(request,dict) or set(request)-{'files','pattern'} or not {'files','pattern'}<=set(request) or not isinstance(request['files'],list) or not isinstance(request['pattern'],str) or not request['pattern'].strip():raise ValueError('invalid request')
    files=copy.deepcopy(request['files']);names=set();matches=[]
    for file in files:
        if not isinstance(file,dict) or set(file)!={'path','language','source'} or not isinstance(file['path'],str) or not file['path'] or file['path'] in names:raise ValueError('invalid/duplicate file')
        names.add(file['path'])
        if file['language']!='python' or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',request['pattern']):raise ValueError('starter supports Python identifier search only')
        for value,start,end,kind in lex(file['source'],file['language']):
            if kind=='name' and value==request['pattern']:
                matches.append({'path':file['path'],'start':start,'end':end,'text':file['source'][start:end]})
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
