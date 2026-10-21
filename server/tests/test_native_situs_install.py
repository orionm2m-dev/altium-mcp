"""Filesystem-only installer tests using temporary fake Altium directories."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_situs_install as installer


class InstallerTests(unittest.TestCase):
    bundle_name = "readonly"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "Altium"
        self.app.mkdir()
        self.exe = self.app / "X2.exe"
        self.exe.write_bytes(b"fake executable never launched")
        self.system = self.app / "System"
        self.system.mkdir()
        self.exchange = self.root / "exchange"
        self.exchange.mkdir()
        self.bundles = {}
        for name, (_, reviewed) in installer.REVIEWED_BUNDLES.items():
            directory = self.root / name
            (directory / "build").mkdir(parents=True)
            real_bundle = installer._bundle_root(name)
            for _, relative, _, _ in reviewed:
                (directory / relative).write_bytes((real_bundle / relative).read_bytes())
            self.bundles[name] = directory
        self.bundle = self.bundles[self.bundle_name]
        self.reviewed = installer.REVIEWED_BUNDLES[self.bundle_name][1]
        self.patch = patch.object(installer, "_bundle_root", side_effect=lambda bundle="readonly": self.bundles[bundle])
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.op = "a" * 32

    def plan(self):
        value = installer.inspect(self.exe, self.exchange, self.bundle_name)
        self.assertTrue(value["success"], value)
        return value

    def install(self, plan=None, **changes):
        args = dict(expected_plan_sha256=(plan or self.plan())["plan_sha256"], operation_id=self.op,
                    bundle=self.bundle_name)
        args.update(changes)
        return installer.install(self.exe, self.exchange, **args)

    def test_inspection_creates_nothing_and_binds_three_reviewed_files(self):
        result = self.plan()
        self.assertTrue(result["can_install"])
        self.assertEqual(len(result["plan"]["files"]), 3)
        self.assertEqual(result["plan"]["bundle"], self.bundle_name)
        self.assertEqual(list(self.system.iterdir()), [])
        self.assertEqual(list(self.exchange.iterdir()), [])
        self.assertFalse(result["installed"])
        self.assertFalse(result["runtime_qualified"])

    def test_install_exact_payload_and_persist_intent_hashes(self):
        result = self.install()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["installed"])
        self.assertFalse(result["native_action"])
        self.assertFalse(result["launch_requested"])
        self.assertEqual(result["bundle"], self.bundle_name)
        self.assertEqual({p.name for p in self.system.iterdir()}, {f[0] for f in self.reviewed})
        for name, relative, _, digest in self.reviewed:
            self.assertEqual(installer._sha((self.system / name).read_bytes()), digest)
        folder = Path(result["receipt_directory"])
        intent = json.loads((folder / "intent.json").read_text())
        receipt = json.loads((folder / "result.json").read_text())
        self.assertEqual(len(intent["plan"]["files"]), 3)
        self.assertEqual(receipt["installed_files"], result["installed_files"])

    def test_target_conflict_never_overwrites_existing_file(self):
        target = self.system / self.reviewed[0][0]
        target.write_bytes(b"unrelated existing content")
        result = self.install()
        self.assertFalse(result["success"])
        self.assertEqual(target.read_bytes(), b"unrelated existing content")
        self.assertEqual(len(list(self.system.iterdir())), 1)

    def test_directory_conflict_is_not_deleted(self):
        target = self.system / self.reviewed[1][0]
        target.mkdir()
        result = self.install()
        self.assertFalse(result["success"])
        self.assertTrue(target.is_dir())

    def test_stale_target_plan_does_not_touch_conflicting_file(self):
        plan = self.plan()
        target = self.system / self.reviewed[2][0]
        target.write_bytes(b"new")
        result = self.install(plan)
        self.assertFalse(result["success"])
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(list(self.exchange.iterdir()), [])

    def test_stale_executable_plan_refuses(self):
        plan = self.plan()
        self.exe.write_bytes(b"updated configured executable")
        result = self.install(plan)
        self.assertFalse(result["success"])
        self.assertEqual(list(self.system.iterdir()), [])

    def test_unreviewed_source_bytes_refused(self):
        source = self.bundle / self.reviewed[0][1]
        data = source.read_bytes()
        source.write_bytes(b"x" + data[1:])
        self.assertFalse(installer.inspect(self.exe, self.exchange, self.bundle_name)["success"])
        self.assertEqual(list(self.system.iterdir()), [])

    def test_missing_source_refused(self):
        (self.bundle / self.reviewed[1][1]).unlink()
        self.assertFalse(installer.inspect(self.exe, self.exchange, self.bundle_name)["success"])

    def test_invalid_plan_and_operation_id_refused(self):
        for change in (dict(expected_plan_sha256="x"), dict(operation_id="../bad"),
                       dict(operation_id="A" * 32), dict(operation_id=1)):
            result = self.install(**change)
            self.assertFalse(result["success"])
        self.assertEqual(list(self.system.iterdir()), [])
        self.assertEqual(list(self.exchange.iterdir()), [])

    def test_wrong_executable_name_and_relative_paths_refused(self):
        other = self.app / "other.exe"
        other.write_bytes(b"x")
        self.assertFalse(installer.inspect(other, self.exchange)["success"])
        self.assertFalse(installer.inspect("X2.exe", self.exchange)["success"])
        self.assertFalse(installer.inspect(self.exe, "exchange")["success"])

    def test_second_file_failure_rolls_back_only_created_first(self):
        original = installer._publish
        calls = []
        def fail_second(target, content, owned):
            calls.append(target.name)
            if len(calls) == 2:
                raise RuntimeError("simulated unexpected second creation failure")
            original(target, content, owned)
        with patch.object(installer, "_publish", side_effect=fail_second):
            result = self.install()
        self.assertFalse(result["success"])
        self.assertEqual(list(self.system.iterdir()), [])
        self.assertFalse(result["partial_files_remaining"])
        self.assertEqual(result["rollback"][0]["status"], "removed_owned_file")

    def test_concurrent_existing_file_is_preserved_and_owned_one_rolled_back(self):
        original = installer._publish
        count = 0
        def race(target, content, owned):
            nonlocal count
            count += 1
            if count == 2:
                target.write_bytes(b"other writer")
            original(target, content, owned)
        with patch.object(installer, "_publish", side_effect=race):
            result = self.install()
        self.assertFalse(result["success"])
        self.assertEqual((self.system / self.reviewed[1][0]).read_bytes(), b"other writer")
        self.assertFalse((self.system / self.reviewed[0][0]).exists())

    def test_changed_owned_content_is_preserved_on_rollback(self):
        original = installer._publish
        count = 0
        def corrupt_and_fail(target, content, owned):
            nonlocal count
            count += 1
            if count == 2:
                owned[0]["path"].write_bytes(b"external modification")
                raise OSError("failed later")
            original(target, content, owned)
        with patch.object(installer, "_publish", side_effect=corrupt_and_fail):
            result = self.install()
        self.assertFalse(result["success"])
        self.assertTrue(result["partial_files_remaining"])
        self.assertEqual((self.system / self.reviewed[0][0]).read_bytes(), b"external modification")
        self.assertEqual(result["rollback"][0]["status"], "preserved")

    def test_repeated_operation_id_is_never_replayed(self):
        plan = self.plan()
        original = installer._publish
        with patch.object(installer, "_publish", side_effect=OSError("failed before create")):
            first = self.install(plan)
        self.assertFalse(first["success"])
        with patch.object(installer, "_publish", wraps=original) as publish:
            repeated = self.install(plan)
        self.assertFalse(repeated["success"])
        publish.assert_not_called()

    def test_partial_write_owned_bytes_can_be_rolled_back(self):
        target = self.system / self.reviewed[0][0]
        content = b"abcdef"
        with target.open("xb", buffering=0) as stream:
            stream.write(content[:3])
            import os
            identity = installer._file_identity(os.fstat(stream.fileno()))
        result = installer._rollback([dict(path=target, identity=identity, written=3, content=content)], self.system.resolve())
        self.assertEqual(result[0]["status"], "removed_owned_file")
        self.assertFalse(target.exists())

    def test_identity_change_preserves_target(self):
        target = self.system / self.reviewed[0][0]
        target.write_bytes(b"same")
        result = installer._rollback([dict(path=target, identity=(-1, -1), written=4, content=b"same")], self.system.resolve())
        self.assertEqual(result[0]["status"], "preserved")
        self.assertTrue(target.exists())


    def test_intent_failure_prevents_all_target_writes(self):
        with patch.object(installer, "_persist", side_effect=OSError("ledger unavailable")):
            with patch.object(installer, "_publish") as publish:
                result = self.install()
        self.assertFalse(result["success"])
        publish.assert_not_called()
        self.assertEqual(list(self.system.iterdir()), [])
        self.assertIn("receipt_error", result)

    def test_final_receipt_failure_rolls_back_all_targets_without_success_claim(self):
        original = installer._persist
        failed = False
        def fail_success_receipt(folder, name, value):
            nonlocal failed
            if name == "result.json" and value["success"] and not failed:
                failed = True
                raise OSError("receipt write interrupted")
            return original(folder, name, value)
        with patch.object(installer, "_persist", side_effect=fail_success_receipt):
            result = self.install()
        self.assertFalse(result["success"])
        self.assertFalse(result["installed"])
        self.assertNotIn("installed_files", result)
        self.assertEqual(list(self.system.iterdir()), [])
        self.assertEqual(len(result["rollback"]), 3)
        receipt = json.loads((Path(result["receipt_directory"]) / "result.json").read_text())
        self.assertEqual(receipt, result)

    def test_reparse_path_refused_before_resolve(self):
        from types import SimpleNamespace
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0, st_file_attributes=0x400)):
            with patch.object(Path, "resolve") as resolve:
                result = installer.inspect(self.exe, self.exchange)
        self.assertFalse(result["success"])
        self.assertIn("reparse", result["error"])
        resolve.assert_not_called()


class NativeControlInstallerTests(InstallerTests):
    bundle_name = "native_control"

    def test_old_default_bundle_remains_readonly(self):
        result = installer.inspect(self.exe, self.exchange)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["bundle"], "readonly")
        self.assertEqual({f["name"] for f in result["plan"]["files"]},
                         {f[0] for f in installer.REVIEWED})

    def test_plan_from_other_bundle_is_refused_without_writes(self):
        readonly = installer.inspect(self.exe, self.exchange)
        control = self.plan()
        self.assertNotEqual(readonly["plan_sha256"], control["plan_sha256"])
        for bundle, plan in (("native_control", readonly), ("readonly", control)):
            result = installer.install(self.exe, self.exchange, plan["plan_sha256"], self.op, bundle)
            self.assertFalse(result["success"])
        self.assertEqual(list(self.system.iterdir()), [])
        self.assertEqual(list(self.exchange.iterdir()), [])

    def test_fixed_bundles_coexist_without_replacing_old_module(self):
        old = installer.inspect(self.exe, self.exchange)
        installed_old = installer.install(self.exe, self.exchange, old["plan_sha256"], "b" * 32)
        self.assertTrue(installed_old["success"], installed_old)
        old_bytes = {f[0]: (self.system / f[0]).read_bytes() for f in installer.REVIEWED}
        control = self.install()
        self.assertTrue(control["success"], control)
        self.assertEqual(len(list(self.system.iterdir())), 6)
        for name, content in old_bytes.items():
            self.assertEqual((self.system / name).read_bytes(), content)

    def test_invalid_bundle_never_reads_source_or_creates_files(self):
        for invalid in ("", "../native_situs_next", "Native_Control", "other", None, [], 1):
            with patch.object(installer, "_bundle_root") as source:
                read = installer.inspect(self.exe, self.exchange, invalid)
                write = installer.install(self.exe, self.exchange, "a" * 64, self.op, invalid)
                self.assertFalse(read["success"])
                self.assertFalse(write["success"])
                source.assert_not_called()
        self.assertEqual(list(self.system.iterdir()), [])
        self.assertEqual(list(self.exchange.iterdir()), [])

    def test_reused_operation_id_across_bundles_is_not_replayed(self):
        old = installer.inspect(self.exe, self.exchange)
        with patch.object(installer, "_publish", side_effect=OSError("first attempt stopped")):
            first = installer.install(self.exe, self.exchange, old["plan_sha256"], self.op)
        self.assertFalse(first["success"])
        with patch.object(installer, "_publish") as publish:
            result = self.install()
        self.assertFalse(result["success"])
        publish.assert_not_called()
        self.assertEqual(list(self.system.iterdir()), [])

class PublicInstallerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import ast
        import asyncio
        from contextlib import contextmanager
        from types import SimpleNamespace
        from mcp.server.fastmcp import FastMCP, Context

        self.before_main = sys.modules.get("main")
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8"))
        nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "stage_situs_native_extension"]
        self.assertEqual(len(nodes), 1)
        self.server = FastMCP("native-situs-stage-offline-test")
        self.lock = asyncio.Lock()
        self.claim_active = False
        self.claim_calls = []
        self.exchange = Path("C:/configured/exchange")
        self.exe = "E:/configured/X2.exe"

        @contextmanager
        def claim(exchange):
            self.assertTrue(self.lock.locked())
            self.assertEqual(exchange, self.exchange)
            self.claim_calls.append(exchange)
            self.claim_active = True
            try:
                yield
            finally:
                self.claim_active = False

        self.emergency = SimpleNamespace(emergency_claim=claim)
        env = dict(Context=Context, mcp=self.server, json=json, asyncio=asyncio,
                   EXCHANGE_DIR=self.exchange, emergency_stop=self.emergency,
                   altium_bridge=SimpleNamespace(_command_lock=self.lock,
                       config=SimpleNamespace(altium_exe_path=self.exe)))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "public_stage_tool", "exec"), env)
        self.env = env

    async def asyncTearDown(self):
        self.assertIs(sys.modules.get("main"), self.before_main)

    async def call(self, arguments):
        result = await self.server.call_tool("stage_situs_native_extension", arguments)
        return json.loads(result[0].text)

    async def test_registered_schema_exposes_no_configurable_paths(self):
        tools = await self.server.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "stage_situs_native_extension")
        self.assertEqual(set(tools[0].inputSchema["properties"]),
                         {"action", "expected_plan_sha256", "operation_id", "bundle"})
        self.assertEqual(tools[0].inputSchema["required"], ["action"])
        self.assertEqual(tools[0].inputSchema["properties"]["bundle"]["default"], "readonly")

    async def test_inspect_uses_configured_paths_and_background_thread_inside_claim(self):
        import threading
        main_thread = threading.get_ident()
        def inspect(exe, exchange, bundle):
            self.assertEqual((exe, exchange), (self.exe, self.exchange))
            self.assertEqual(bundle, "readonly")
            self.assertTrue(self.lock.locked())
            self.assertTrue(self.claim_active)
            self.assertNotEqual(threading.get_ident(), main_thread)
            return dict(success=True, installed=False, can_install=True)
        with patch.object(installer, "inspect", side_effect=inspect) as run:
            result = await self.call({"action": "inspect"})
        self.assertTrue(result["success"])
        run.assert_called_once()
        self.assertFalse(self.lock.locked())
        self.assertFalse(self.claim_active)

    async def test_install_forwards_only_review_hash_and_operation_id(self):
        def install(*args):
            self.assertTrue(self.lock.locked())
            self.assertTrue(self.claim_active)
            self.assertEqual(args, (self.exe, self.exchange, "a" * 64, "b" * 32, "readonly"))
            return dict(success=True, installed=True, native_action=False)
        with patch.object(installer, "install", side_effect=install) as run:
            result = await self.call(dict(action="install", expected_plan_sha256="a" * 64,
                                          operation_id="b" * 32))
        self.assertTrue(result["installed"])
        self.assertFalse(result["native_action"])
        run.assert_called_once()

    async def test_explicit_control_bundle_forwarded_without_path_input(self):
        with patch.object(installer, "inspect", return_value=dict(success=True, installed=False)) as read:
            await self.call(dict(action="inspect", bundle="native_control"))
        read.assert_called_once_with(self.exe, self.exchange, "native_control")
        with patch.object(installer, "install", return_value=dict(success=True, installed=True)) as write:
            await self.call(dict(action="install", bundle="native_control", expected_plan_sha256="a" * 64,
                                 operation_id="b" * 32))
        write.assert_called_once_with(self.exe, self.exchange, "a" * 64, "b" * 32, "native_control")

    async def test_unknown_bundle_never_claims_or_dispatches_backend(self):
        with patch.object(installer, "inspect") as read, patch.object(installer, "install") as write:
            result = await self.call(dict(action="install", bundle="../native_situs_next"))
        self.assertFalse(result["success"])
        self.assertEqual(self.claim_calls, [])
        read.assert_not_called()
        write.assert_not_called()

    async def test_invalid_action_never_claims_or_dispatches_backend(self):
        with patch.object(installer, "inspect") as read, patch.object(installer, "install") as write:
            result = await self.call({"action": "launch"})
        self.assertFalse(result["success"])
        self.assertEqual(self.claim_calls, [])
        read.assert_not_called()
        write.assert_not_called()

    async def test_busy_claim_refuses_without_backend(self):
        self.emergency.emergency_claim = lambda _: (_ for _ in ()).throw(OSError("claim busy"))
        with patch.object(installer, "install") as write:
            result = await self.call(dict(action="install", expected_plan_sha256="a" * 64))
        self.assertFalse(result["success"])
        self.assertTrue(result["blocked"])
        self.assertFalse(result["installed"])
        write.assert_not_called()
        self.assertFalse(self.lock.locked())

    async def test_cancellation_retains_both_locks_until_worker_finishes(self):
        import asyncio
        import threading
        started, finish = threading.Event(), threading.Event()
        def inspect(*_):
            started.set()
            if not finish.wait(3):
                raise OSError("test worker deadline")
            self.assertTrue(self.lock.locked())
            self.assertTrue(self.claim_active)
            return dict(success=True, installed=False)
        with patch.object(installer, "inspect", side_effect=inspect):
            task = asyncio.create_task(self.call({"action": "inspect"}))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(started.is_set())
                task.cancel()
                await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
                self.assertTrue(self.lock.locked())
                self.assertTrue(self.claim_active)
            finally:
                finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertFalse(self.lock.locked())
        self.assertFalse(self.claim_active)


if __name__ == "__main__":
    unittest.main()
