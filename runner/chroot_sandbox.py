"""Circuit-only backend with pinned source/runtime and sanitized export.

The host controller stays trusted; candidate programs run after chroot, UID/GID
drop and a default-deny seccomp filter. No host-shell fallback is provided.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from runner import chroot_linux
from runner.sandbox import CandidateArtifactError
from runner.command_budget import POLICY, MIN_LAUNCH_SECONDS, CommandBudgetExpired, capped_timeout


REQUIRED_CHECKS = {'normal_tools', 'boundary_execution', 'hidden_files_and_sibling',
                  'privilege_drop', 'closed_host_descriptors', 'network_and_escalation_denied',
                  'workspace_read_write', 'timeout', 'background_cleanup', 'output_cap'}


def validate_identity(config):
    identity = config.get('sandbox_identity', {})
    for key in ['source_sha256', 'runtime_sha256', 'evidence_sha256', 'evidence_path']:
        if not identity.get(key):
            raise ValueError('missing chroot identity: ' + key)
    source = Path(chroot_linux.__file__)
    if hashlib.sha256(source.read_bytes()).hexdigest() != identity['source_sha256']:
        raise ValueError('chroot launcher source identity changed')
    evidence = Path(identity['evidence_path'])
    if not evidence.is_absolute():
        evidence = Path(__file__).resolve().parents[1] / evidence
    if hashlib.sha256(evidence.read_bytes()).hexdigest() != identity['evidence_sha256']:
        raise ValueError('sandbox evidence identity changed')
    report = json.loads(evidence.read_text())
    if not report.get('accepted') or not REQUIRED_CHECKS.issubset(report.get('checks', {})):
        raise ValueError('incomplete sandbox validation')
    if not all(report['checks'][name]['pass'] for name in REQUIRED_CHECKS):
        raise ValueError('sandbox validation failed')
    if report['source_sha256'] != identity['source_sha256'] or report['runtime_sha256'] != identity['runtime_sha256']:
        raise ValueError('sandbox evidence does not match configured source/runtime')
    return identity


def sanitized_export(source, destination):
    """Copy stopped candidate files without trusting its Git state or links."""
    source, destination = Path(source), Path(destination)
    files = []
    total = 0
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if set(relative.parts) & {'.git', '__pycache__', '.pytest_cache'}:
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CandidateArtifactError('candidate export contains special file or link')
        total += info.st_size
        if info.st_size > 8 * 1024**2 or total > 64 * 1024**2 or len(files) >= 4096:
            raise CandidateArtifactError('candidate export exceeds Circuit size limits')
        files.append((path, relative, info.st_mode))
    destination.mkdir()
    for path, relative, mode in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o755 if mode & 0o111 else 0o644)


class ChrootSandbox:
    def __init__(self, repo, config, export=True):
        self.repo = Path(repo).resolve()
        self.config = config
        self.identity = validate_identity(config)
        self.export = export
        self.parent = None
        self.backend = None
        self.container = None

    def __enter__(self):
        self.parent = tempfile.TemporaryDirectory(prefix='fuse-isolated-command-')
        try:
            self.backend = chroot_linux.ChrootCandidate(self.repo, self.parent.name).prepare()
            if self.backend.runtime_identity != self.identity['runtime_sha256']:
                raise ValueError('chroot runtime changed; rerun validation and freeze')
            self.descriptor = Path(self.parent.name) / 'grading.json'
            self.descriptor.write_text(json.dumps({'root': str(self.backend.root), 'uid': self.backend.uid,
                                                   'launcher': str(self.backend.launcher)}))
            self.descriptor.chmod(0o600)
            return self
        except Exception:
            if self.backend is not None:
                self.backend.close()
            self.parent.cleanup()
            raise

    def run(self, command, timeout):
        timeout=capped_timeout(timeout)
        prospective=self.config.get('command_budget_policy') == POLICY
        if prospective and timeout < MIN_LAUNCH_SECONDS:
            raise ValueError('remaining budget below pinned chroot launch minimum')
        result=self.backend.run(command, timeout)
        if prospective and result.get('timed_out') is True:
            captured=dict(stdout=result.get('stdout'),stderr=result.get('stderr'),
                          truncated=result.get('truncated'),output_capture_complete=True)
            raise CommandBudgetExpired(command,timeout,'chroot-circuit-v0',captured)
        return result

    def __exit__(self, kind, value, traceback):
        try:
            chroot_linux.kill_uid(self.backend.uid)
            # Retain completed edits even when transport or another controller
            # operation failed. Export still runs only after candidate cleanup
            # and through the same file/link/size validation.
            if self.export:
                clean = Path(self.parent.name) / 'export'
                sanitized_export(self.backend.root / 'workspace', clean)
                for path in self.repo.iterdir():
                    if path.name != '.git':
                        if path.is_dir(): shutil.rmtree(path)
                        else: path.unlink()
                for path in clean.iterdir():
                    shutil.move(str(path), str(self.repo / path.name))
        finally:
            self.backend.close()
            self.parent.cleanup()
