"""Select the original built-in Codex provider without handling credentials.

Only the explicitly resolved account data directory is shared. Original OpenCode
owns OAuth, refresh and inference. This module does not inspect its auth file,
provide a token, start a relay, choose a model, or implement another model loop.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import hashlib
import math

from packages.domain.store import Conflict
from packages.domain.tasks import digest

PROTOCOL = "NATIVE_CODEX_SUBSCRIPTION"
AUTH_MODE = "OAUTH_SUBSCRIPTION"
ENDPOINT = "https://chatgpt.com/backend-api/codex"


def _structured_terminal(source, selected, by_id):
    proof = source['structured_output']
    keys = {'assistant_id', 'part_id', 'call_id', 'schema_sha256', 'value_sha256', 'part_sha256'}
    if (source['kind'] != 'PLANNING' or not isinstance(proof, dict) or set(proof) != keys
            or len(selected) != 1 or proof['assistant_id'] != selected[0]
            or any(not isinstance(proof[k], str) or not re.fullmatch('[a-f0-9]{64}', proof[k])
                for k in ('schema_sha256', 'value_sha256', 'part_sha256'))):
        raise Conflict('SUBSCRIPTION_STRUCTURED_PROOF_INVALID')
    item = by_id.get(selected[0], {}); info = item.get('info', {})
    parts = [p for p in item.get('parts', []) if p.get('type') == 'tool']
    if len(parts) != 1:
        raise Conflict('SUBSCRIPTION_STRUCTURED_PART_NOT_UNIQUE')
    part = parts[0]; state = part.get('state', {}); timing = state.get('time', {})
    if (info.get('finish') != 'tool-calls' or not isinstance(info.get('structured'), dict)
            or part.get('tool') != 'StructuredOutput' or state.get('status') != 'completed'
            or not isinstance(state.get('metadata'), dict) or state['metadata'].get('valid') is not True
            or state.get('error') is not None or digest(state.get('input')) != proof['value_sha256']
            or part.get('sessionID') != source['original_session_id'] or part.get('messageID') != selected[0]
            or not isinstance(part.get('id'), str) or not re.fullmatch(r'prt_[A-Za-z0-9_-]{1,100}', part['id'])
            or not isinstance(part.get('callID'), str) or not 1 <= len(part['callID']) <= 256
            or proof['part_id'] != part['id'] or proof['call_id'] != part['callID']
            or proof['value_sha256'] != digest(info['structured']) or proof['part_sha256'] != digest(part)
            or any(type(timing.get(k)) not in {int, float} or not math.isfinite(timing[k])
                or timing[k] <= 0 for k in ('start', 'end'))
            or type(info.get('time', {}).get('completed')) not in {int, float}
            or not timing['start'] <= timing['end'] <= info['time']['completed']):
        raise Conflict('SUBSCRIPTION_STRUCTURED_TERMINAL_NOT_OBSERVED')


def subscription_response_evidence(profile, source, messages):
    """Validate an already observed original response; never dispatch or read auth.

    Source is built by the owning adapter from its frozen operation, not accepted
    from a renderer/model. This proves response reachability, not semantic quality,
    subscription tier, remaining usage or future authorization.
    """
    _profile(profile)
    keys={'kind','project_id','object_ref','operation_id','request_sha256','generation',
          'original_session_id','status','idle_observed','cancel_requested',
          'native_user_messages','native_assistant_ids','deadline_ms'}
    if not isinstance(source,dict) or set(source) not in (keys, keys | {'structured_output'}):
        raise Conflict('SUBSCRIPTION_RESPONSE_SOURCE_FIELDS_INVALID')
    if (source.get('status') != 'RETURNED' or source.get('idle_observed') is not True
            or source.get('cancel_requested') is not False):
        return None
    if not re.fullmatch('[a-f0-9]{32}', getattr(profile, 'authorization_epoch', '') or ''):
        return None
    if (source.get('kind') not in {'ORIGINAL_SESSION','NATIVE_DESKTOP','PLANNING'}
            or not _reference(source.get('object_ref'))
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(source.get('project_id','')))
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(source.get('operation_id','')))
            or not re.fullmatch('[a-f0-9]{64}', str(source.get('request_sha256','')))
            or type(source.get('generation')) is not int or not 1 <= source['generation'] < 2**48
            or type(source.get('deadline_ms')) not in {int,float} or not math.isfinite(source['deadline_ms']) or source['deadline_ms']<=0
            or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}', str(source.get('original_session_id','')))):
        raise Conflict('SUBSCRIPTION_RESPONSE_SOURCE_SCOPE_INVALID')
    parents=source.get('native_user_messages');selected=source.get('native_assistant_ids')
    if (not isinstance(parents,dict) or not 1 <= len(parents) <= 32
            or not isinstance(selected,list) or not 1 <= len(selected) <= 200 or len(set(selected)) != len(selected)
            or any(not re.fullmatch(r'msg_[A-Za-z0-9_-]{1,100}', str(key)) or not re.fullmatch('[a-f0-9]{64}', str(value)) for key,value in parents.items())
            or not isinstance(messages,list) or len(messages)>200):
        raise Conflict('SUBSCRIPTION_RESPONSE_MESSAGE_SCOPE_INVALID')
    by_id={item.get('info',{}).get('id'):item for item in messages}
    if len(by_id)!=len(messages):raise Conflict('SUBSCRIPTION_RESPONSE_DUPLICATE_MESSAGES')
    structured = 'structured_output' in source
    if structured: _structured_terminal(source, selected, by_id)
    observed=[]
    for ident in selected:
        item=by_id.get(ident,{});info=item.get('info',{});parent=info.get('parentID')
        user=by_id.get(parent,{});user_info=user.get('info',{})
        text=''.join(part.get('text','') for part in user.get('parts',[]) if part.get('type')=='text')
        completed=info.get('time',{}).get('completed')
        if (parent not in parents or user_info.get('role')!='user'
                or user_info.get('sessionID')!=source['original_session_id']
                or hashlib.sha256(text.encode()).hexdigest()!=parents[parent]
                or info.get('role')!='assistant' or info.get('sessionID')!=source['original_session_id']
                or info.get('providerID')!=profile.provider_id or info.get('modelID')!=profile.model_id
                or info.get('finish') != ('tool-calls' if structured else 'stop') or info.get('error') is not None
                or type(completed) not in {int,float} or not math.isfinite(completed) or completed<=0
                or completed>source['deadline_ms']
                or not (any(part.get('type')=='text' and isinstance(part.get('text'),str) and part['text'].strip() for part in item.get('parts',[]))
                        or isinstance(info.get('structured'),dict))):
            raise Conflict('SUBSCRIPTION_RESPONSE_EXACT_ASSISTANT_NOT_OBSERVED')
        # Store only identifiers/hashes and terminal metadata, never any content.
        observed.append({'id':ident,'parent_id':parent,'completed':completed,'finish':info['finish'],
                         'message_sha256':digest(item)})
    return {'schema':'eeas.subscription-response-observation.v1','account_ref':profile.account_ref,
            'profile_ref':profile.reference,'authorization_epoch':profile.authorization_epoch,
            'provider_id':profile.provider_id,'model_id':profile.model_id,'source':source,
            'assistants':observed,'model_access':'VERIFIED','evidence_kind':'ORIGINAL_MODEL_RESPONSE_OBSERVED',
            'semantic_acceptance':False,'independent_verification':'NOT_RUN',
            'subscription_entitlement':'NOT_VALIDATED','remaining_allowance':'NOT_MEASURED',
            'future_availability':'NOT_GUARANTEED','model_calls_by_observer':0}


def is_subscription(profile):
    return getattr(profile, "auth_mode", None) == AUTH_MODE


def _reference(value):
    return (isinstance(value, dict) and set(value) == {"id", "revision", "sha256"}
            and isinstance(value["id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["id"])
            and type(value["revision"]) is int and value["revision"] >= 1
            and isinstance(value["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", value["sha256"]))


def _profile(profile):
    if (not is_subscription(profile) or getattr(profile, "protocol", None) != PROTOCOL
            or getattr(profile, "provider_id", None) != "openai"
            or getattr(profile, "api_key", None) is not None
            or getattr(profile, "provider_url", ENDPOINT) not in {None, ENDPOINT}
            or not _reference(getattr(profile, "reference", None))
            or not _reference(getattr(profile, "account_ref", None))
            or not isinstance(getattr(profile, "model_id", None), str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", profile.model_id)
            or not isinstance(getattr(profile, "account_data_home", None), (str, os.PathLike))):
        raise Conflict("EXACT_NATIVE_SUBSCRIPTION_PROFILE_REQUIRED")


def subscription_environment(base_env, profile):
    """Return the execution environment; no global environment or auth read."""
    _profile(profile)
    if not isinstance(base_env, dict):
        raise Conflict("PRIVATE_SUBSCRIPTION_EXECUTION_ENVIRONMENT_REQUIRED")
    account = Path(profile.account_data_home)
    if not account.is_absolute() or ".." in account.parts:
        raise Conflict("EXACT_NATIVE_ACCOUNT_DATA_HOME_REQUIRED")
    try:
        for item in [*reversed(account.parents), account]:
            info = item.lstat()
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise Conflict("NATIVE_ACCOUNT_DATA_HOME_LINK_OR_REPARSE_REFUSED")
        account = account.resolve()
        for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            private = Path(base_env[key]).resolve()
            if private == account or private.is_relative_to(account) or account.is_relative_to(private):
                raise Conflict("SUBSCRIPTION_MUST_SHARE_ONLY_ACCOUNT_DATA_HOME")
    except (KeyError, OSError, TypeError, ValueError):
        raise Conflict("EXACT_NATIVE_ACCOUNT_DATA_HOME_REQUIRED") from None
    result = dict(base_env)
    # Defense against ambient API-key fallback. The account service separately
    # binds an original OAuth login; no auth.json content is read here.
    for key in list(result):
        if (key in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION",
                    "OPENAI_PROJECT_ID", "ANTHROPIC_API_KEY", "CHATGPT_ACCESS_TOKEN", "CODEX_ACCESS_TOKEN",
                    "CLAUDE_CODE_OAUTH_TOKEN", "OPENCODE_API_KEY", "EEAS_OPENCODE_API_KEY",
                    "OPENCODE_CONFIG_CONTENT"} or key.startswith("OPENCODE_AUTH_")):
            result.pop(key)
    result["XDG_DATA_HOME"] = str(account)
    # --pure remains on the original process command, preventing external
    # plugins. This flag independently restores the built-in auth plugins.
    result["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "false"
    return result


def native_provider_config(profile, max_output_tokens):
    """Preserve the original OpenAI Responses provider and exact selected model."""
    _profile(profile)
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768:
        raise Conflict("BOUNDED_NATIVE_OUTPUT_REQUEST_REQUIRED")
    return {"provider": {"openai": {"models": {
        profile.model_id: {"limit": {"context": 131072, "output": max_output_tokens}}
    }}}}


def subscription_budget_summary(budget,*,meter=None):
    """Requested limits are not observed RPCs or a native provider hard cap."""
    if not isinstance(budget, dict):
        raise Conflict("EXPLICIT_NATIVE_SUBSCRIPTION_BUDGET_REQUIRED")
    result={"rpc_attempts": None, "rpc_max_calls": budget.get("max_calls"),
            "hard_rpc_cap": "NOT_QUALIFIED", "rpc_cap_enforcement": "NATIVE_DIRECT_UNMETERED",
            "native_steps": budget.get("native_steps", 1),
            "deadline_seconds": budget.get("timeout_seconds"),
            "requested_max_output_tokens": budget.get("max_output_tokens"),
            "output_token_cap": "NOT_QUALIFIED_NATIVE_CODEX_PLUGIN", "provider_receipts": [],
            "commercial_cost": "SUBSCRIPTION_ALLOWANCE_NOT_MEASURED", "silent_paid_fallback": False,
            "account_refresh_concurrency": "NOT_QUALIFIED"}
    if meter is not None:
        try:result.update(meter.summary())
        except (Conflict,OSError,ValueError,TypeError,KeyError):result.update(http_reservations=None,rpc_cap_enforcement='METER_RECORD_UNAVAILABLE',hard_rpc_cap='NOT_QUALIFIED')
    return result
