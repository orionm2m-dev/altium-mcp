"""Validate local documentation structure without importing or launching Altium."""
import json
from pathlib import Path
import re
from urllib.parse import urlparse


def main():
    root = Path(__file__).resolve().parent
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    sources = {source["id"]: source for source in manifest["sources"]}
    assert len(sources) == len(manifest["sources"]), "Duplicate source ID"
    seen = set()
    for source in sources.values():
        for key in ("url", "final_url"):
            parsed = urlparse(source[key])
            assert parsed.scheme == "https" and parsed.hostname == "www.altium.com"
        assert re.fullmatch(r"[0-9a-f]{64}", source["response_sha256"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["retrieved"])
    for entry in catalog["entries"]:
        key = (entry["interface"].lower(), entry["member"].lower())
        assert key not in seen, f"Duplicate member: {key}"
        seen.add(key)
        source = sources[entry["source_id"]]
        assert entry["source_url"] == source["url"]
        assert entry["retrieved"] == source["retrieved"]
        assert entry["status"] == "DOCUMENTED"
        assert entry["runtime_verified"] is False
        assert entry["declaring_interface"] == entry["interface"]
        assert entry["access"] in {"read", "write", "read_write", "unspecified", "not_applicable"}
        declaration = entry["signature"]
        if declaration is None:
            assert entry["evidence_kind"] in {"documented_usage", "documented_example"}
            assert entry.get("example_call")
        else:
            assert entry["evidence_kind"] == "declaration"
            assert re.match(r"(?:Function|Procedure|Property)\s+" + re.escape(entry["member"]) + r"\b", declaration, re.I)
            if entry["member_kind"] == "property":
                read = bool(re.search(r"\bRead\b", declaration, re.I))
                write = bool(re.search(r"\bWrite\b", declaration, re.I))
                expected = "read_write" if read and write else "read" if read else "write" if write else "unspecified"
                assert entry["access"] == expected, key
    for page in root.glob("*.md"):
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", page.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            assert (page.parent / path).exists(), f"Broken local link: {page.name}: {target}"
    print(f"Validated {len(seen)} documented entries, {len(sources)} official sources and local links.")
    print("No runtime availability, native behavior or source-content freshness was tested.")


if __name__ == "__main__":
    main()
