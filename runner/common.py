import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "benchmark/tasks/circuit_001"
# Public release: the three tasks sealed and run in the study may be used with a model.
MODEL_TASKS = frozenset({"circuit_001", "codesearch_001", "textdrop_001"})


def resolve_task(task_id='circuit_001'):
    if not isinstance(task_id,str) or not re.fullmatch(r'[a-z]+_\d{3}',task_id):
        raise ValueError('invalid task identity')
    task=ROOT/'benchmark/tasks'/task_id
    if not task.is_dir() or not (task/'A/requirement.md').is_file():
        raise ValueError('unknown or unauthored task')
    return task


def install_reference(repo,reference):
    """Trusted reference fixture only; never an agent success retry."""
    for path in Path(reference).rglob('*'):
        relative=path.relative_to(reference)
        if set(relative.parts)&{'.git','__pycache__','.pytest_cache'}:continue
        if path.is_file():
            target=Path(repo)/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(path.read_bytes())


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+"\n")


def copy_tree(source, destination):
    source,destination = Path(source),Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if set(relative.parts) & {".git","__pycache__",".pytest_cache"}:
            continue
        if path.is_symlink():
            raise ValueError(f"symlinks are unsupported: {relative}")
        if path.is_file():
            target = destination/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path,target)


def tree_digest(repo):
    digest=hashlib.sha256()
    for path in sorted(Path(repo).rglob("*")):
        relative=path.relative_to(repo)
        if set(relative.parts)&{".git","__pycache__",".pytest_cache"}: continue
        if path.is_symlink(): raise ValueError("symlink in snapshot")
        if path.is_file():
            digest.update(str(relative).encode()+b"\0"+path.read_bytes()+b"\0")
    return digest.hexdigest()


def git(repo, *args):
    result=subprocess.run(["git","-c","user.name=FUSE","-c","user.email=fuse@example.invalid",*args],cwd=repo,capture_output=True,text=True,check=True)
    return result.stdout.strip()


def initialize(repo):
    git(repo,"init","-q")
    git(repo,"add","-f",".")
    git(repo,"commit","-qm","Initial repository")
    return git(repo,"rev-parse","HEAD")


def append_record(path, record):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a") as file: file.write(json.dumps(record,sort_keys=True)+"\n")
