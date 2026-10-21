"""Offline Situs documentation and reviewed capabilities; never dispatches work."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

REFERENCE_DIR = Path(__file__).resolve().parents[1] / "docs" / "situs-reference"
STATUSES = frozenset({"available", "partial", "blocked", "missing", "probe_only"})
DOCUMENTATION_BOUNDARY = (
    "Documentation only. Presence in this reference does not qualify a command, "
    "setting or API on the installed native runtime."
)


def _base():
    return {
        "no_new_dispatch": True,
        "live_qualification_performed": False,
        "documentation_is_runtime_allowlist": False,
        "absence_means": "UNKNOWN",
        "capability_snapshot_is_live_state": False,
    }


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must be a list of nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    return value


def _read(name):
    raw = (REFERENCE_DIR / name).read_bytes()
    return json.loads(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()


def _links(source_ids, sources):
    ids = _strings(source_ids, "source_ids")
    if any(source_id not in sources for source_id in ids):
        raise ValueError("source_ids contains an unknown source")
    return [dict(sources[source_id]) for source_id in ids]


def _load():
    catalog, catalog_hash = _read("catalog.json")
    matrix, matrix_hash = _read("capabilities.json")
    source_data, source_hash = _read("sources.json")
    if not isinstance(catalog, dict) or type(catalog.get("schema_version")) is not int or catalog.get("schema_version") != 1:
        raise ValueError("catalog must be a schema_version 1 object")
    if not isinstance(matrix, dict) or type(matrix.get("schema_version")) is not int or matrix.get("schema_version") != 1:
        raise ValueError("capabilities must be a schema_version 1 object")
    source_rows = source_data.get("sources") if isinstance(source_data, dict) else source_data
    if not isinstance(source_rows, list):
        raise ValueError("sources must be a list or an object containing a sources list")
    sources = {}
    for source in source_rows:
        if not isinstance(source, dict):
            raise ValueError("each source must be an object")
        source_id = _text(source.get("id"), "source.id")
        _text(source.get("title"), "source.title")
        url = _text(source.get("url"), "source.url")
        if not url.startswith(("https://", "http://")):
            raise ValueError("source.url must be an HTTP(S) URL")
        if source_id in sources:
            raise ValueError("duplicate source id")
        sources[source_id] = source
    entries = catalog.get("entries")
    capabilities = matrix.get("capabilities")
    if not isinstance(entries, list) or not isinstance(capabilities, list):
        raise ValueError("entries and capabilities must be lists")
    definitions = matrix.get("status_definitions")
    if not isinstance(definitions, dict) or set(definitions) != STATUSES:
        raise ValueError("status_definitions must define the five capability statuses")
    for key, definition in definitions.items():
        _text(definition, f"status_definitions.{key}")
    for key in ("checked_commit", "checked_utc", "checked_runtime_version", "runtime_verified_definition"):
        _text(matrix.get(key), key)
    documents = []
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each catalog entry must be an object")
        for key in ("id", "name", "kind", "summary"):
            _text(entry.get(key), f"entry.{key}")
        if entry["id"] in ids:
            raise ValueError("duplicate documentation id")
        ids.add(entry["id"])
        if entry.get("status", "DOCUMENTED") != "DOCUMENTED" or entry.get("runtime_verified", False) is not False:
            raise ValueError("documentation cannot promote runtime availability")
        boundary = entry.get("qualification_boundary", DOCUMENTATION_BOUNDARY)
        _text(boundary, "entry.qualification_boundary")
        documents.append(dict(entry, reference_type="documentation", status="DOCUMENTED",
                              runtime_verified=False, qualification_boundary=boundary,
                              sources=_links(entry.get("source_ids"), sources)))
    ids = set()
    reviewed = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ValueError("each capability must be an object")
        for key in ("id", "name", "qualification_boundary", "checked_commit", "checked_runtime_version"):
            _text(capability.get(key), f"capability.{key}")
        if capability["id"] in ids:
            raise ValueError("duplicate capability id")
        ids.add(capability["id"])
        if capability.get("status") not in STATUSES:
            raise ValueError("unknown capability status")
        if not isinstance(capability.get("runtime_verified"), bool):
            raise ValueError("capability.runtime_verified must be boolean")
        for key in ("registered_tools", "supported_actions", "native_qualified_actions", "evidence_ids"):
            _strings(capability.get(key), f"capability.{key}")
        if capability["runtime_verified"] and not capability["native_qualified_actions"]:
            raise ValueError("runtime_verified requires named native qualification observations")
        reviewed.append(dict(capability, reference_type="capability",
                             sources=_links(capability.get("source_ids", []), sources)))
    metadata = {
        "checked_commit": matrix["checked_commit"],
        "checked_utc": matrix["checked_utc"],
        "checked_runtime_version": matrix["checked_runtime_version"],
        "review_scope": matrix.get("scope"),
        "review_status": matrix.get("status"),
        "status_definitions": definitions,
        "runtime_verified_definition": matrix["runtime_verified_definition"],
        "source_sha256": {"catalog.json": catalog_hash, "capabilities.json": matrix_hash,
                          "sources.json": source_hash},
        "last_review_is_not_live_qualification": True,
    }
    return documents, reviewed, sources, metadata


def _failure(exc):
    return dict(_base(), success=False, error=f"Situs reference unavailable: {exc}", matches=[])


def _tokens(query):
    return re.findall(r"\w+", query.casefold())


def search(query, limit=10):
    """Search local settings and reviewed support without checking or opening Altium."""
    if not isinstance(query, str) or not query.strip() or len(query) > 256 or not _tokens(query):
        return dict(_base(), success=False, error="query must contain search terms and be at most 256 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        return dict(_base(), success=False, error="limit must be an integer from 1 to 50")
    try:
        documents, capabilities, sources, metadata = _load()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        return _failure(exc)
    terms = _tokens(query)
    found = []
    for entry in documents + capabilities:
        searchable = json.dumps(entry, ensure_ascii=False).casefold()
        if all(term in searchable for term in terms):
            found.append(entry)
    normalized = query.strip().casefold()
    found.sort(key=lambda entry: (
        -(normalized == entry["id"].casefold()), -(normalized == entry["name"].casefold()),
        -sum(term in entry["name"].casefold() for term in terms),
        entry["id"], entry["reference_type"],
    ))
    results = found[:limit]
    return dict(_base(), success=True, query=query.strip(), review=metadata,
                total_matches=len(found), returned=len(results), truncated=len(found) > limit,
                matches=results,
                limitations=["Documentation and implementation review do not change capability status.",
                             "Runtime observations apply only to their named actions and qualification boundaries."])


def get_capabilities():
    """Return the complete saved review, not a runtime availability probe."""
    try:
        documents, capabilities, sources, metadata = _load()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        return _failure(exc)
    counts = Counter(entry["status"] for entry in capabilities)
    return dict(_base(), success=True, review=metadata, capability_count=len(capabilities),
                counts={status: counts[status] for status in sorted(STATUSES)},
                capabilities=capabilities, sources=list(sources.values()),
                documentation_entry_count=len(documents))
