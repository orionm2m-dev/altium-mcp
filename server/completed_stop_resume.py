"""Resume a recorded successful Stop with one fresh probe; never repeat Stop."""
import hashlib
import json
import math
from pathlib import Path
import re

import altium_focus
import emergency_stop
import script_recovery
from pending_stop_wizard_resume import _ready_main


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _stop_exit(stop_pid):
    for process in script_recovery._launches.values():
        if process.poll() is None:
            raise ValueError("A tracked recovery launcher is still running")
    owned = script_recovery._launches.get(stop_pid)
    if owned is not None:
        if owned.poll() != 0:
            raise ValueError("The tracked Stop launcher did not exit successfully")
        return {"source": "owned_popen", "exit_code": 0, "currently_running": False}
    try:
        process = emergency_stop.WinProcess(stop_pid)
    except OSError as error:
        if getattr(error, "winerror", None) != 87:
            raise
        return {"source": "pid_absent_at_open", "currently_running": False,
                "historical_creation_verified": False}
    with process:
        observed = process.exit_observation()
    if (observed.get("pid") != stop_pid or type(observed.get("exit_code")) is not int
            or observed["exit_code"] != 0
            or type(observed.get("creation_filetime")) is not int or observed["creation_filetime"] <= 0):
        raise ValueError("Recorded Stop PID is present/live or successful exit is unconfirmed")
    return {"source": "query_handle_exited", "exit_code": 0, "currently_running": False,
            "historical_creation_verified": False}


def _receipts(exe, exchange, target_pid, recovery_token):
    latch_path = Path(exchange) / "script_recovery_state.json"
    startup_path = Path(exchange) / "altium_startup.json"
    raw, startup_raw = latch_path.read_bytes(), startup_path.read_bytes()
    state, startup = json.loads(raw), json.loads(startup_raw)
    if not isinstance(state, dict) or not isinstance(startup, dict):
        raise ValueError("Recovery and startup receipts must be objects")
    launches = state.get("launches", [])
    if not isinstance(launches, list) or any(not isinstance(item, dict) for item in launches):
        raise ValueError("Recovery launch records must be an array of objects")
    if (state.get("token") != recovery_token or state.get("phase") != "blocked_after_stop"
            or state.get("manual_stop_confirmed") is not False
            or state.get("fresh_instance_confirmed") is not False
            or state.get("require_fresh_instance") or state.get("probe_result_path")
            or len(launches) != 1 or launches[0].get("phase") != "stop"
            or type(launches[0].get("exit_code")) is not int or launches[0]["exit_code"] != 0
            or type(launches[0].get("pid")) is not int or launches[0]["pid"] <= 0
            or launches[0]["pid"] == target_pid):
        raise ValueError("Exact blocked-after-Stop checkpoint with one recorded successful Stop is required")
    identity = startup.get("identity")
    if (not isinstance(identity, dict) or startup.get("success") is not True
            or startup.get("launched") is not True or startup.get("script_dispatched") is not False
            or startup.get("mutation_replayed") is not False or startup.get("exit_code") is not None
            or startup.get("status") != "process_started_ui_unverified"
            or type(startup.get("target_pid")) is not int or startup["target_pid"] != target_pid
            or type(identity.get("pid")) is not int or identity["pid"] != target_pid
            or type(identity.get("creation_filetime")) is not int or identity["creation_filetime"] <= 0
            or identity.get("running") is not True
            or startup.get("creation_filetime_decimal") != str(identity["creation_filetime"])
            or emergency_stop._canonical(startup.get("configured_exe", "")) != emergency_stop._canonical(exe)
            or identity.get("exe") != emergency_stop._canonical(exe)
            or identity.get("user_sid") != emergency_stop._current_sid()):
        raise ValueError("A complete successful configured normal-start identity is required")
    started = startup.get("started_at_unix")
    created = (identity["creation_filetime"] - 116444736000000000) / 10000000
    if (type(started) not in (int, float) or not math.isfinite(started)
            or not started <= created <= latch_path.stat().st_mtime
            or startup_path.stat().st_mtime_ns > latch_path.stat().st_mtime_ns
            or not _ready_main(state.get("environment", {}), identity)):
        raise ValueError("Stop checkpoint is not demonstrably after this ready normal-start session")
    if latch_path.read_bytes() != raw or startup_path.read_bytes() != startup_raw:
        raise ValueError("Recovery or startup receipt changed during validation")
    return raw, startup_raw, state, identity, launches[0]["pid"]


