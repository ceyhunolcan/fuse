import json
from pathlib import Path
from runner.common import copy_tree,initialize,tree_digest


def fork(artifact, destination):
    artifact=Path(artifact)
    metadata=json.loads((artifact/"artifact.json").read_text())
    if tree_digest(artifact/"repo") != metadata["tree_sha256"]:
        raise ValueError("artifact digest mismatch")
    copy_tree(artifact/"repo",destination)
    initialize(destination)
    return metadata
