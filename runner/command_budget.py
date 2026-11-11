"""Prospective command watchdog policy; no historical outcome conversion."""
import math
import subprocess

POLICY = 'command-budget-v1'
BACKEND_CEILING_SECONDS = 60
MIN_LAUNCH_SECONDS = .1  # The pinned chroot launcher has this minimum.


class CommandBudgetExpired(subprocess.TimeoutExpired):
    """The backend's declared command wait expired, after candidate cleanup."""
    def __init__(self, command, timeout, backend, captured_result=None):
        super().__init__(command, timeout)
        self.backend = backend
        self.captured_result = captured_result


class SessionLaunchBudgetExpired(TimeoutError):
    """Insufficient declared session allowance to start the pinned launcher."""


def capped_timeout(timeout, cap=BACKEND_CEILING_SECONDS):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or type(cap) not in (int, float) or not math.isfinite(cap) or not 0 < cap <= 60):
        raise ValueError('invalid command wait budget')
    return min(timeout, cap)


def validate_policy(config):
    policy = config.get('command_budget_policy')
    if policy is None:
        if 'max_command_seconds' in config:
            raise ValueError('command cap requires an explicit command policy')
        return
    if policy != POLICY or config.get('timeout_policy') != 'session-deadline-v1':
        raise ValueError('command policy requires session-deadline-v1')
    if type(config.get('max_command_seconds')) is not int or not 1 <= config['max_command_seconds'] <= 60:
        raise ValueError('command cap must be an integer from 1 through 60')


def receipt(remaining, cap):
    effective = capped_timeout(remaining, cap)
    return dict(policy=POLICY, session_remaining_seconds=remaining,
                declared_command_cap_seconds=cap, effective_timeout_seconds=effective,
                limiting_budget='session' if remaining <= cap else 'command')
