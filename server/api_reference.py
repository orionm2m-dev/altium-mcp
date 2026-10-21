"""Offline access to curated API documentation, never a runtime allowlist."""
import hashlib
import json
from pathlib import Path
import re

REFERENCE_DIR = Path(__file__).resolve().parents[1] / "docs" / "altium-api-reference"
NEGATIVE_CATALOG = Path(__file__).with_name("delphiscript_compatibility.json")


def _load():
    catalog_path = REFERENCE_DIR / "catalog.json"
    raw = catalog_path.read_bytes()
    catalog = json.loads(raw.decode("utf-8-sig"))
    sources = json.loads((REFERENCE_DIR / "sources.json").read_text("utf-8-sig"))
    negative = json.loads(NEGATIVE_CATALOG.read_text("utf-8-sig"))
    if not all(isinstance(value, dict) for value in (catalog, sources, negative)):
        raise ValueError("reference files must contain JSON objects")
    if not isinstance(catalog.get("entries"), list) or not isinstance(sources.get("sources"), list):
        raise ValueError("catalog entries and sources must be lists")
    if not all(isinstance(source, dict) for source in sources["sources"]):
        raise ValueError("source entries must be JSON objects")
    source_map = {source["id"]: source for source in sources["sources"]}
    entries = []
    for entry in catalog["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("catalog entries must be JSON objects")
        if (entry.get("status") != "DOCUMENTED" or entry.get("runtime_verified") is not False
                or not isinstance(entry.get("interface"), str) or not isinstance(entry.get("member"), str)
                or entry.get("signature") is not None and not isinstance(entry["signature"], str)):
            raise ValueError("reference entry violates the documented/unverified contract")
        source = source_map[entry["source_id"]]
        if not isinstance(entry.get("source_url"), str) or not isinstance(source.get("title"), str):
            raise ValueError("reference entry lacks a source URL or title")
        entries.append(dict(entry, source_title=source["title"]))
    rules = negative["incompatible_members"]
    if not isinstance(rules, list):
        raise ValueError("incompatible_members must be a list")
    if not all(isinstance(rule, dict) and isinstance(rule.get("receiver_type"), str)
               and isinstance(rule.get("member"), str) for rule in rules):
        raise ValueError("incompatibility entries require receiver_type and member")
    return entries, rules, catalog.get("edition"), hashlib.sha256(raw).hexdigest()


def _tokens(query):
    return re.findall(r"[\w]+", query.casefold())


def _matches(terms, values):
    text = " ".join(str(value or "") for value in values).casefold()
    return all(term in text for term in terms)


def _negative_for(entry, rules):
    return [rule for rule in rules if rule["receiver_type"].casefold() == entry["interface"].casefold()
            and rule["member"].casefold() == entry["member"].casefold()]


def search(query, limit=10):
    """Search interface/member/signature/source title; no Altium calls or writes."""
    base = dict(no_new_dispatch=True, runtime_verified=False,
                documentation_is_runtime_allowlist=False,
                negative_catalog_precedence="Observed incompatibilities take precedence over documentation for the exact receiver/member. The current linter does not automatically detect runtime versions.",
                absence_means="UNKNOWN")
    if not isinstance(query, str) or not query.strip() or len(query) > 256:
        return dict(base, success=False, error="query must be nonempty text of at most 256 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        return dict(base, success=False, error="limit must be an integer from 1 to 50")
    terms = _tokens(query)
    if not terms:
        return dict(base, success=False, error="query must contain an interface, member or search term")
    try:
        entries, rules, edition, digest = _load()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        return dict(base, success=False, error=f"API reference unavailable: {exc}", matches=[])
    found = [entry for entry in entries if _matches(terms, [entry["interface"], entry["member"],
                entry.get("signature"), entry["source_title"], entry.get("source_heading")])]
    normalized = query.strip().casefold()
    def rank(entry):
        interface, member = entry["interface"].casefold(), entry["member"].casefold()
        return (-(normalized == interface + "." + member), -(normalized == member),
                -sum(term in member for term in terms), -sum(term in interface for term in terms), interface, member)
    found.sort(key=rank)
    results = [dict(entry, observed_incompatibilities=_negative_for(entry, rules)) for entry in found[:limit]]
    negative_matches = [rule for rule in rules if _matches(terms, [rule["receiver_type"], rule["member"], rule.get("reason")])]
    return dict(base, success=True, query=query.strip(), catalog_edition=edition, catalog_sha256=digest,
                total_matches=len(found), returned=len(results), truncated=len(found)>limit,
                matches=results, observed_incompatibilities=negative_matches,
                limitations=["Documentation is partial and historical; no native compilation or execution was performed.",
                             "No inferred inheritance, overload, argument or getter/setter runtime compatibility."])


def annotate_findings(findings):
    """Optional citations only. Never change a lint finding's severity or verdict."""
    try:
        entries, rules, edition, digest = _load()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        return dict(available=False, error=f"Optional API references unavailable: {exc}")
    count = 0
    for finding in findings:
        receiver, member = finding.get("receiver_type"), finding.get("member")
        if not receiver or not member:
            continue
        matches = [dict(entry, observed_incompatibilities=_negative_for(entry, rules)) for entry in entries
                   if entry["interface"].casefold() == receiver.casefold()
                   and entry["member"].casefold() == member.casefold()]
        if matches:
            finding["documentation"] = matches
            count += 1
    return dict(available=True, catalog_edition=edition, catalog_sha256=digest,
                findings_with_references=count, changes_lint_verdict=False, runtime_verified=False)