class _VerifiedCompletedStop:
    """Private one-use capability; public arguments cannot provide this proof."""
    def __init__(self, exe, exchange, raw, startup_raw, main, identity, stop_pid, window):
        self.exe, self.exchange = exe, Path(exchange)
        self.raw, self.startup_raw = raw, startup_raw
        self.main, self.identity, self.stop_pid = main, identity, stop_pid
        self.window = dict(window)
        self.consumed = False

    def _check_main(self, exe, require_foreground):
        current = self.main.identity()
        emergency_stop._valid_identity(current, exe, self.identity["pid"])
        if not emergency_stop._same_identity(current, self.identity):
            raise ValueError("Pinned main identity changed")
        inventory = altium_focus.altium_lifecycle.configured_instances(exe)
        if inventory["uncertain"] or [row["pid"] for row in inventory["configured"]] != [current["pid"]]:
            raise ValueError("Configured process inventory changed after focus")
        api = altium_focus.WindowAPI()
        window = altium_focus._select_main(api, current["pid"])
        if (window["hwnd"] != self.window["hwnd"] or window["thread_id"] != self.window["thread_id"]
                or (require_foreground and api.foreground() != window["hwnd"])):
            raise ValueError("Exact focused main window changed or is no longer foreground")
        after = self.main.identity()
        emergency_stop._valid_identity(after, exe, current["pid"])
        if not emergency_stop._same_identity(after, current):
            raise ValueError("Pinned main identity changed during window check")
        return current

    def validate_after_probe(self, exe, exchange, state):
        # A matching token certifies only this same live session. Foreground
        # may naturally move during dispatch; do not confuse focus with identity.
        self._check_main(exe, False)
        if ((self.exchange / "altium_startup.json").read_bytes() != self.startup_raw
                or Path(exchange).resolve() != self.exchange.resolve()
                or json.loads((self.exchange / "script_recovery_state.json").read_bytes()) != json.loads(json.dumps(state))):
            raise ValueError("Recovery state or startup receipt changed during the probe")

    def consume(self, exe, exchange, old, environment):
        if self.consumed:
            raise ValueError("Completed Stop proof was already consumed")
        self.consumed = True
        if (emergency_stop._canonical(exe) != emergency_stop._canonical(self.exe)
                or Path(exchange).resolve() != self.exchange.resolve()
                or (self.exchange / "script_recovery_state.json").read_bytes() != self.raw
                or (self.exchange / "altium_startup.json").read_bytes() != self.startup_raw
                or old != json.loads(self.raw)):
            raise ValueError("Completed Stop or startup receipt changed")
        current = self.main.identity()
        emergency_stop._valid_identity(current, exe, self.identity["pid"])
        if not emergency_stop._same_identity(current, self.identity) or not _ready_main(environment, current):
            raise ValueError("The recorded main session is no longer ready")
        observation = _stop_exit(self.stop_pid)
        current = self._check_main(exe, True)
        if ((self.exchange / "script_recovery_state.json").read_bytes() != self.raw
                or (self.exchange / "altium_startup.json").read_bytes() != self.startup_raw):
            raise ValueError("Recovery receipts changed before probe authorization")
        return {"kind": "recorded_successful_stop_exit", "stop_pid": self.stop_pid,
            "exit_code": 0, "historical_stop_creation_verified": False,
            "historical_main_creation_verified_in_stop_receipt": False,
            "current_launcher_observation": observation,
            "main_identity": current, "normal_start_identity_verified": True,
            "prior_latch_token": old["token"], "prior_latch_sha256": _hash(self.raw),
            "startup_sha256": _hash(self.startup_raw), "manual_stop_confirmed": False,
            "stop_redispatched": False, "stop_delivery_verified": False}


async def resume_locked(exe, exchange, target_pid, recovery_token,
                        timeout_seconds=30, dispatch_timeout_seconds=8):
    """Caller holds bridge lock and OS claim across focus, proof and probe."""
    result = {"recovered": False, "blocked": True, "stop_redispatched": False,
              "manual_stop_confirmed": False, "document_saved": False}
    try:
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError("target_pid must be a positive integer")
        if not isinstance(recovery_token, str) or re.fullmatch(r"[0-9a-f]{32}", recovery_token) is None:
            raise ValueError("recovery_token must identify the exact 32-character checkpoint")
        if (type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60
                or type(dispatch_timeout_seconds) is not int or not 1 <= dispatch_timeout_seconds <= 15):
            raise ValueError("Invalid bounded probe timeout")
        configured = Path(exe)
        if not configured.is_absolute() or not configured.is_file() or configured.suffix.casefold() != ".exe":
            raise ValueError("Configured Altium executable must be an absolute existing .exe")
        raw, startup_raw, state, identity, stop_pid = _receipts(exe, exchange, target_pid, recovery_token)
        with emergency_stop.WinProcess(target_pid) as main:
            current = main.identity()
            emergency_stop._valid_identity(current, exe, target_pid)
            if not emergency_stop._same_identity(current, identity):
                raise ValueError("Current main does not match the recorded normal-start identity")
            result["stop_observation"] = _stop_exit(stop_pid)
            # This bounded call is synchronous: request cancellation cannot leave
            # a focus worker running after either serialization lock is released.
            result["focus"] = altium_focus._focus_locked(exe, target_pid)
            if result["focus"].get("success") is not True:
                raise ValueError("The exact main window could not be restored to foreground")
            proof = _VerifiedCompletedStop(exe, exchange, raw, startup_raw, main, identity, stop_pid,
                result["focus"]["window_after"])
            recovery = await script_recovery._recover_emergency_serialized(exe, exchange,
                timeout_seconds, dispatch_timeout_seconds, False, verified_stop=proof)
            result.update(recovery)
            return result
    except (OSError, ValueError, TypeError, KeyError) as error:
        return dict(result, error=str(error))
