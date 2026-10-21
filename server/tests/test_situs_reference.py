"""Offline Situs reference contracts and registered public tool integration."""
import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import situs_reference


def fixture_data():
    definitions = {status: f"Reviewed definition: {status}" for status in situs_reference.STATUSES}
    def capability(identifier, name, status, boundary, **extra):
        return dict(id=identifier, name=name, status=status, qualification_boundary=boundary,
                    checked_commit="review-commit", checked_runtime_version="AD 26.9.1",
                    runtime_verified=False, registered_tools=[], supported_actions=[],
                    native_qualified_actions=[], evidence_ids=[], source_ids=["official"], **extra)
    caps = [
        capability("situs.preferred", "Preferred routing directions", "missing", "No dedicated setter is implemented."),
        capability("situs.rip_up", "Rip-up All checkbox", "blocked", "Observed custom checkbox safely refused."),
        capability("situs.stop", "Stop routing", "missing", "PCB:Autoroute Stop is not script debugger Stop."),
        capability("situs.recovery", "Recover script debugger", "partial", "EditScript:Stop is not router Stop."),
        capability("situs.setup", "Open setup", "probe_only", "Generic scripts do not qualify a public typed opener."),
        capability("situs.lookup", "Read local documentation", "available", "Offline utility; no native dispatch."),
    ]
    return {
        "catalog.json": {"schema_version": 1, "entries": [
            dict(id="doc.preferred", name="Preferred routing directions", kind="setting",
                 summary="Specify preferred layer direction in routing setup.", source_ids=["official"]),
            dict(id="doc.stop", name="Stop routing", kind="lifecycle",
                 summary="The router Stop operation is distinct from debugger Stop.", source_ids=["official"]),
        ]},
        "capabilities.json": dict(schema_version=1, checked_commit="review-commit",
                                  checked_utc="2026-10-21T12:00:00Z", checked_runtime_version="AD 26.9.1",
                                  runtime_verified_definition="Only named observed actions are verified.",
                                  status_definitions=definitions, capabilities=caps),
        "sources.json": [dict(id="official", title="Situs Topological Autorouter",
                              url="https://www.altium.com/documentation/altium-designer/pcb/routing/situs-topological-autorouter")],
    }


class FixtureMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = fixture_data()
        self.write_data()
        self.reference_patch = patch.object(situs_reference, "REFERENCE_DIR", self.root)
        self.reference_patch.start()
        self.addCleanup(self.reference_patch.stop)

    def write_data(self):
        for name, value in self.data.items():
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")


