"""Pure protocol builder/classifier; no process, native, network or file I/O."""
import base64
import hashlib
import json
import ntpath
import re

PROCESS = 'NativeSitusControl:ExecuteRequest'
MAX_REQUEST_BYTES = 65536
ACTIONS = frozenset(('read_settings', 'set_settings'))
FIELDS = frozenset(('schema_version', 'request_id', 'action', 'pcb_path', 'pid',
                    'creation_filetime', 'exe', 'payload'))


def _path(value):
    if (not isinstance(value, str) or len(value) > 1024
            or not re.match(r'^[A-Za-z]:\\', value)
            or any(c in value for c in '\0\r\n|"<>') or ':' in value[2:]):
        raise ValueError('Absolute local Windows path required')
    return ntpath.normpath(value)


def validate_request(request):
    if not isinstance(request, dict) or set(request) != FIELDS:
        raise ValueError('Complete fixed request envelope required')
    if type(request['schema_version']) is not int or request['schema_version'] != 1:
        raise ValueError('schema_version must be 1')
    if not isinstance(request['request_id'], str) or not re.fullmatch('[0-9a-f]{32}', request['request_id']):
        raise ValueError('request_id must be 32 lowercase hexadecimal characters')
    if not isinstance(request['action'], str) or request['action'] not in ACTIONS:
        raise ValueError('Unsupported fixed action')
    if type(request['pid']) is not int or not 0 < request['pid'] <= 2147483647:
        raise ValueError('Positive native PID required')
    created = request['creation_filetime']
    if not isinstance(created, str) or not re.fullmatch('[0-9]+', created) or not 0 < int(created) <= 9223372036854775807:
        raise ValueError('Positive decimal FILETIME string required')
    if ntpath.splitext(_path(request['pcb_path']))[1].lower() != '.pcbdoc':
        raise ValueError('Exact PCB path required')
    for key in ('pcb_path', 'exe'):
        if _path(request[key]) != request[key]:
            raise ValueError('Canonical envelope paths required')
    if not isinstance(request['payload'], dict):
        raise ValueError('Payload must be an object')
    return request


def build_request(request, request_path):
    """Return immutable bytes and ONE argv argument; caller owns CreateNew I/O.

    The public backend must pin exact PID/creation/exe and singleton, persist
    its dispatch intent, launch once without shell, and never retry on timeout.
    """
    validate_request(request)
    path = _path(request_path)
    if ntpath.basename(path) != 'request.json' or ntpath.basename(ntpath.dirname(path)) != request['request_id']:
        raise ValueError('Request path must end in request_id/request.json')
    try:
        raw = json.dumps(request, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':'), sort_keys=True).encode('utf-8')
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('Payload must contain finite JSON data') from error
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('Request exceeds bounded size')
    digest = hashlib.sha256(raw).hexdigest()
    token = base64.urlsafe_b64encode(path.encode('utf-8')).decode('ascii').rstrip('=')
    if len(token) > 2048:
        raise ValueError('Encoded path exceeds native parameter bound')
    argument = '-R' + PROCESS + '(Request=' + token + '|Sha256=' + digest + ')'
    return dict(request_bytes=raw, request_sha256=digest, request_path=path,
                argument=argument, response_path=ntpath.join(ntpath.dirname(path), 'response.json'),
                attempt_path=ntpath.join(ntpath.dirname(path), 'attempt.json'))


def classify_response(request, request_sha256, response, launcher_exit_code=None):
    """Only a complete correlated native receipt proves handler completion.

    Launcher exit 0 alone is never completion. This classifier authorizes no
    replay, even for known refusal. Nested action status is preserved separately.
    """
    validate_request(request)
    unknown = dict(completed=False, outcome_unknown=True, retry_allowed=False)
    if not isinstance(response, dict):
        return dict(unknown, reason='Missing or incomplete native response')
    expected = {key: request[key] for key in ('request_id', 'action', 'pid', 'creation_filetime')}
    expected.update(schema_version=1, request_sha256=request_sha256)
    if (not isinstance(request_sha256, str) or not re.fullmatch('[0-9a-f]{64}', request_sha256)
            or any(type(response.get(k)) is not type(v) or response.get(k) != v for k,v in expected.items())
            or ntpath.normcase(str(response.get('pcb_path', ''))) != ntpath.normcase(request['pcb_path'])
            or ntpath.normcase(str(response.get('exe', ''))) != ntpath.normcase(request['exe'])):
        return dict(unknown, reason='Native response identity/hash mismatch')
    if response.get('retry_allowed') is not False or response.get('document_saved') is not False:
        return dict(unknown, reason='Native response contract mismatch')
    if response.get('success') is True and response.get('native_handler_completed') is True and response.get('outcome_unknown') is False:
        data = response.get('data')
        if (response.get('operation_invoked') is not True or not isinstance(data, dict)
                or type(data.get('success')) is not bool or type(data.get('outcome_unknown')) is not bool):
            return dict(unknown, reason='Incomplete action result')
        action_unknown = isinstance(data, dict) and data.get('outcome_unknown') is True
        action_success = data.get('success') if isinstance(data, dict) else None
        return dict(completed=True, outcome_unknown=action_unknown, retry_allowed=False,
                    action_success=action_success, native_response=response,
                    launcher_exit_code=launcher_exit_code)
    if (response.get('success') is False and response.get('operation_invoked') is False
            and response.get('outcome_unknown') is False):
        return dict(completed=True, outcome_unknown=False, retry_allowed=False,
                    action_success=False, native_response=response)
    return dict(unknown, reason='Native action outcome is unresolved', native_response=response)
