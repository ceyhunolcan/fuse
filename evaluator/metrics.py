import difflib
from pathlib import Path


def texts(repo):
    result = {}
    for path in Path(repo).rglob("*"):
        if path.is_file() and not path.is_symlink() and not set(path.parts) & {".git","__pycache__",".pytest_cache"}:
            try:
                result[str(path.relative_to(repo))] = path.read_text().splitlines()
            except (UnicodeError, OSError):
                pass
    return result


def changes(before, after):
    left, right = texts(before), texts(after)
    added = deleted = files = 0
    for name in set(left) | set(right):
        a,b = left.get(name,[]), right.get(name,[])
        if a == b:
            continue
        files += 1
        for op,i,j,k,l in difflib.SequenceMatcher(a=a,b=b,autojunk=False).get_opcodes():
            if op != "equal":
                deleted += j-i
                added += l-k
    return {"loc_added":added,"loc_deleted":deleted,"loc_changed":added+deleted,"files_touched":files}


def invalidation(base, upstream, downstream):
    b,a,c = texts(base),texts(upstream),texts(downstream)
    authored = invalidated = 0
    for name,lines in a.items():
        new = set()
        for op,_,_,start,end in difflib.SequenceMatcher(a=b.get(name,[]),b=lines,autojunk=False).get_opcodes():
            if op != "equal": new.update(range(start,end))
        survives = set()
        for op,start,end,_,_ in difflib.SequenceMatcher(a=lines,b=c.get(name,[]),autojunk=False).get_opcodes():
            if op == "equal": survives.update(range(start,end))
        authored += len(new)
        invalidated += len(new-survives)
    return {"invalidation":invalidated,"authored_lines_at_risk":authored,
            "invalidation_fraction":invalidated/authored if authored else None}