class SearchTests(FixtureMixin, unittest.TestCase):
    def test_preferred_returns_documentation_and_missing_capability(self):
        result = situs_reference.search("PrEfErReD")
        self.assertTrue(result["success"])
        self.assertEqual({entry["reference_type"] for entry in result["matches"]}, {"documentation", "capability"})
        by_id = {entry["id"]: entry for entry in result["matches"]}
        self.assertEqual(by_id["situs.preferred"]["status"], "missing")
        self.assertEqual(by_id["doc.preferred"]["status"], "DOCUMENTED")
        self.assertFalse(by_id["doc.preferred"]["runtime_verified"])
        self.assertTrue(all(entry["sources"][0]["url"].startswith("https://www.altium.com/") for entry in by_id.values()))
        self.assertTrue(result["no_new_dispatch"])
        self.assertFalse(result["documentation_is_runtime_allowlist"])

    def test_custom_checkbox_stays_blocked(self):
        result = situs_reference.search("custom checkbox")
        self.assertEqual(result["matches"][0]["id"], "situs.rip_up")
        self.assertEqual(result["matches"][0]["status"], "blocked")
        self.assertIn("refused", result["matches"][0]["qualification_boundary"])

    def test_router_stop_is_not_debugger_stop(self):
        result = situs_reference.search("Stop")
        matches = {entry["id"]: entry for entry in result["matches"]}
        self.assertEqual(matches["situs.stop"]["status"], "missing")
        self.assertEqual(matches["situs.recovery"]["status"], "partial")
        self.assertIn("not router Stop", matches["situs.recovery"]["qualification_boundary"])
        self.assertEqual(matches["doc.stop"]["status"], "DOCUMENTED")

    def test_unknown_is_unknown_not_unsupported(self):
        result = situs_reference.search("not_present_821")
        self.assertTrue(result["success"])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["absence_means"], "UNKNOWN")

    def test_limits_and_total(self):
        result = situs_reference.search("Situs", 1)
        self.assertEqual(result["returned"], 1)
        self.assertGreater(result["total_matches"], 1)
        self.assertTrue(result["truncated"])
        self.assertTrue(situs_reference.search("Situs", 50)["success"])

    def test_invalid_inputs(self):
        for query, limit in [("", 10), ("  ", 10), ("...", 10), ("x" * 257, 10),
                             (None, 10), (2, 10), ("Situs", 0), ("Situs", 51),
                             ("Situs", True), ("Situs", 2.5), ("Situs", "2")]:
            with self.subTest(query=query, limit=limit):
                result = situs_reference.search(query, limit)
                self.assertFalse(result["success"])
                self.assertTrue(result["no_new_dispatch"])

    def test_full_capabilities_counts_and_review_are_historical(self):
        result = situs_reference.get_capabilities()
        self.assertTrue(result["success"])
        self.assertEqual(result["capability_count"], 6)
        self.assertEqual(sum(result["counts"].values()), 6)
        self.assertEqual(result["counts"]["missing"], 2)
        self.assertEqual(result["review"]["checked_commit"], "review-commit")
        self.assertEqual(result["review"]["status_definitions"], self.data["capabilities.json"]["status_definitions"])
        self.assertTrue(result["review"]["last_review_is_not_live_qualification"])
        self.assertFalse(result["capability_snapshot_is_live_state"])
        self.assertFalse(result["live_qualification_performed"])

    def test_reviewed_true_does_not_become_global_runtime_verification(self):
        cap = self.data["capabilities.json"]["capabilities"][3]
        cap["runtime_verified"] = True
        cap["native_qualified_actions"] = ["one exact debugger recovery observation"]
        self.write_data()
        result = situs_reference.search("situs.recovery")
        self.assertTrue(result["matches"][0]["runtime_verified"])
        self.assertEqual(result["matches"][0]["status"], "partial")
        self.assertFalse(result["live_qualification_performed"])

    def test_missing_and_malformed_json_are_errors(self):
        (self.root / "catalog.json").unlink()
        self.assertIn("unavailable", situs_reference.get_capabilities()["error"])
        (self.root / "catalog.json").write_text("{bad", encoding="utf-8")
        result = situs_reference.search("Preferred")
        self.assertFalse(result["success"])
        self.assertEqual(result["matches"], [])

    def test_malformed_catalog_fails_closed(self):
        variants = []
        for name in ("catalog.json", "capabilities.json"):
            invalid = fixture_data()
            invalid[name] = []
            variants.append(invalid)
        invalid = fixture_data()
        invalid["catalog.json"]["entries"][0]["source_ids"] = ["unknown"]
        variants.append(invalid)
        invalid = fixture_data()
        invalid["catalog.json"]["entries"].append(copy.deepcopy(invalid["catalog.json"]["entries"][0]))
        variants.append(invalid)
        invalid = fixture_data()
        invalid["sources.json"].append(copy.deepcopy(invalid["sources.json"][0]))
        variants.append(invalid)
        invalid = fixture_data()
        invalid["capabilities.json"]["capabilities"][0]["status"] = "unsupported"
        variants.append(invalid)
        invalid = fixture_data()
        invalid["capabilities.json"]["capabilities"][0]["runtime_verified"] = True
        variants.append(invalid)
        invalid = fixture_data()
        invalid["catalog.json"]["entries"][0]["runtime_verified"] = True
        variants.append(invalid)
        invalid = fixture_data()
        invalid["catalog.json"]["entries"][0]["status"] = "available"
        variants.append(invalid)
        invalid = fixture_data()
        invalid["sources.json"][0]["url"] = "file:///local"
        variants.append(invalid)
        for index, invalid in enumerate(variants):
            with self.subTest(index=index):
                self.data = invalid
                self.write_data()
                self.assertFalse(situs_reference.search("Preferred")["success"])
                self.assertFalse(situs_reference.get_capabilities()["success"])

    def test_wrapped_source_list_is_supported(self):
        self.data["sources.json"] = {"sources": self.data["sources.json"]}
        self.write_data()
        self.assertTrue(situs_reference.get_capabilities()["success"])

    def test_module_has_only_offline_standard_library_imports(self):
        tree = ast.parse((SERVER / "situs_reference.py").read_text("utf-8-sig"))
        allowed = {"collections", "hashlib", "json", "pathlib", "re"}
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module.split(".")[0])
        self.assertLessEqual(imports, allowed)


class PublicTests(FixtureMixin, unittest.IsolatedAsyncioTestCase):
    async def test_registered_public_calls_are_offline(self):
        main_before = sys.modules.get("main")
        from mcp.server.fastmcp import FastMCP, Context
        tree = ast.parse((SERVER / "main.py").read_text("utf-8"))
        names = {"search_situs_reference", "get_situs_capabilities"}
        nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        server = FastMCP("situs-reference-offline-test")
        env = dict(Context=Context, json=json, mcp=server)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "registered_situs_reference", "exec"), env)
        tools = {tool.name: tool for tool in await server.list_tools()}
        self.assertEqual(set(tools), names)
        self.assertEqual(tools["search_situs_reference"].inputSchema["properties"]["limit"]["type"], "integer")
        found = await server.call_tool("search_situs_reference", {"query": "Preferred", "limit": 2})
        result = json.loads(found[0].text)
        self.assertEqual(result["returned"], 2)
        self.assertTrue(result["no_new_dispatch"])
        full = await server.call_tool("get_situs_capabilities", {})
        result = json.loads(full[0].text)
        self.assertEqual(result["capability_count"], 6)
        self.assertFalse(result["live_qualification_performed"])
        # Neither test nor implementation imports main.py or opens a transport.
        self.assertIs(sys.modules.get("main"), main_before)


if __name__ == "__main__":
    unittest.main()
