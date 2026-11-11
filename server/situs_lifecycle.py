"""Durable, bounded orchestration for typed Situs jobs.

Native access is supplied by the registered server adapter. The adapter must
provide exact-session observations and a separate control channel while a
blocking start is active. This module never launches or controls Altium itself.
"""
import asyncio
from contextlib import contextmanager
import copy
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import time
import uuid

SCOPE_ACTIONS = {
    "all": "Start", "net": "Net", "net_class": "NetClass",
    "component": "Component", "component_class": "ComponentClass",
    "connection": "Connection", "area": "Area", "room": "Room",
    "selected_components": "OnSelectedComponents",
    "between_selected_components": "BetweenSelectedComponents",
}
CONTROL_ACTIONS = {"stop": "Stop", "pause": "Pause", "resume": "Pause", "reset": "Reset"}
TERMINAL_STATES = {"completed", "stopped", "reset"}
VERIFIED_STATES = {"idle", "running", "paused", *TERMINAL_STATES}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\0|"):
        raise ValueError(f"{label} must be nonempty single-line text without pipe characters")
    return value


def _path(value):
    value = _text(value, "pcb_path")
    path = PureWindowsPath(value)
    if not path.is_absolute() or path.suffix.casefold() != ".pcbdoc" or ".." in path.parts:
        raise ValueError("pcb_path must be an absolute PcbDoc path without parent traversal")
    return str(path)


def normalize_binding(binding):
    if not isinstance(binding, dict) or set(binding) != {"pid", "creation_time", "exe_path", "pcb_path"}:
        raise ValueError("binding requires exactly pid, creation_time, exe_path and pcb_path")
    if type(binding["pid"]) is not int or binding["pid"] <= 0:
        raise ValueError("binding.pid must be a positive integer")
    creation = binding["creation_time"]
    if type(creation) is not int or creation <= 0:
        raise ValueError("binding.creation_time must be a positive process creation timestamp")
    exe = PureWindowsPath(_text(binding["exe_path"], "exe_path"))
    if not exe.is_absolute() or exe.name.casefold() != "x2.exe" or ".." in exe.parts:
        raise ValueError("binding.exe_path must identify the absolute Altium X2.EXE path")
    return dict(pid=binding["pid"], creation_time=creation,
                exe_path=str(exe).casefold(), pcb_path=_path(binding["pcb_path"]).casefold())


def validate_options(options):
    """Validate request types; the adapter must independently apply/read them back."""
    options = {} if options is None else copy.deepcopy(options)
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    allowed = {"strategy_name", "passes", "rip_up_violations", "orthogonal", "via_cost"}
    if set(options) - allowed:
        raise ValueError("unknown Situs option: " + ", ".join(sorted(set(options) - allowed)))
    if "strategy_name" in options:
        _text(options["strategy_name"], "strategy_name")
    if "passes" in options:
        passes = options["passes"]
        if not isinstance(passes, list) or not passes or len(passes) > 64:
            raise ValueError("passes must contain 1 to 64 ordered names")
        for name in passes:
            _text(name, "pass name")
    for name in ("rip_up_violations", "orthogonal"):
        if name in options and type(options[name]) is not bool:
            raise ValueError(f"{name} must be boolean")
    if "via_cost" in options:
        cost = options["via_cost"]
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise ValueError("via_cost must be a finite nonnegative number; native range still requires validation")
    return options


def build_start_request(binding, expected_sha256, scope="all", targets=None, options=None):
    binding = normalize_binding(binding)
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected_sha256):
        raise ValueError("expected_sha256 must be the current saved PCB SHA-256")
    if scope not in SCOPE_ACTIONS:
        raise ValueError("unknown routing scope")
    targets = [] if targets is None else copy.deepcopy(targets)
    if scope == "all":
        if not isinstance(targets, list) or targets:
            raise ValueError("all scope must use an empty target list")
    elif scope == "area":
        if (not isinstance(targets, list) or len(targets) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in targets) or
                targets[0] >= targets[2] or targets[1] >= targets[3]):
            raise ValueError("area targets must be finite [xmin, ymin, xmax, ymax] with positive area")
    else:
        if not isinstance(targets, list) or not targets or len(targets) > 10000:
            raise ValueError("scope requires a nonempty explicit target list")
        for target in targets:
            _text(target, "target")
        if len(set(targets)) != len(targets):
            raise ValueError("target identities must be unique")
    options = validate_options(options)
    return dict(binding=binding, expected_sha256=expected_sha256.lower(), scope=scope,
                targets=targets, options=options, settings_digest=digest(options),
                target_digest=digest(dict(scope=scope, targets=targets)),
                process="PCB:Autoroute", parameters={"Action": SCOPE_ACTIONS[scope]},
                requires_target_binding=scope != "all", no_save=True, runtime_qualified=False)


