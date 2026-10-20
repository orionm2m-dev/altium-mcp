"""Inspect a specific import wizard and cancel its observed control once.

Importing the module performs no UI calls. The public wrapper serializes each
operation with the bridge command lock and the cross-process emergency claim.
"""
import copy, hashlib, hmac, json, ntpath, os, secrets, tempfile, time
from pathlib import Path

WIZARD_CLASS = "TPCBImportWizard"
WIZARD_TITLE = "DXP Import Wizard"
BUTTON_CLASSES = {"Button", "TButton", "TBitBtn", "TXPBitBtn"}
CANCEL_CAPTIONS = {"cancel", "отмена"}
TOKEN_TTL = 120.0

def canonical(path):
    return ntpath.normcase(ntpath.normpath(path))

def caption(text):
    return text.replace("&", "").strip().casefold()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def latch_hash(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else "ABSENT"

class ObservationStore:
    """One persistent plan, protected by the public wrapper's emergency_claim.

    Only a token hash and authenticated payload are stored, never the token.
    A matching token is consumed before policy validation or any gesture.
    """
    def __init__(self, directory):
        self.path = Path(directory) / "import_wizard_observation.json"

    def save(self, token, plan):
        payload = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        envelope = {"plan": plan, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "plan_hmac_sha256": hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()}
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".import_wizard_", suffix=".tmp", delete=False) as stream:
                temp = Path(stream.name)
                json.dump(envelope, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if temp is not None and temp.exists():
                temp.unlink()

    def take(self, token):
        if not isinstance(token, str) or not 16 <= len(token) <= 128:
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        expected = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(envelope.get("token_sha256", ""), expected):
            return None
        self.path.unlink()  # matching token consumed, even if payload/policy is invalid
        payload = json.dumps(envelope["plan"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        actual = hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope.get("plan_hmac_sha256", ""), actual):
            raise ValueError("Observation record changed; inspect again")
        return envelope["plan"]

def qualifying_cancel(snapshot, configured_exe, pid, hwnd):
    """Pure policy. Exact wizard only; never generic errors/confirmations/save UI."""
    ident = snapshot["identity"]; win = snapshot["window"]
    if ident["pid"] != pid or canonical(ident["exe"]) != canonical(configured_exe):
        raise ValueError("Process/executable mismatch")
    if not isinstance(ident["creation_filetime"], str) or not ident["creation_filetime"].isdigit():
        raise ValueError("Exact FILETIME must be a decimal string")
    if not ident["running"] or not ident["same_user"]:
        raise ValueError("Target identity is not a running same-user process")
    if str(win["hwnd"]) != str(hwnd) or win["pid"] != pid:
        raise ValueError("Window binding mismatch")
    if win["class"] != WIZARD_CLASS or win["title"] != WIZARD_TITLE:
        raise ValueError("Only the observed exact DXP import wizard is supported")
    if not win["visible"] or not win["enabled"] or snapshot.get("blocking_dialogs"):
        raise ValueError("Wizard hidden, disabled or another modal requires inspection")
    found = [b for b in snapshot["buttons"] if b["class"] in BUTTON_CLASSES and caption(b["caption"]) in CANCEL_CAPTIONS]
    if len(found) != 1:
        raise ValueError("A unique observed Cancel button is required")
    button = found[0]
    if not button["visible"] or not button["enabled"] or button["pid"] != pid or not button["descendant"]:
        raise ValueError("Cancel button is not a live enabled descendant of this wizard")
    if button["client_rect"][2] <= 0 or button["client_rect"][3] <= 0:
        raise ValueError("Cancel button has invalid client bounds")
    return button

class WizardControl:
    """Transport-independent policy. Tests inject a fake adapter and latch reader."""
    def __init__(self, adapter, configured_exe, read_latch, store, clock=time.time):
        self.adapter, self.exe, self.read_latch, self.clock = adapter, configured_exe, read_latch, clock
        self.store = store

    def inspect(self, pid, hwnd):
        base = {"wizard_source": "unknown", "script_dispatched": False, "executor_recovered": False,
                "latch_cleared": False, "save_performed": False, "process_termination_requested": False}
        try:
            snapshot = self.adapter.snapshot(pid, str(hwnd))
            # Snapshot contains button captions only; no Edit values or document text.
            base.update(snapshot=snapshot, recovery_latch_sha256=latch_hash(self.read_latch()))
            button = qualifying_cancel(snapshot, self.exe, pid, str(hwnd))
            token = secrets.token_urlsafe(24)
            now = self.clock()
            self.store.save(token, {"snapshot": copy.deepcopy(snapshot), "button": copy.deepcopy(button),
                                   "issued": now, "expires": now+TOKEN_TTL,
                                   "latch_hash": base["recovery_latch_sha256"]})
            return dict(base, success=True, cancel_available=True, observation_token=token, token_ttl_seconds=TOKEN_TTL)
        except Exception as exc:
            return dict(base, success=False, cancel_available=False, error=str(exc))

    def cancel(self, pid, hwnd, token, acknowledge_managed_wizard=False):
        base = {"wizard_source": "unknown", "cancel_requested": False, "script_dispatched": False,
                "executor_recovered": False, "latch_cleared": False, "save_performed": False,
                "process_termination_requested": False, "retry_performed": False}
        try:
            plan = self.store.take(token)
            if plan is None:
                raise ValueError("Unknown or consumed observation token")
            if acknowledge_managed_wizard is not True:
                raise ValueError("Explicit confirmation of this managed wizard cancellation is required")
            if not plan["issued"] <= self.clock() <= plan["expires"]:
                raise ValueError("Observation expired; inspect again")
            if latch_hash(self.read_latch()) != plan["latch_hash"]:
                raise ValueError("Recovery state changed; inspect again")
            fresh = self.adapter.snapshot(pid, str(hwnd))
            button = qualifying_cancel(fresh, self.exe, pid, str(hwnd))
            if digest(fresh) != digest(plan["snapshot"]) or button != plan["button"]:
                raise ValueError("Process/window/button state changed; inspect again")
            # Recheck immediately before the adapter's bounded native operation.
            if latch_hash(self.read_latch()) != plan["latch_hash"]:
                raise ValueError("Recovery state changed before cancel")
            base["cancel_requested"] = True
            self.adapter.cancel_once(fresh, button)
            after = self.adapter.observe_after(fresh)
            unchanged = latch_hash(self.read_latch()) == plan["latch_hash"]
            closed = after.get("same_process_alive") is True and after.get("wizard_visible") is False
            return dict(base, success=closed, wizard_closed_verified=closed,
                        recovery_latch_unchanged=unchanged, after=after,
                        next_action="Inspect pending launcher and use the existing explicit recovery probe; cancellation does not prove executor readiness")
        except Exception as exc:
            return dict(base, success=False, blocked=not base["cancel_requested"], outcome_unknown=base["cancel_requested"],
                        wizard_closed_verified=False, error=str(exc), next_action="Reinspect; do not replay a click automatically")

class NativeAdapter:
    """Only Win32 reads and a bounded gesture to the observed Cancel control.
    No generic keyboard/mouse injection, shell, launcher, script or kill API.
    """
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        import emergency_stop
        self.c, self.w, self.process = ctypes, w, emergency_stop.WinProcess
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.enum_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "IsWindow": ([w.HWND], w.BOOL), "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsWindowEnabled": ([w.HWND], w.BOOL), "IsChild": ([w.HWND,w.HWND],w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND,ctypes.POINTER(w.DWORD)],w.DWORD),
            "GetWindowTextLengthW": ([w.HWND],ctypes.c_int),
            "GetWindowTextW": ([w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
            "GetClassNameW": ([w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
            "GetClientRect": ([w.HWND,ctypes.POINTER(w.RECT)],w.BOOL),
            "GetWindowRect": ([w.HWND,ctypes.POINTER(w.RECT)],w.BOOL),
            "GetWindow": ([w.HWND,w.UINT],w.HWND),
            "EnumWindows": ([self.enum_type,w.LPARAM],w.BOOL),
            "EnumChildWindows": ([w.HWND,self.enum_type,w.LPARAM],w.BOOL),
            "SendMessageTimeoutW": ([w.HWND,w.UINT,w.WPARAM,w.LPARAM,w.UINT,w.UINT,ctypes.POINTER(ctypes.c_size_t)],w.LPARAM),
        }
        for name,(args,result) in signatures.items():
            method=getattr(self.u,name);method.argtypes=args;method.restype=result

    def _pid(self, hwnd):
        pid=self.w.DWORD();self.u.GetWindowThreadProcessId(hwnd,self.c.byref(pid));return int(pid.value)
    def _text(self, hwnd):
        size=min(512,self.u.GetWindowTextLengthW(hwnd)+1);buf=self.c.create_unicode_buffer(max(1,size));self.u.GetWindowTextW(hwnd,buf,len(buf));return buf.value
    def _class(self, hwnd):
        buf=self.c.create_unicode_buffer(256);self.u.GetClassNameW(hwnd,buf,len(buf));return buf.value
    def _rect(self, hwnd, client=False):
        rect=self.w.RECT();fn=self.u.GetClientRect if client else self.u.GetWindowRect
        if not fn(hwnd,self.c.byref(rect)):raise OSError("Cannot read window bounds")
        return [rect.left,rect.top,rect.right,rect.bottom]
    def _identity(self, process):
        ident=process.identity()
        with self.process(__import__('os').getpid()) as own: same=own.identity()["user_sid"]==ident["user_sid"]
        return {"pid":ident["pid"],"creation_filetime":str(ident["creation_filetime"]),"exe":ident["exe"],"running":ident["running"],"same_user":same}

    def snapshot(self, pid, hwnd):
        hwnd=int(hwnd);buttons=[];blockers=[]
        with self.process(pid) as process:
            ident=self._identity(process)
            if not self.u.IsWindow(hwnd) or self._pid(hwnd)!=pid:raise ValueError("Window absent or another PID")
            win={"hwnd":str(hwnd),"pid":pid,"class":self._class(hwnd),"title":self._text(hwnd),
                 "visible":bool(self.u.IsWindowVisible(hwnd)),"enabled":bool(self.u.IsWindowEnabled(hwnd)),
                 "owner_hwnd":str(int(self.u.GetWindow(hwnd,4) or 0)),"rect":self._rect(hwnd)}
            # Fail before reading descendants if this is any other dialog type.
            if win["class"]!=WIZARD_CLASS or win["title"]!=WIZARD_TITLE:raise ValueError("Exact import wizard not found")
            errors=[]
            def child(h,_):
                try:
                    cls=self._class(h)
                    if cls in BUTTON_CLASSES:
                        buttons.append({"hwnd":str(int(h)),"pid":self._pid(h),"class":cls,"caption":self._text(h),
                                        "visible":bool(self.u.IsWindowVisible(h)),"enabled":bool(self.u.IsWindowEnabled(h)),
                                        "descendant":bool(self.u.IsChild(hwnd,h)),"client_rect":self._rect(h,True)})
                    return True
                except Exception as exc:
                    errors.append(str(exc)); return False
            self.u.EnumChildWindows(hwnd,self.enum_type(child),0)
            if errors: raise OSError("Incomplete child observation: " + errors[0])
            def top(h,_):
                try:
                    if int(h)!=hwnd and self.u.IsWindowVisible(h) and self._pid(h)==pid:
                        cls=self._class(h)
                        if cls in {"#32770","TMessageForm","TShowException"}:
                            blockers.append({"hwnd":str(int(h)),"class":cls})
                    return True
                except Exception as exc:
                    errors.append(str(exc)); return False
            top_ok=self.u.EnumWindows(self.enum_type(top),0)
            if errors or not top_ok: raise OSError("Incomplete top-level observation")
            if not self.u.IsWindow(hwnd) or self._pid(hwnd)!=pid: raise ValueError("Wizard disappeared during observation")
            if self._identity(process)!=ident:raise ValueError("Process identity changed during observation")
        return {"identity":ident,"window":win,"buttons":sorted(buttons,key=lambda b:int(b["hwnd"])),
                "blocking_dialogs":sorted(blockers,key=lambda b:int(b["hwnd"]))}

    def cancel_once(self, snapshot, button):
        ident=snapshot['identity'];pid=ident['pid'];hwnd=int(snapshot['window']['hwnd']);bh=int(button['hwnd'])
        with self.process(pid) as process:
            if self._identity(process)!=ident:raise ValueError("Process identity changed before gesture")
            if self.snapshot(pid,str(hwnd))!=snapshot:raise ValueError("Wizard changed before gesture")
            rect=button['client_rect'];x=(rect[2]-rect[0])//2;y=(rect[3]-rect[1])//2
            if not(0<=x<=32767 and 0<=y<=32767):raise ValueError("Unsupported button bounds")
            point=(y<<16)|x
            # VCL TXPBitBtn handles these existing provider messages; BM_CLICK is not sufficient.
            for message,wparam in [(0x0201,1),(0x0202,0)]:
                if self._identity(process)!=ident or self._pid(bh)!=pid or not self.u.IsChild(hwnd,bh):raise ValueError("Target changed during gesture")
                result=self.c.c_size_t()
                if not self.u.SendMessageTimeoutW(bh,message,wparam,point,3,300,self.c.byref(result)):
                    raise TimeoutError("Cancel gesture timed out; outcome unknown, no retry")

    def observe_after(self, snapshot):
        ident=snapshot['identity'];hwnd=int(snapshot['window']['hwnd']);deadline=time.monotonic()+2.0
        while True:
            try:
                with self.process(ident['pid']) as process: alive=self._identity(process)==ident
            except OSError: return {"same_process_alive":False,"wizard_visible":None,"target_outcome":"unknown_or_exited"}
            visible=bool(self.u.IsWindow(hwnd) and self.u.IsWindowVisible(hwnd) and self._pid(hwnd)==ident['pid'] and self._class(hwnd)==WIZARD_CLASS)
            if not alive or not visible or time.monotonic()>=deadline:
                return {"same_process_alive":alive,"wizard_visible":visible,"secondary_confirmation_answered":False}
            time.sleep(.1)
