"""Small fixed scaffold over an OpenAI-compatible chat-completions endpoint.

JSON actions avoid server-specific native tool parsers. This scaffold is an
independent research harness, not an official Kaggle leaderboard submission.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from runner.common import ROOT,append_record
from runner.sandbox import sandbox_for_config,CandidateArtifactError,SandboxCleanupError,immutable_docker_image
from runner.context import tokenize_chat, bounded_feedback
from runner.http_deadline import POLICY as DEADLINE_HTTP, request_json, validate_policy
from runner.command_budget import (POLICY as COMMAND_POLICY, MIN_LAUNCH_SECONDS,
    CommandBudgetExpired, SessionLaunchBudgetExpired,
    receipt as command_receipt, validate_policy as validate_command_policy)


ACTION_CONTRACT = '''
Respond with exactly one JSON object, without Markdown fences:
{"action":"run_command","command":"a shell command"}
or {"action":"submit_patch"} when finished.
Each run_command executes in the persistent /workspace repository.
Command output can be truncated. There are no other tools.
'''


def action_contract(config):
    """Describe the effective model-visible limit for the selected policy."""
    contract = ACTION_CONTRACT
    if config.get('command_budget_policy') == COMMAND_POLICY:
        contract += (f"\nEach command has a {config['max_command_seconds']}-second watchdog, "
                     "or the remaining session time if shorter. Expiry ends this session.\n")
    if config.get('context_policy') == 'vllm-tokenize-v1':
        contract += (f"\nModel-visible stdout and stderr are each limited to "
                     f"{config['tool_feedback_max_bytes']} UTF-8 bytes.\n")
    return contract


def validate_config(config, study=False):
    from evaluator.grade_policy import validate_config as validate_grading
    from runner.outcome_policy import validate_config as validate_outcomes
    required=["endpoint_url","model","model_revision","context_budget_tokens"]
    missing=[key for key in required if not config.get(key)]
    if missing: raise ValueError("configure before model execution: "+", ".join(missing))
    validate_policy(config)
    validate_command_policy(config)
    validate_grading(config)
    validate_outcomes(config)
    if study and config.get("status")!="frozen": raise ValueError("study requires a frozen configuration")
    if config.get('timeout_policy', 'legacy-v0') not in ('legacy-v0', 'session-deadline-v1'):
        raise ValueError('unsupported timeout policy')
    policy = config.get('context_policy', 'utf8-envelope-v0')
    if policy not in ('utf8-envelope-v0', 'vllm-tokenize-v1'):
        raise ValueError('unsupported context policy')
    if policy == 'vllm-tokenize-v1':
        for key in ('context_safety_margin_tokens', 'tool_feedback_max_bytes'):
            if type(config.get(key)) is not int or config[key] <= 0:
                raise ValueError('invalid context policy budget: ' + key)
        if config.get('action_recovery_policy') != 'bounded-rejection-v1':
            raise ValueError('exact context policy requires pinned action recovery')
    suffixes = config.get('allowed_model_control_suffixes', [])
    if not isinstance(suffixes, list) or any(s != '<tool_call|>' for s in suffixes):
        raise ValueError('unsupported model control suffix')
    backend=config.get('sandbox_backend','docker')
    if backend=='docker':
        if not immutable_docker_image(config.get("container_image")): raise ValueError("container image must use an immutable digest")
    elif backend=='chroot-circuit-v0':
        from runner.chroot_sandbox import validate_identity
        validate_identity(config)
    else: raise ValueError('unsupported sandbox backend')
    for key in ["max_turns","max_generated_tokens","max_output_tokens_per_turn","time_limit_seconds","context_budget_tokens"]:
        if type(config.get(key)) is not int or config[key]<=0: raise ValueError("invalid budget: "+key)


def complete(config, messages, max_tokens, seed, timeout):
    payload={"model":config["model"],"messages":messages,"temperature":config["temperature"],"max_tokens":max_tokens,"stream":False}
    if "chat_template_kwargs" in config:
        payload["chat_template_kwargs"]=config["chat_template_kwargs"]
    if config.get("seed_supported"): payload["seed"]=seed
    headers={"Content-Type":"application/json"}
    key=os.environ.get(config.get("api_key_env","FUSE_API_KEY"))
    if key: headers["Authorization"]="Bearer "+key
    if config.get('http_transport') == DEADLINE_HTTP:
        return request_json(config,config["endpoint_url"].rstrip("/")+"/chat/completions",payload,headers,timeout)
    request=urllib.request.Request(config["endpoint_url"].rstrip("/")+"/chat/completions",data=json.dumps(payload).encode(),headers=headers)
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return json.loads(response.read())


def parse_action(content, control_suffixes=()):
    """Accept one JSON action with only explicitly pinned model control suffixes."""
    if not isinstance(content, str):
        raise TypeError('action content must be text')
    text = content.strip()
    for suffix in control_suffixes:
        if not isinstance(suffix, str) or not suffix:
            raise ValueError('invalid model control suffix')
        if text.endswith(suffix):
            text = text[:-len(suffix)].rstrip()
            break
    return json.loads(text)


def is_timeout(exc):
    """urllib can wrap a socket timeout in URLError; HTTP errors are not timeouts."""
    return isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError))


def run_agent(repo,prompt,config,seed,log_path,request_fn=complete,sandbox_factory=None,token_count_fn=tokenize_chat):
    validate_config(config)
    messages=[{"role":"system","content":(ROOT/"prompts/system.md").read_text()+action_contract(config)},
              {"role":"user","content":prompt}]
    exact = config.get('context_policy') == 'vllm-tokenize-v1'
    if exact:
        messages[0]['content'] += '\nKeep command actions small; split large file edits into multiple commands. If an action is rejected or truncated, send a complete smaller JSON action.\n'
    started=time.monotonic()
    deadline=started+config['time_limit_seconds']
    if config.get('http_transport') == DEADLINE_HTTP:
        # Controller-only fields are local to this invocation and never alter
        # the caller's prospective config or enter the candidate workspace.
        from pathlib import Path
        config = dict(config)
        evidence_root = Path(str(log_path) + '.http')
        evidence_root.mkdir(parents=True, exist_ok=False)
        config.update(_http_evidence_root=str(evidence_root.resolve()), _http_session_deadline=deadline)
    deadline_policy=config.get('timeout_policy', 'legacy-v0') == 'session-deadline-v1'
    command_policy=config.get('command_budget_policy') == COMMAND_POLICY
    issued_command_budget=None
    stage='sandbox_setup'
    usage_complete=True
    failed_at=None
    failed_exception=None
    def invoke(operation, *args):
        # Capture the failure before sandbox export/cleanup can consume time.
        nonlocal failed_at,failed_exception
        if not deadline_policy:
            return operation(*args)
        try:
            return operation(*args)
        except Exception as exc:
            failed_at=time.monotonic()
            failed_exception=exc
            raise
    def expired_operation(exc):
        return exc is failed_exception and failed_at is not None and failed_at>=deadline
    generated=input_tokens=turns=calls=0
    status="turn_budget_exhausted"
    error=None
    try:
        with (sandbox_factory(repo,config.get("container_image"),config.get("container_user","1000:1000"))
              if sandbox_factory else sandbox_for_config(repo,config)) as sandbox:
            stage='sandbox_readiness'
            if command_policy and deadline-time.monotonic() < MIN_LAUNCH_SECONDS:
                raise SessionLaunchBudgetExpired('session allowance below launcher minimum')
            readiness=invoke(sandbox.run,"python --version && git --version && test -w /workspace",
                                  min(30,max(.001,deadline-time.monotonic())) if deadline_policy else 30)
            if readiness["returncode"]!=0:
                raise ValueError("sandbox requires Python, Git, and writable /workspace")
            for turn in range(config["max_turns"]):
                remaining=config["time_limit_seconds"]-(time.monotonic()-started)
                if remaining<=0: status="time_budget_exhausted";break
                budget=config["max_generated_tokens"]-generated
                if budget<=0: status="token_budget_exhausted";break
                max_tokens=min(budget,config["max_output_tokens_per_turn"])
                if exact:
                    stage='tokenize'
                    measured = invoke(token_count_fn,config, messages, remaining)
                    if deadline_policy and time.monotonic()>=deadline:
                        status='time_budget_exhausted';break
                    ceiling = min(config['context_budget_tokens'], measured['max_model_len'])
                    available = ceiling - measured['count'] - config['context_safety_margin_tokens']
                    max_tokens = min(max_tokens, available)
                    append_record(log_path, {'event':'context_preflight', 'turn':turn,
                        'policy':'vllm-tokenize-v1', 'prompt_tokens':measured['count'],
                        'model_limit':measured['max_model_len'], 'max_tokens':max_tokens})
                    if max_tokens <= 0:
                        status='context_budget_exhausted';break
                    remaining=config['time_limit_seconds']-(time.monotonic()-started)
                    if remaining <= 0: status='time_budget_exhausted';break
                else:
                    # Retain the original policy for historical configurations.
                    envelope=len(json.dumps(messages,ensure_ascii=False).encode())+1024
                    if envelope+max_tokens>config['context_budget_tokens']:
                        status='context_budget_exhausted';break
                stage='completion'
                usage_complete=False
                result=invoke(request_fn,config,messages,max_tokens,seed,remaining)
                turns+=1
                usage=result.get("usage",{})
                if not all(type(usage.get(k)) is int and usage[k]>=0 for k in ["prompt_tokens","completion_tokens"]):
                    raise ValueError("endpoint must return measured token usage")
                input_tokens+=usage["prompt_tokens"]
                generated+=usage["completion_tokens"]
                usage_complete=True
                append_record(log_path,{"event":"model_response","turn":turn,"response":result})
                if deadline_policy and time.monotonic()>=deadline:
                    status='time_budget_exhausted';break
                content=result['choices'][0]['message']['content']
                if exact and usage['prompt_tokens'] != measured['count']:
                    append_record(log_path, {'event':'token_count_mismatch', 'turn':turn,
                        'tokenize_count':measured['count'], 'usage_count':usage['prompt_tokens']})
                    status='token_accounting_mismatch';break
                if (generated>config["max_generated_tokens"] or usage["prompt_tokens"]+usage["completion_tokens"]>config["context_budget_tokens"]
                        or config.get('outcome_policy')=='submitted-trajectory-v1' and usage['completion_tokens']>max_tokens):
                    status="provider_budget_violation";break
                if exact and result['choices'][0].get('finish_reason') == 'length':
                    append_record(log_path, {'event':'action_rejected','turn':turn,'reason':'output_length'})
                    # Never execute a truncated action, even if its prefix is valid JSON.
                    messages.append({'role':'assistant','content':'[Response truncated; no action executed.]'})
                    messages.append({'role':'user','content':'Output limit reached. Return a complete smaller JSON action; split large edits into short commands.'})
                    continue
                if not exact:
                    messages.append({"role":"assistant","content":content})
                try:
                    action=parse_action(content,config.get('allowed_model_control_suffixes',[]))
                    if not isinstance(action,dict): raise ValueError("action must be an object")
                    if action=={"action":"submit_patch"}:
                        status="submitted";break
                    if set(action)!={"action","command"} or action["action"]!="run_command" or not isinstance(action["command"],str):
                        raise ValueError("invalid action schema")
                except (ValueError,TypeError):
                    if exact:
                        append_record(log_path, {'event':'action_rejected','turn':turn,'reason':'invalid_schema'})
                        messages.append({'role':'assistant','content':'[Invalid action rejected; no action executed.]'})
                    messages.append({"role":"user","content":"Invalid action. Return exactly one JSON object in the documented schema."})
                    continue
                if exact:
                    messages.append({'role':'assistant','content':content})
                remaining=config["time_limit_seconds"]-(time.monotonic()-started)
                if remaining<=0: status="time_budget_exhausted";break
                if command_policy and remaining < MIN_LAUNCH_SECONDS:
                    status='time_budget_exhausted';break
                calls+=1
                stage='command'
                timeout=remaining
                if command_policy:
                    issued_command_budget=command_receipt(remaining,config['max_command_seconds'])
                    timeout=issued_command_budget['effective_timeout_seconds']
                    append_record(log_path,dict(event='command_budget',turn=turn,**issued_command_budget))
                try:
                    output=invoke(sandbox.run,action["command"],timeout)
                except CommandBudgetExpired as exc:
                    if (command_policy and exc.timeout == timeout
                            and exc.backend == config.get('sandbox_backend','docker')):
                        # A recorded watchdog outcome, not a fabricated exit code.
                        captured=exc.captured_result or {}
                        append_record(log_path,dict(event='tool_call',turn=turn,command=action['command'],
                            output=dict(returncode=None,stdout=captured.get('stdout'),stderr=captured.get('stderr'),
                                truncated=captured.get('truncated'),timed_out=True,
                                output_capture_available=exc.captured_result is not None,
                                output_capture_complete=captured.get('output_capture_complete'),
                                outcome='command_watchdog_expired',backend=exc.backend,
                                **issued_command_budget)))
                    raise
                append_record(log_path,{"event":"tool_call","turn":turn,"command":action["command"],"output":output})
                feedback = bounded_feedback(output,config['tool_feedback_max_bytes']) if exact else output
                messages.append({"role":"user","content":json.dumps({"tool_result":feedback})})
    except SandboxCleanupError:
        # A controller cannot safely freeze a bind-mounted workspace while its
        # container may still be running. Retain the original response prefix,
        # record the unresolved cleanup and stop before any phase export.
        append_record(log_path,{'event':'sandbox_cleanup_unresolved','stage':stage,
                                'error_class':'SandboxCleanupError'})
        raise
    except SessionLaunchBudgetExpired as exc:
        status=('time_budget_exhausted' if command_policy and stage=='sandbox_readiness'
                else 'infrastructure_error')
        error=type(exc).__name__
    except CommandBudgetExpired as exc:
        if (command_policy and stage=='command' and issued_command_budget is not None
                and exc.timeout == issued_command_budget['effective_timeout_seconds']
                and exc.backend == config.get('sandbox_backend','docker')):
            status=('time_budget_exhausted' if issued_command_budget['limiting_budget']=='session'
                    else 'command_budget_exhausted')
            error=type(exc).__name__
        elif deadline_policy:
            status='time_budget_exhausted' if expired_operation(exc) else 'infrastructure_error'
            error=type(exc).__name__
        else:
            status='time_budget_exhausted'
    except subprocess.TimeoutExpired as exc:
        if deadline_policy:
            status='time_budget_exhausted' if expired_operation(exc) else 'infrastructure_error'
            error=type(exc).__name__
        else:
            status="time_budget_exhausted"
    except CandidateArtifactError:
        status="invalid_artifact";error="CandidateArtifactError"
    except (urllib.error.URLError,OSError,ValueError,KeyError,IndexError,TypeError,subprocess.CalledProcessError) as exc:
        # Keep diagnostic class without logging secrets from HTTP headers.
        status="infrastructure_error";error=type(exc).__name__
        if deadline_policy and is_timeout(exc) and expired_operation(exc):
            status='time_budget_exhausted'
    if deadline_policy and (status=='time_budget_exhausted' or error):
        append_record(log_path, {'event':'session_stop', 'policy':'session-deadline-v1',
            'stage':stage, 'status':status, 'error_class':error,
            'deadline_elapsed':(failed_at if failed_at is not None else time.monotonic())>=deadline,
            'usage_complete':usage_complete})
    unknown=status=="infrastructure_error" or (deadline_policy and not usage_complete)
    integrity_unresolved=(config.get('outcome_policy')=='submitted-trajectory-v1'
                          and status in ('token_accounting_mismatch','provider_budget_violation'))
    unknown=unknown or integrity_unresolved
    metrics={"status":status,"error_class":error,"tokens":None if unknown else generated,
             "input_tokens":None if unknown else input_tokens,
             "tool_calls":calls,"turns":turns,"wall_seconds":time.monotonic()-started,
             "requested_seed":seed,"seed_sent":bool(config.get("seed_supported")),
             "model":config["model"],"model_revision":config["model_revision"]}
    if deadline_policy:
        metrics.update(timeout_policy='session-deadline-v1', usage_complete=usage_complete,
                       observed_completion_tokens=generated, observed_prompt_tokens=input_tokens)
    if command_policy:
        metrics.update(command_budget_policy=COMMAND_POLICY,
                       max_command_seconds=config['max_command_seconds'])
    if config.get('outcome_policy')=='submitted-trajectory-v1':
        metrics.update(outcome_policy='submitted-trajectory-v1',
            usage_integrity='unresolved' if integrity_unresolved else 'not_flagged',
            observed_completion_tokens=generated,observed_prompt_tokens=input_tokens)
    append_record(log_path,{"event":"session_end","metrics":metrics})
    return metrics
