"""Development-only Linux chroot prototype. No host-shell fallback.

Not selected by the FUSE runner until live validation and protocol review pass.
The launcher is a separate trusted interpreter, never a preexec_fn in Jupyter.
"""
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time

ALLOW = 0x7fff0000
ERRNO = 0x00050000
# Deliberately permits local stdlib/Python/Git work, not arbitrary Linux tools.
LOCAL_SYSCALLS = '''read write readv writev pread64 pwrite64 preadv pwritev
close close_range open openat openat2 creat access faccessat faccessat2
stat fstat lstat newfstatat statx statfs fstatfs lseek getdents getdents64
readlink readlinkat getcwd chdir fchdir mkdir mkdirat rmdir unlink unlinkat
rename renameat renameat2 link linkat symlink symlinkat chmod fchmod fchmodat
fchmodat2 umask truncate ftruncate fallocate fsync fdatasync sync_file_range
utime utimes futimesat utimensat flock fcntl dup dup2 dup3 pipe pipe2
mmap mprotect munmap mremap madvise brk msync mincore
rt_sigaction rt_sigprocmask rt_sigreturn rt_sigsuspend rt_sigpending
rt_sigtimedwait sigaltstack restart_syscall
getpid getppid gettid getuid geteuid getgid getegid getgroups
getresuid getresgid uname sysinfo getrandom time gettimeofday
clock_gettime clock_getres clock_nanosleep nanosleep
poll ppoll select pselect6 epoll_create epoll_create1 epoll_ctl epoll_wait
epoll_pwait epoll_pwait2
futex futex_waitv set_tid_address set_robust_list get_robust_list rseq
arch_prctl sched_yield sched_getaffinity sched_getparam sched_getscheduler
getpriority getrlimit setrlimit prlimit64 getrusage times
fork vfork execve execveat wait4 waitid exit exit_group
getxattr lgetxattr fgetxattr listxattr llistxattr flistxattr
capget'''.split()


class Comparison(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]


def syscall_filter():
    library = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                               ctypes.c_int, ctypes.c_uint,
                                               ctypes.POINTER(Comparison)]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(ERRNO | errno.EPERM)
    if not context:
        raise RuntimeError('seccomp_init failed')
    def rule(name, action, comparisons=()):
        number = library.seccomp_syscall_resolve_name(name.encode())
        if number == -1:
            return
        array = (Comparison * len(comparisons))(*comparisons) if comparisons else None
        result = library.seccomp_rule_add_array(context, action, number, len(comparisons), array)
        if result < 0:
            raise RuntimeError('seccomp rule failed: ' + name + ':' + str(result))
    try:
        for name in LOCAL_SYSCALLS:
            rule(name, ALLOW)
        # Legacy clone may create processes/threads but no namespaces.
        namespace_flags = 0x00000080 | 0x00020000 | 0x02000000 | 0x04000000 | 0x08000000 | 0x10000000 | 0x20000000 | 0x40000000
        rule('clone', ALLOW, [Comparison(0, 7, namespace_flags, 0)])
        # libc falls back to clone when clone3 is unavailable.
        rule('clone3', ERRNO | errno.ENOSYS)
        # Permit inspection only; no changing privileges, seccomp or session.
        rule('prctl', ALLOW, [Comparison(0, 4, 39, 0)])  # PR_GET_NO_NEW_PRIVS
        # CPython's script loader marks its file descriptor non-inheritable
        # with FIOCLEX. Permit only these descriptor flags, never device ioctls.
        for request in [0x5451, 0x5450]:
            rule('ioctl', ALLOW, [Comparison(1, 4, request, 0)])
        result = library.seccomp_load(context)
        if result < 0:
            raise RuntimeError('seccomp_load failed: ' + str(result))
    finally:
        library.seccomp_release(context)


