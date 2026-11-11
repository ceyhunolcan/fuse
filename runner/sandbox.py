"""Only the candidate repository is mounted into an agent container."""
import os
import re
import subprocess
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from runner.command_budget import CommandBudgetExpired, capped_timeout


class CandidateArtifactError(ValueError):
    pass


class SandboxCleanupError(OSError):
    """Container absence is unresolved; do not export or schedule another phase."""
    pass


def immutable_docker_image(image):
    """Preserve repository digest support and admit only full local content IDs."""
    return isinstance(image, str) and ('@sha256:' in image or
        re.fullmatch(r'sha256:[a-f0-9]{64}', image) is not None)


def confirm_container_absent(identifier):
    """A disconnected daemon is not evidence that an owned container is absent."""
    if not re.fullmatch(r'(?:[a-f0-9]{64}|fuse-cpu-[a-f0-9]{32})', identifier):
        raise ValueError('expected an owned container name or full container ID')
    try:
        result = subprocess.run(['docker', 'container', 'inspect', identifier], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxCleanupError('Docker absence inspection failed; cleanup unresolved') from exc
    diagnostic = result.stderr.strip().lower()
    missing = re.fullmatch(r'(?:error(?: response from daemon)?:\s*)?no such (?:object|container):\s*'
                           + re.escape(identifier), diagnostic)
    if result.returncode != 1 or missing is None:
        raise SandboxCleanupError('Docker did not confirm container absence; cleanup unresolved')
    return dict(container_identifier=identifier, returncode=result.returncode,
                absence_confirmed=True)


class DockerSandbox:
    def __init__(self, repo, image, user="1000:1000"):
        if not immutable_docker_image(image):
            raise ValueError("a locally available Docker image pinned by digest is required")
        self.repo,self.image,self.user=Path(repo).resolve(),image,user
        self.container=None
        self.name='fuse-cpu-'+uuid.uuid4().hex
        self.creation_attempted=False
        self.cleanup_verified=False
        self.cleanup_report=None

    def __enter__(self):
        if self.creation_attempted:
            raise ValueError('Docker sandbox instances are single use')
        # Disposable candidate files contain no host secrets. Allow the non-root
        # container user to edit bind-mounted files across differing host UIDs.
        for path in [self.repo,*self.repo.rglob("*")]:
            if path.is_symlink(): raise ValueError("candidate symlink unsupported")
            path.chmod(0o777 if path.is_dir() or os.access(path,os.X_OK) else 0o666)
        args=["docker","run","--name",self.name,"--pull=never","-d","--network=none","--cap-drop=ALL",
              "--security-opt=no-new-privileges","--read-only","--pids-limit=128","--memory=4g","--cpus=2",
              "--user",self.user,"--tmpfs","/tmp:rw,nosuid,size=128m",
              "--mount",f"type=bind,src={self.repo},dst=/workspace","--workdir","/workspace",
              "--env","HOME=/tmp","--env","PYTHONDONTWRITEBYTECODE=1",
              "--entrypoint","/bin/sh",self.image,"-c","while :; do sleep 60; done"]
        self.creation_attempted=True
        try:
            identifier=subprocess.run(args,capture_output=True,text=True,check=True,timeout=30).stdout.strip()
            if not re.fullmatch(r'[a-f0-9]{64}',identifier):
                raise ValueError('Docker creation returned no full container ID')
            self.container=identifier
        except BaseException:
            # A timed-out client may have created the container without returning
            # its ID. Remove only the unique name assigned before creation.
            self._cleanup()
            raise
        return self

    def _cleanup(self):
        identifier=self.container or self.name
        removal_error=None
        removal=None
        try:
            removal=subprocess.run(["docker","rm","-f",identifier],capture_output=True,text=True,timeout=30)
        except (OSError,subprocess.SubprocessError) as exc:
            removal_error=exc
        try:
            absence=confirm_container_absent(identifier)
        except SandboxCleanupError as exc:
            if removal_error is not None:
                raise exc from removal_error
            raise
        self.cleanup_report=dict(absence=absence,
            removal_returncode=removal.returncode if removal is not None else None,
            removal_error_class=type(removal_error).__name__ if removal_error is not None else None)
        self.cleanup_verified=True

    def run(self, command, timeout):
        if not self.container or self.cleanup_verified:
            raise ValueError('Docker command requires an active owned container')
        timeout=capped_timeout(timeout)
        process=subprocess.Popen(["docker","exec",self.container,"/bin/sh","-c",command],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        buffers=[bytearray(),bytearray()]
        truncated=[False,False]
        def drain(stream,index):
            while data:=stream.read(4096):
                space=20000-len(buffers[index])
                buffers[index].extend(data[:space])
                truncated[index] |= len(data)>space
        threads=[threading.Thread(target=drain,args=(stream,index),daemon=True)
                 for index,stream in enumerate([process.stdout,process.stderr])]
        for thread in threads: thread.start()
        expired=None
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            expired=exc
            process.kill();process.wait(timeout=2)
            self._cleanup()
        finally:
            for thread in threads: thread.join(timeout=1)
        if expired is not None:
            captured=dict(stdout=buffers[0].decode(errors='replace'),stderr=buffers[1].decode(errors='replace'),
                          truncated=any(truncated),output_capture_complete=not any(t.is_alive() for t in threads))
            raise CommandBudgetExpired(command,timeout,'docker',captured) from expired
        return {"returncode":process.returncode,"stdout":buffers[0].decode(errors="replace"),
                "stderr":buffers[1].decode(errors="replace"),"truncated":any(truncated)}

    def __exit__(self,*args):
        if (self.container or self.creation_attempted) and not self.cleanup_verified:
            self._cleanup()


@contextmanager
def grading_environment(repo, config):
    """Fresh container after the agent session; trusted tests stay on the host."""
    from unittest.mock import patch
    if config.get('sandbox_backend', 'docker') == 'chroot-circuit-v0':
        from runner.chroot_sandbox import ChrootSandbox
        with ChrootSandbox(repo,config,export=False) as sandbox:
            with patch.dict(os.environ,{'FUSE_TEST_CHROOT':str(sandbox.descriptor)}):
                yield
    else:
        with DockerSandbox(repo,config["container_image"],config.get("container_user","1000:1000")) as sandbox:
            with patch.dict(os.environ,{"FUSE_TEST_CONTAINER":sandbox.container}):
                yield


def sandbox_for_config(repo, config):
    backend=config.get('sandbox_backend','docker')
    if backend=='docker':
        return DockerSandbox(repo,config['container_image'],config.get('container_user','1000:1000'))
    if backend=='chroot-circuit-v0':
        from runner.chroot_sandbox import ChrootSandbox
        return ChrootSandbox(repo,config)
    raise ValueError('unsupported sandbox backend')
