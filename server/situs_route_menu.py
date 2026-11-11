"""Native route-menu navigation and controls, without the PAS request queue.

The public job controller must bind the intended full PCB path separately and
serialize UI operations. This module pins the process and exact main title.
"""
from __future__ import annotations

import situs_setup_extended as setup

SCOPES = {"route": ["Route"], "autoroute": ["Route", "Auto Route"]}
COMMANDS = {"setup": "setup", "all": "all", "pause": "pause", "resume": "resume", "stop": "stop", "reset": "reset"}


def _receipt(target, main, scope, observed):
    receipt = setup._receipt(target, main, "main", observed)
    receipt.pop("snapshot_sha256")
    receipt["menu_scope"] = scope
    return dict(receipt, snapshot_sha256=setup._digest(receipt))


def _plan(receipt, action, runtime_id):
    node = setup._node(receipt, runtime_id)
    if node.get("control_type") != "ControlType.MenuItem":
        raise ValueError("Only a live exact route MenuItem can be addressed")
    scope = receipt["menu_scope"]
    name = setup._label(node.get("name", ""))
    if action == "expand_route":
        if scope != "route" or name != "route" or node["runtime_id"] != receipt["nodes"][0]["runtime_id"]:
            raise ValueError("Exact Route subtree root required")
        operation = "expand"
    elif action == "expand_auto_route":
        if scope == "route":
            candidates = [n for n in receipt["nodes"] if setup._label(n.get("name", "")) == "auto route" and n.get("control_type") == "ControlType.MenuItem"]
            if len(candidates) != 1 or candidates[0]["runtime_id"] != runtime_id:
                raise ValueError("One observed Auto Route item in the Route subtree required")
        elif scope != "autoroute" or node["runtime_id"] != receipt["nodes"][0]["runtime_id"]:
            raise ValueError("Exact Auto Route subtree root required")
        if name != "auto route":
            raise ValueError("Exact Auto Route caption required")
        operation = "expand"
    elif action in COMMANDS:
        if scope != "autoroute" or name != COMMANDS[action]:
            raise ValueError("Exact command under Route > Auto Route required")
        # The target must be an immediate child of the scoped Auto Route menu.
        parent = ','.join(str(n) for n in receipt["nodes"][0]["runtime_id"])
        if node.get("parent_runtime_id") != parent:
            raise ValueError("Command is not an immediate Auto Route child")
        operation = "invoke"
    else:
        raise ValueError("Unsupported native route-menu action")
    setup._has(node, "ExpandCollapse" if operation == "expand" else "Invoke")
    if operation == "expand":
        state = node.get("states", {}).get("expand")
        if state not in (0, 1):
            raise ValueError("Menu expand state is not observable")
        return operation, state == 1
    return operation, False


def control(exe, action, target_hwnd, expected_main_title, menu_scope="route", expected_snapshot=None, runtime_id=None):
    """Inspect one route subtree or dispatch exactly one named native action.

    Pause/Resume/Stop/Reset do not enqueue scripts or wait for a script queue.
    A returned invocation is not proof of routing state. Reopen and inspect the
    command menu, and use the bound job's progress/completion checks separately.
    """
    result = dict(success=False, action=action, input_attempted=False, completion_verified=False,
                  script_dispatched=False, retry_performed=False, document_save_requested=False,
                  routing_state_verified=False, document_binding="exact main title; full path must be bound by caller job")
    target = None
    try:
        if menu_scope not in SCOPES:
            raise ValueError("menu_scope must be route or autoroute")
        if action == "inspect" and (expected_snapshot is not None or runtime_id is not None):
            raise ValueError("Inspection accepts no mutation arguments")
        target = setup.CaptureTarget(exe, target_hwnd)
        main = setup._bind(target, "main", expected_main_title)
        request = setup._request(target, main, "main")
        request.update(scope_path=SCOPES[menu_scope], allow_partial=False)
        observed = setup._run_worker(request)
        if not observed.get("success"):
            raise ValueError(observed.get("error", "Menu inspection failed"))
        if setup._bind(target, "main", expected_main_title) != main:
            raise ValueError("Main-window state changed during menu inspection")
        receipt = _receipt(target, main, menu_scope, observed)
        result["snapshot"] = receipt
        if action == "inspect":
            return dict(result, success=True, no_input=True)
        if not receipt["complete"] or expected_snapshot != receipt:
            raise ValueError("Expected complete menu snapshot differs from current state; no input")
        operation, noop = _plan(receipt, action, runtime_id)
        if noop:
            return dict(result, success=True, no_input=True, menu_expanded_verified=True)
        request.update(operation=operation, runtime_id=runtime_id, value=None, expected_nodes=receipt["nodes"])
        target.verify()
        response = setup._run_worker(request)
        result.update(input_attempted=bool(response.get("input_attempted")), operation=operation,
                      operation_returned=bool(response.get("operation_returned")))
        if not response.get("success"):
            raise ValueError(response.get("error", "Native menu action failed"))
        if response.get("complete"):
            result["after_snapshot"] = _receipt(target, main, menu_scope, response)
            if operation == "expand":
                node = setup._node(result["after_snapshot"], runtime_id)
                if node.get("states", {}).get("expand") != 1:
                    raise ValueError("Menu expanded state was not verified after one gesture")
                result["menu_expanded_verified"] = True
        else:
            result["post_action_readback_error"] = response.get("readback_error")
        return dict(result, success=True, outcome="One native menu operation returned; routing completion remains unverified")
    except Exception as error:
        return dict(result, error=str(error), blocked=not result["input_attempted"], outcome_unknown=result["input_attempted"])
    finally:
        if target is not None:
            target.close()