def launch(root, uid, seconds, command):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() != 0:
        raise RuntimeError('launcher requires Linux x86_64 root')
    libc = ctypes.CDLL(None, use_errno=True)
    # Preload the security library before changing root. Fail closed if absent.
    ctypes.CDLL('libseccomp.so.2')
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'no_new_privs failed')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(seconds) + 1, math.ceil(seconds) + 1))
    host_fd_limit = min(1048576, resource.getrlimit(resource.RLIMIT_NOFILE)[1])
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    os.chroot(root)
    os.chdir('/workspace')
    os.setgroups([])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    # Do not carry host directory descriptors into candidate code.
    os.closerange(3, int(host_fd_limit))
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]
    header, data = Header(0x20080522, 0), (Data * 2)()
    if libc.capget(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise RuntimeError('capget failed')
    if any(item.effective or item.permitted or item.inheritable for item in data):
        raise RuntimeError('capabilities survived privilege drop')
    syscall_filter()
    environment = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMPDIR': '/tmp',
                   'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    os.execve('/bin/sh', ['/bin/sh', '-c', command], environment)


def uid_processes(uid):
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            line = next(line for line in (entry / 'status').read_text().splitlines() if line.startswith('Uid:'))
            if any(int(value) == uid for value in line.split()[1:]):
                result.append(int(entry.name))
        except (OSError, StopIteration, ValueError):
            pass
    return result


def kill_uid(uid):
    # Dedicated per-sandbox UID; never signal a process from another identity.
    for _ in range(10):
        found = uid_processes(uid)
        if not found:
            return
        for pid in found:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(.02)
    # Zombies can remain until their parent reaps them; they cannot execute.
    live = []
    for pid in uid_processes(uid):
        try:
            if 'State:\tZ' not in Path('/proc', str(pid), 'status').read_text():
                live.append(pid)
        except OSError:
            pass
    if live:
        raise RuntimeError('sandbox process cleanup failed')


USED_UIDS = set()


class ChrootCandidate:
    def __init__(self, repo, parent):
        if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() != 0:
            raise RuntimeError('chroot prototype requires Linux x86_64 root')
        self.repo = Path(repo).resolve()
        self.root = Path(tempfile.mkdtemp(prefix='rootfs-', dir=parent))
        self.uid = 700000 + secrets.randbelow(100000000)
        while self.uid in USED_UIDS or uid_processes(self.uid):
            self.uid = 700000 + secrets.randbelow(100000000)
        USED_UIDS.add(self.uid)
        self.copied = set()
        self.launcher = Path(__file__).resolve()

    def copy_binary(self, source, destination=None):
        source = Path(source)
        destination = Path(destination or source)
        target = self.root / str(destination).lstrip('/')
        if str(destination) in self.copied:
            return
        self.copied.add(str(destination))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.resolve(), target)
        target.chmod(0o755 if os.access(source, os.X_OK) else 0o644)
        result = subprocess.run(['/usr/bin/ldd', str(source.resolve())], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            if 'not found' in line:
                raise RuntimeError('missing runtime dependency')
            for token in line.split():
                if token.startswith('/') and Path(token).is_file():
                    self.copy_binary(token)

    def prepare(self):
        ctypes.CDLL('libseccomp.so.2')
        tools = ['sh', 'git', 'ls', 'cat', 'mkdir', 'rm', 'cp', 'mv', 'find',
                 'head', 'tail', 'sed', 'awk', 'sort', 'wc', 'diff', 'touch', 'sleep', 'env', 'test']
        for name in tools:
            source = shutil.which(name)
            if not source:
                raise RuntimeError('missing tool: ' + name)
            self.copy_binary(source, '/bin/' + name)
        self.copy_binary(sys.executable, '/usr/bin/python')
        (self.root / 'usr/bin/python3').symlink_to('python')
        stdlib = Path(sysconfig.get_path('stdlib'))
        destination = self.root / 'usr/lib' / ('python' + '.'.join(map(str, sys.version_info[:2])))
        shutil.copytree(stdlib, destination,
                        ignore=shutil.ignore_patterns('site-packages', 'dist-packages', '__pycache__', '*.pyc', 'ensurepip', 'idlelib', 'tkinter', 'test', 'sitecustomize.py', 'usercustomize.py'),
                        symlinks=False)
        for extension in destination.rglob('*.so'):
            relative = extension.relative_to(destination)
            original = stdlib / relative
            self.copy_binary(original, '/' + str(extension.relative_to(self.root)))
        # Git needs its helper binaries for local operations, never host configs.
        git_exec = subprocess.run(['git', '--exec-path'], capture_output=True, text=True, check=True).stdout.strip()
        git_destination = self.root / 'usr/lib/git-core'
        git_destination.mkdir(parents=True, exist_ok=True)
        for name in ['git', 'git-sh-i18n', 'git-sh-setup']:
            source = Path(git_exec) / name
            if source.is_file():
                self.copy_binary(source, '/usr/lib/git-core/' + name)
        (self.root / 'etc').mkdir(exist_ok=True)
        (self.root / 'etc/passwd').write_text(f'fuse:x:{self.uid}:{self.uid}::/tmp:/bin/sh\n')
        (self.root / 'etc/group').write_text(f'fuse:x:{self.uid}:\n')
        # No /proc, /sys, real devices, host mounts, package sites or model input.
        (self.root / 'dev').mkdir(exist_ok=True)
        (self.root / 'dev/null').write_bytes(b'')
        (self.root / 'dev/null').chmod(0o666)  # regular file, not host device
        for path in [self.root, *self.root.rglob('*')]:
            if path.is_symlink():
                continue
            path.chmod(0o755 if path.is_dir() or os.access(path, os.X_OK) else 0o644)
            os.chown(path, 0, 0)
        (self.root / 'dev/null').chmod(0o666)
        for path in [self.repo, *self.repo.rglob('*')]:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('candidate special file, hardlink or symlink unsupported')
        shutil.copytree(self.repo, self.root / 'workspace')
        (self.root / 'tmp').mkdir()
        for directory in ['workspace', 'tmp']:
            for path in [self.root / directory, *(self.root / directory).rglob('*')]:
                os.chown(path, self.uid, self.uid)
                path.chmod(0o700 if path.is_dir() or os.access(path, os.X_OK) else 0o600)
        self.runtime_identity = self.digest_runtime()
        return self

    def digest_runtime(self):
        digest = hashlib.sha256()
        for path in sorted(self.root.rglob('*')):
            relative = path.relative_to(self.root)
            if relative.parts[0] in ['workspace', 'tmp', 'etc', 'dev']:
                continue
            digest.update(str(relative).encode() + b'\0')
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def run(self, command, timeout=30, output_max_bytes=20000):
        if type(output_max_bytes) is not int or not 1 <= output_max_bytes <= 16*1024**2:
            raise ValueError('invalid trusted output capture allowance')
        timeout = min(60, max(.1, timeout))
        process = subprocess.Popen([sys.executable, '-I', str(self.launcher), '--launch', str(self.root), str(self.uid), str(timeout), command],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, start_new_session=True,
                                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        buffers, truncated = [bytearray(), bytearray()], [False, False]
        def drain(stream, index):
            while data := stream.read(4096):
                space = max(0, output_max_bytes - len(buffers[index]))
                buffers[index].extend(data[:space])
                truncated[index] |= len(data) > space
        threads = [threading.Thread(target=drain, args=(stream, index), daemon=True)
                   for index, stream in enumerate([process.stdout, process.stderr])]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            kill_uid(self.uid)
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError('sandbox output pipe remained open')
        return {'returncode': process.returncode, 'stdout': buffers[0].decode(errors='replace'),
                'stderr': buffers[1].decode(errors='replace'), 'truncated': any(truncated), 'timed_out': timed_out}

    def close(self):
        kill_uid(self.uid)
        shutil.rmtree(self.root)


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--launch':
    launch(sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), sys.argv[5])
