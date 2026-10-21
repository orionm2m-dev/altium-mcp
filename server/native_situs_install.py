"""Stage a fixed reviewed native Situs bundle; never launch or call Altium."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

REVIEWED = (
    ("NativeSitusReadOnly.dll", "build/NativeSitusReadOnly.dll", 9728,
     "cf4f117c9b8c3cafca29d4a09c4a4149ea99ec809cdf7c847f75ede1242d6b64"),
    ("NativeSitusReadOnly.ins", "NativeSitusReadOnly.ins", 399,
     "06c5600819674b0029d6303af2a814cf894540040283c67b8e4ebc78e0c21c07"),
    ("NativeSitusReadOnly.rcs", "NativeSitusReadOnly.rcs", 472,
     "81f90d23cba3e48048f2f95dcd74ce17be21990c5efd27828807b258c9b9b40e"),
)

CONTROL_REVIEWED = (
    ("NativeSitusControl.dll", "build/NativeSitusControl.dll", 49152,
     "42cecbd3b6f1a7108d33d969c24f3a197b5c6839218b73f9538733bd8e00c7f3"),
    ("NativeSitusControl.ins", "NativeSitusControl.ins", 310,
     "4fe5777bddb0f169dade363149123c34c5dad3aa0d4eb8dcc50643144646722a"),
    ("NativeSitusControl.rcs", "NativeSitusControl.rcs", 162,
     "f3d42f85c9d2c361131ab7f6cbc1070324e6856d44ee7e117e04721fccaca766"),
)
REVIEWED_BUNDLES = {
    "readonly": ("native_situs", REVIEWED),
    "native_control": ("native_situs_next", CONTROL_REVIEWED),
}


def _reviewed_bundle(bundle):
    if not isinstance(bundle, str) or bundle not in REVIEWED_BUNDLES:
        raise ValueError("bundle must be readonly or native_control")
    return REVIEWED_BUNDLES[bundle]


def _bundle_root(bundle="readonly"):
    directory, _ = _reviewed_bundle(bundle)
    return Path(__file__).resolve().parents[1] / "dev" / directory


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _plain_path(path):
    """Reject links/reparse points before resolve can hide a redirected path."""
    path = Path(path)
    if not path.is_absolute() or any(c in str(path) for c in "\r\n\0"):
        raise ValueError("An absolute single-line path is required")
    if ".." in path.parts:
        raise ValueError("Parent traversal is forbidden")
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Links/reparse points are forbidden: " + str(item))
    return path.resolve()


def _snapshot(exe, exchange_dir, bundle="readonly"):
    _, reviewed = _reviewed_bundle(bundle)
    executable = _plain_path(exe)
    exchange = _plain_path(exchange_dir)
    if executable.name.casefold() != "x2.exe" or not executable.is_file():
        raise ValueError("Configured existing X2.exe is required")
    if not exchange.is_dir():
        raise ValueError("Configured exchange directory must exist")
    system = _plain_path(executable.parent / "System")
    if not system.is_dir():
        raise ValueError("Configured executable/System directory must exist")
    files, payloads = [], {}
    for name, relative, size, digest in reviewed:
        source = _plain_path(_bundle_root(bundle) / relative)
        if not source.is_file() or source.stat().st_size != size:
            raise ValueError("Reviewed bundled artifact size mismatch: " + name)
        content = source.read_bytes()
        if _sha(content) != digest:
            raise ValueError("Reviewed bundled artifact hash mismatch: " + name)
        target = _plain_path(system / name)
        if target.parent != system:
            raise ValueError("Target escaped configured System directory")
        files.append(dict(name=name, source=str(source), target=str(target),
                          size=size, sha256=digest, target_exists=target.exists()))
        payloads[name] = content
    snapshot = dict(schema_version=1, bundle=bundle, configured_exe=str(executable),
                    executable_sha256=_sha(executable.read_bytes()),
                    target_system=str(system), exchange_directory=str(exchange), files=files,
                    registry_modified=False, dependencies_copied=False,
                    launch_requested=False, restart_required=True)
    return snapshot, payloads


def inspect(exe, exchange_dir, bundle="readonly"):
    """Return a hash-bound review plan; creates no files."""
    try:
        snapshot, _ = _snapshot(exe, exchange_dir, bundle)
        return dict(success=True, action="inspect", bundle=bundle, plan=snapshot,
                    plan_sha256=_sha(_canonical_json(snapshot).encode("ascii")),
                    can_install=not any(f["target_exists"] for f in snapshot["files"]),
                    installed=False, runtime_qualified=False)
    except (OSError, ValueError, TypeError) as error:
        return dict(success=False, action="inspect", installed=False, error=str(error))


def _persist(folder, name, value):
    target = folder / name
    temporary = folder / (name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(target)


def _file_identity(info):
    return (info.st_dev, info.st_ino)


def _publish(target, content, owned):
    """An exclusive creation is recorded before writing; no replacement."""
    with target.open("xb", buffering=0) as stream:
        info = os.fstat(stream.fileno())
        record = dict(path=target, identity=_file_identity(info), written=0, content=content)
        owned.append(record)
        while record["written"] < len(content):
            count = stream.write(content[record["written"]:])
            if not count:
                raise OSError("Incomplete exclusive file write")
            record["written"] += count
        os.fsync(stream.fileno())
        if _file_identity(target.stat()) != record["identity"]:
            raise OSError("Created file identity changed")
    if target.read_bytes() != content:
        raise OSError("Installed artifact verification failed")


def _rollback(owned, system):
    results = []
    for record in reversed(owned):
        target = record["path"]
        try:
            if _plain_path(target).parent != system:
                raise ValueError("Rollback target escaped System")
            if not target.exists():
                results.append(dict(path=str(target), status="already_absent"))
                continue
            if _file_identity(target.stat()) != record["identity"]:
                raise ValueError("Created file identity changed; preserved")
            content = target.read_bytes()
            # Remove only bytes known to have been written by this operation.
            if content != record["content"][:record["written"]]:
                raise ValueError("Created file content changed; preserved")
            target.unlink()
            results.append(dict(path=str(target), status="removed_owned_file"))
        except (OSError, ValueError) as error:
            results.append(dict(path=str(target), status="preserved", error=str(error)))
    return results


def install(exe, exchange_dir, expected_plan_sha256, operation_id="", bundle="readonly"):
    """Install exactly the reviewed three files; never overwrite or restart.

    The caller serializes this operation with its existing bridge lock.
    Filesystem exclusive creation independently prevents concurrent replacement.
    A repeated operation ID is refused, never replayed. Staging a DLL does not
    load it; the caller separately handles unsaved-document inventory/restart.
    """
    result = dict(success=False, action="install", installed=False,
                  native_action=False, launch_requested=False, restart_required=True,
                  runtime_qualified=False, registry_modified=False,
                  dependencies_copied=False, retry_allowed=False)
    owned, folder = [], None
    try:
        _reviewed_bundle(bundle)
        result["bundle"] = bundle
        if not isinstance(expected_plan_sha256, str) or re.fullmatch("[0-9a-f]{64}", expected_plan_sha256) is None:
            raise ValueError("Exact inspect plan SHA-256 required")
        operation_id = uuid.uuid4().hex if operation_id == "" else operation_id
        if not isinstance(operation_id, str) or re.fullmatch("[0-9a-f]{32}", operation_id) is None:
            raise ValueError("operation_id must be 32 lowercase hex characters")
        result["operation_id"] = operation_id
        snapshot, payloads = _snapshot(exe, exchange_dir, bundle)
        actual = _sha(_canonical_json(snapshot).encode("ascii"))
        if actual != expected_plan_sha256:
            raise ValueError("Inspect plan changed; no files installed")
        if any(f["target_exists"] for f in snapshot["files"]):
            raise ValueError("All three target paths must be absent")
        system = Path(snapshot["target_system"])
        ledger = _plain_path(Path(snapshot["exchange_directory"]) / "native_situs_install")
        ledger.mkdir(exist_ok=True)
        operation = _plain_path(ledger / operation_id)
        operation.mkdir(exist_ok=False)
        folder = operation
        result["receipt_directory"] = str(folder)
        _persist(folder, "intent.json", dict(stage="prepared", plan_sha256=actual,
                                            plan=snapshot, operation_id=operation_id))
        for entry in snapshot["files"]:
            target = _plain_path(entry["target"])
            if target.parent != system:
                raise ValueError("Target directory changed")
            _publish(target, payloads[entry["name"]], owned)
            _persist(folder, "progress.json",
                     dict(stage="staging", installed_files=[dict(name=r["path"].name,
                         sha256=_sha(r["content"]), size=r["written"],
                         file_identity=list(r["identity"])) for r in owned]))
        for entry in snapshot["files"]:
            target = _plain_path(entry["target"])
            if target.parent != system or _sha(target.read_bytes()) != entry["sha256"]:
                raise ValueError("Final artifact verification failed")
        result.update(success=True, installed=True, plan_sha256=actual,
                      installed_files=[dict(path=f["target"], size=f["size"], sha256=f["sha256"])
                                       for f in snapshot["files"]])
        _persist(folder, "result.json", result)
        return result
    except Exception as error:
        result.update(success=False, installed=False, error=str(error))
        result.pop("installed_files", None)
        if owned:
            result["rollback"] = _rollback(owned, system)
            result["partial_files_remaining"] = any(r["status"] == "preserved" for r in result["rollback"])
        if folder is not None:
            try:
                _persist(folder, "result.json", result)
            except OSError as receipt_error:
                result["receipt_error"] = str(receipt_error)
        return result