class SitusJobManager:
    """Persist before dispatch; never replay a pending/unknown operation.

    Adapter contract:
      await observe(job) -> exact binding, observation_id, observed_at (epoch),
        state_verified, router_state, modal_blocked, progress (optional).
      await dispatch(job, action, operation_id) -> acknowledgement only.
    Initial observation must also prove saved_sha256, settings_digest,
    target_digest, global_rules_verified, rules_digest and both process depths.
    A terminal observation must carry the matching start operation_id and
    router_returned=True; an idle process alone cannot complete a job.
    """
    def __init__(self, directory, adapter, observation_timeout=5.0, freshness_seconds=10.0):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.adapter = adapter
        self.observation_timeout = observation_timeout
        self.freshness_seconds = freshness_seconds
        self._tasks = set()
        self._lock = asyncio.Lock()

    def _path(self, job_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("invalid Situs job id")
        return self.directory / (job_id + ".json")

    def _read(self, job_id):
        value = json.loads(self._path(job_id).read_text("utf-8"))
        if value.get("job_id") != job_id or value.get("schema_version") != 1:
            raise ValueError("invalid stored Situs job")
        return value

    def _write(self, job):
        path = self._path(job["job_id"])
        temporary = path.with_suffix(".pending")
        temporary.write_text(json.dumps(job, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    @contextmanager
    def _exclusive(self):
        lease = self.directory / "dispatch.lock"
        with lease.open("x", encoding="utf-8") as stream:
            stream.write("Situs job-store write in progress; do not remove while owner is active.\n")
        try:
            yield
        finally:
            lease.unlink()

    async def _observe(self, job):
        observed = await asyncio.wait_for(self.adapter.observe(copy.deepcopy(job)), self.observation_timeout)
        if not isinstance(observed, dict) or observed.get("state_verified") is not True:
            raise ValueError("router state is not verified")
        if normalize_binding(observed.get("binding")) != job["request"]["binding"]:
            raise ValueError("process or exact PCB binding changed")
        _text(observed.get("observation_id"), "observation_id")
        when = observed.get("observed_at")
        if (isinstance(when, bool) or not isinstance(when, (int, float)) or
                not math.isfinite(when) or not -1 <= time.time() - when <= self.freshness_seconds):
            raise ValueError("stale or invalid router observation")
        if observed.get("router_state") not in VERIFIED_STATES:
            raise ValueError("unknown router state")
        if type(observed.get("modal_blocked")) is not bool:
            raise ValueError("observation lacks modal state")
        progress = observed.get("progress")
        if progress is not None:
            if not isinstance(progress, dict):
                raise ValueError("progress must be an object")
            for key in ("routed", "total"):
                if type(progress.get(key)) is not int or progress[key] < 0:
                    raise ValueError("progress routed/total must be nonnegative integers")
            if progress["routed"] > progress["total"]:
                raise ValueError("routed progress exceeds total")
        if job.get("start_operation_id") and (observed.get("job_id") != job["job_id"] or
                observed.get("start_operation_id") != job["start_operation_id"]):
            raise ValueError("observation is not correlated to this job and start operation")
        return observed

    @staticmethod
    def _reconcile(job, observed):
        state = observed["router_state"]
        if job["state"] in TERMINAL_STATES and state != job["state"]:
            # Reset is the only accepted transition after a terminal result.
            if not (state == "reset" and any(op["action"] == "reset" for op in job["operations"])):
                raise ValueError("terminal job cannot inherit another routing run")
        if state in TERMINAL_STATES and observed.get("router_returned") is not True:
            raise ValueError("terminal state lacks matching router return evidence")
        expected = {"pause": "paused", "resume": "running", "stop": "stopped", "reset": "reset"}
        for operation in job["operations"]:
            if operation["action"] in expected and not operation.get("transition_verified"):
                if (observed.get("control_operation_id") == operation["id"] and
                        state == expected[operation["action"]]):
                    operation["transition_verified"] = True
                    operation["transition_observation_id"] = observed["observation_id"]
        unresolved = any(op["action"] != "start" and not op.get("transition_verified")
                         for op in job["operations"])
        if state != "idle" and (not unresolved or state in TERMINAL_STATES):
            job["state"] = state
        job["last_observation"] = observed

    def _schedule(self, job, action, operation_id):
        task = asyncio.create_task(self._dispatch(job["job_id"], action, operation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, job_id, action, operation_id):
        try:
            acknowledgement = await self.adapter.dispatch(copy.deepcopy(self._read(job_id)), action, operation_id)
            if not isinstance(acknowledgement, dict):
                raise ValueError("adapter acknowledgement must be an object")
            digest(acknowledgement)  # Reject non-JSON/NaN data before persistence.
            async with self._lock:
                with self._exclusive():
                    job = self._read(job_id)
                    operation = next(op for op in job["operations"] if op["id"] == operation_id)
                    operation["acknowledgement"] = acknowledgement
                    operation["dispatch_returned"] = True
                    self._write(job)
        except BaseException as error:
            async with self._lock:
                with self._exclusive():
                    job = self._read(job_id)
                    operation = next(op for op in job["operations"] if op["id"] == operation_id)
                    operation["error"] = str(error) or type(error).__name__
                    operation["outcome_unknown"] = True
                    if job["state"] not in TERMINAL_STATES:
                        job["state"] = "outcome_unknown"
                    self._write(job)
            if isinstance(error, asyncio.CancelledError):
                raise

    async def start(self, request):
        # Revalidate the typed request; caller-supplied process/parameters are
        # never passed through as arbitrary executable command data.
        canonical = build_start_request(request["binding"], request["expected_sha256"],
                                        request["scope"], request["targets"], request["options"])
        job_id = uuid.uuid4().hex
        job = dict(schema_version=1, job_id=job_id, request=canonical, state="preparing",
                   created_at=time.time(), operations=[], no_save=True, runtime_qualified=False)
        observed = await self._observe(job)
        if observed["router_state"] != "idle" or observed["modal_blocked"]:
            raise ValueError("start requires verified idle router and no blocking modal")
        for name in ("client_depth", "pcb_depth"):
            if type(observed.get(name)) is not int or observed[name] != 0:
                raise ValueError("start requires both native process depths zero")
        for name, expected in (("saved_sha256", canonical["expected_sha256"]),
                               ("settings_digest", canonical["settings_digest"]),
                               ("target_digest", canonical["target_digest"])):
            if observed.get(name) != expected:
                raise ValueError(f"pre-start {name} readback differs")
        if observed.get("global_rules_verified") is not True:
            raise ValueError("global routing rules have not been verified")
        _text(observed.get("rules_digest"), "rules_digest")
        async with self._lock:
            with self._exclusive():
                for path in self.directory.glob("*.json"):
                    existing = self._read(path.stem)
                    if (existing["request"]["binding"] == canonical["binding"] and
                            existing["state"] not in TERMINAL_STATES):
                        raise ValueError("an unresolved Situs job already owns this PCB/session")
                operation_id = uuid.uuid4().hex
                job.update(state="start_pending", last_observation=observed,
                           start_operation_id=operation_id,
                           operations=[dict(id=operation_id, action="start", dispatch_attempted=True,
                                            created_at=time.time(), dispatch_returned=False)])
                self._write(job)
            self._schedule(job, "start", operation_id)
        return copy.deepcopy(job)

    async def status(self, job_id):
        job = self._read(job_id)
        try:
            observed = await self._observe(job)
        except (ValueError, OSError, TimeoutError, asyncio.TimeoutError) as error:
            return dict(job, observation_error=str(error), no_new_dispatch=True)
        async with self._lock:
            with self._exclusive():
                job = self._read(job_id)
                try:
                    self._reconcile(job, observed)
                except ValueError as error:
                    return dict(job, observation_error=str(error), no_new_dispatch=True)
                self._write(job)
        return dict(job, no_new_dispatch=True)

    async def control(self, job_id, action, confirm_reset=False):
        if action not in CONTROL_ACTIONS:
            raise ValueError("control action must be stop, pause, resume or reset")
        if action == "reset" and confirm_reset is not True:
            raise ValueError("reset requires explicit confirm_reset=true")
        job = self._read(job_id)
        observed = await self._observe(job)
        allowed = {"stop": {"running", "paused"}, "pause": {"running"},
                   "resume": {"paused"}, "reset": {"stopped", "completed"}}
        if observed["modal_blocked"] or observed["router_state"] not in allowed[action]:
            raise ValueError("control action does not match observed router state")
        if not getattr(self.adapter, "supports_concurrent_control", False) and self._tasks:
            raise ValueError("adapter has no independently qualified active-router control channel")
        async with self._lock:
            with self._exclusive():
                job = self._read(job_id)
                self._reconcile(job, observed)
                if any(op["action"] != "start" and not op.get("transition_verified") for op in job["operations"]):
                    raise ValueError("previous control outcome is unresolved; no repeated input")
                operation_id = uuid.uuid4().hex
                job["operations"].append(dict(id=operation_id, action=action,
                    process="PCB:Autoroute", parameters={"Action": CONTROL_ACTIONS[action]},
                    dispatch_attempted=True, dispatch_returned=False, created_at=time.time()))
                job.update(state=action + "_pending", last_observation=observed)
                self._write(job)
            self._schedule(job, action, operation_id)
        return copy.deepcopy(job)
