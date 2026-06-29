#!/usr/bin/env python3
"""ACA-Spec L0 reference validator.

Performs structural conformance checks (conformance level L0 from aca-spec.md):

  1. Each document validates against its JSON Schema (Charter or System).
  2. Referential integrity: every symbol used in C/D/E/F/X is declared in R.
  3. Object-id resolution: every referenced object id exists in the system.
  4. Each escalation.projection key appears in that rule's `to` list.

L1 (CEL type-checking) and L2 (compositional satisfiability) are out of scope
for this reference tool; see cel-environment.md.

Usage:
    python3 validate.py path/to/system.aca.yaml
    python3 validate.py path/to/charter.aca.yaml [more.aca.yaml ...]

Requires: pyyaml, jsonschema
"""
from __future__ import annotations

import json
import os
import sys

import yaml
from jsonschema import Draft202012Validator

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.join(HERE, "..", "schema")


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_schema(name):
    with open(os.path.join(SCHEMA_DIR, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


CHARTER_SCHEMA = load_schema("charter.schema.json")
SYSTEM_SCHEMA = load_schema("system.schema.json")


def declared_symbols(charter):
    """All names declared in R (terms, states, events, predicates, success)."""
    r = charter.get("representation", {})
    names = set()
    for group in ("terms", "states", "events", "predicates", "success"):
        for item in r.get(group, []):
            names.add(item["name"])
    return names


def schema_errors(doc, schema):
    validator = Draft202012Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        out.append(f"schema: {loc}: {err.message}")
    return out


def check_views(charter, errors):
    """Every symbol in an exposure/projection view must be declared in R."""
    symbols = declared_symbols(charter)
    cid = charter["object"]["id"]
    for exp in charter.get("exposure", []):
        for sym in exp.get("view", []):
            if sym not in symbols:
                errors.append(f"{cid}: exposure to '{exp['to']}' views undeclared symbol '{sym}'")
    for esc in charter.get("escalation", []):
        to = set(esc.get("to", []))
        for target, view in (esc.get("projection") or {}).items():
            if target not in to:
                errors.append(f"{cid}: escalation '{esc['id']}' projects to '{target}' not in its `to` list")
            for sym in view:
                if sym not in symbols:
                    errors.append(f"{cid}: escalation '{esc['id']}' projects undeclared symbol '{sym}'")


def referenced_object_ids(charter):
    """All object ids this charter points at."""
    refs = set()
    for d in charter.get("decisions", []):
        if d.get("approver"):
            refs.add(d["approver"])
    for a in charter.get("actions", []):
        if a.get("target"):
            refs.add(a["target"])
    for c in charter.get("constraints", []):
        refs.add(c["owner"])
        refs.update(c.get("shared_with", []))
    for e in charter.get("exposure", []):
        refs.add(e["to"])
    for f in charter.get("delegation", []):
        refs.update(f.get("to", []))
    for x in charter.get("escalation", []):
        refs.update(x.get("to", []))
    return refs


def validate_charters(charters, errors):
    ids = {c["object"]["id"] for c in charters}
    for c in charters:
        cid = c["object"]["id"]
        for e in schema_errors(c, CHARTER_SCHEMA):
            errors.append(f"{cid}: {e}")
        check_views(c, errors)
        for ref in referenced_object_ids(c):
            if ref not in ids:
                errors.append(f"{cid}: references unknown object id '{ref}'")


def main(argv):
    paths = argv[1:]
    if not paths:
        print(__doc__)
        return 2

    errors = []
    charters = []

    for path in paths:
        doc = load_yaml(path)
        if not isinstance(doc, dict) or "kind" not in doc:
            errors.append(f"{path}: not an ACA document (missing `kind`)")
            continue
        base = os.path.dirname(os.path.abspath(path))
        if doc["kind"] == "System":
            for e in schema_errors(doc, SYSTEM_SCHEMA):
                errors.append(f"{path}: {e}")
            for member in doc.get("objects", []):
                cpath = os.path.normpath(os.path.join(base, member["charter"]))
                charters.append(load_yaml(cpath))
        elif doc["kind"] == "Charter":
            charters.append(doc)
        else:
            errors.append(f"{path}: unknown kind '{doc['kind']}'")

    validate_charters(charters, errors)

    if errors:
        print(f"FAIL ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1

    names = ", ".join(sorted(c["object"]["id"] for c in charters))
    print(f"OK: L0 conformance passed for {len(charters)} charter(s): {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
