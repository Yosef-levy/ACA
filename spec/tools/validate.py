#!/usr/bin/env python3
"""ACA-Spec reference validator (conformance levels L0 and L1).

L0 — Structural (from aca-spec.md):

  1. Each document validates against its JSON Schema (Charter or System).
  2. Referential integrity: every symbol used in C/D/E/F/X is declared in R.
  3. Object-id resolution: every referenced object id exists in the system.
  4. Each escalation.projection key appears in that rule's `to` list.

L1 — Local validity (from aca-spec.md):

  1. Every `expr`, `when`, and `precondition` parses and type-checks against the
     CEL environment built from the charter's representation space R
     (see cel-environment.md). This catches symbols used inside expressions but
     not declared in R, and confident type errors (e.g. a boolean operator on a
     number, or a comparison across incompatible base types).
  2. `state == '...'` and enum comparisons use only declared state/enum values.
  3. predicate/success definitions are acyclic.
  4. `decisions[].governed_by` and `actions[].governed_by` resolve to a
     constraint declared in the same charter; `actions[].realizes_decision`
     resolves to a decision in the same charter.

L2 — Compositional validity (prototype; see l2check.py and aca-spec.md §10):

  1. For each object, the conjunction of its `hard` constraints (any scope) plus
     its terms' `range` bounds is satisfiable over the modelled linear fragment.
  2. Every `inherited` constraint traces to a delegation edge from its `owner`.

L2 is a conservative prototype over a decidable linear fragment, not a full SMT
backend; it reports a contradiction only when it can prove one. See aca-spec.md
§10 for the full L2 ambition.

Usage:
    python3 validate.py path/to/system.aca.yaml                # default: L1
    python3 validate.py --level L2 path/to/system.aca.yaml     # + compositional
    python3 validate.py path/to/charter.aca.yaml [more.aca.yaml ...]
    python3 validate.py --level L0 path/to/system.aca.yaml     # structural only

Requires: pyyaml, jsonschema
"""
from __future__ import annotations

import json
import os
import sys

import yaml
from jsonschema import Draft202012Validator

import celcheck
import l2check

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


def iter_expressions(charter):
    """Yield (label, expr) for every CEL expression field in a charter."""
    r = charter.get("representation", {})
    for group in ("predicates", "success"):
        for item in r.get(group, []):
            yield f"{group} '{item['name']}'", item["expr"]
    for c in charter.get("constraints", []):
        yield f"constraint '{c['id']}'", c["expr"]
    for d in charter.get("decisions", []):
        for k, pre in enumerate(d.get("preconditions", [])):
            yield f"decision '{d['id']}' precondition[{k}]", pre
    for x in charter.get("escalation", []):
        yield f"escalation '{x['id']}' when", x["when"]


def check_expressions(charter, errors):
    """L1: every CEL expression parses and type-checks against R."""
    cid = charter["object"]["id"]
    env = celcheck.build_env(charter)
    for label, expr in iter_expressions(charter):
        for msg in celcheck.check_expr(expr, env):
            errors.append(f"{cid}: {label}: {msg}")

    # delegation.escalation_conditions watch the *child's* reported telemetry,
    # so they evaluate in the parent's R extended with the delegation's
    # `reporting` symbols (typed opaquely; their real types live in the child's R).
    for f in charter.get("delegation", []):
        env2 = celcheck.Env(
            vars=dict(env.vars),
            states=set(env.states),
            enums=dict(env.enums),
        )
        for sym in f.get("reporting", []):
            env2.vars.setdefault(sym, celcheck.DYN)
        for k, cond in enumerate(f.get("escalation_conditions", [])):
            label = f"delegation '{f['delegates']}' escalation_condition[{k}]"
            for msg in celcheck.check_expr(cond, env2):
                errors.append(f"{cid}: {label}: {msg}")


def check_definition_cycles(charter, errors):
    """L1: predicate/success bodies must not reference each other cyclically."""
    cid = charter["object"]["id"]
    r = charter.get("representation", {})
    defined = {}
    for group in ("predicates", "success"):
        for item in r.get(group, []):
            defined[item["name"]] = item["expr"]

    def refs(expr):
        try:
            ast = celcheck.parse(expr)
        except celcheck.CelError:
            return set()
        names = set()
        _collect_idents(ast, names)
        return names & set(defined)

    deps = {name: refs(expr) for name, expr in defined.items()}

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in defined}

    def visit(n, stack):
        color[n] = GRAY
        for m in deps[n]:
            if color[m] == GRAY:
                cycle = " -> ".join(stack[stack.index(m):] + [m])
                errors.append(f"{cid}: cyclic definition: {cycle}")
                return
            if color[m] == WHITE:
                visit(m, stack + [m])
        color[n] = BLACK

    for n in defined:
        if color[n] == WHITE:
            visit(n, [n])


def _collect_idents(node, out):
    if isinstance(node, celcheck.Ident):
        out.add(node.name)
    elif isinstance(node, celcheck.Unary):
        _collect_idents(node.operand, out)
    elif isinstance(node, celcheck.Binary):
        _collect_idents(node.left, out)
        _collect_idents(node.right, out)
    elif isinstance(node, celcheck.Call):
        # the callee in `f(...)` is a function name, not a referenced symbol
        if not isinstance(node.func, celcheck.Ident):
            _collect_idents(node.func, out)
        for a in node.args:
            _collect_idents(a, out)
    elif isinstance(node, celcheck.Member):
        _collect_idents(node.base, out)
    elif isinstance(node, celcheck.Index):
        _collect_idents(node.base, out)
        _collect_idents(node.index, out)


def check_local_references(charter, errors):
    """L1: governed_by / realizes_decision resolve within the same charter."""
    cid = charter["object"]["id"]
    constraint_ids = {c["id"] for c in charter.get("constraints", [])}
    decision_ids = {d["id"] for d in charter.get("decisions", [])}

    for d in charter.get("decisions", []):
        for g in d.get("governed_by", []):
            if g not in constraint_ids:
                errors.append(
                    f"{cid}: decision '{d['id']}' governed_by unknown "
                    f"constraint '{g}'"
                )
    for a in charter.get("actions", []):
        for g in a.get("governed_by", []):
            if g not in constraint_ids:
                errors.append(
                    f"{cid}: action '{a['id']}' governed_by unknown "
                    f"constraint '{g}'"
                )
        rd = a.get("realizes_decision")
        if rd and rd not in decision_ids:
            errors.append(
                f"{cid}: action '{a['id']}' realizes unknown decision '{rd}'"
            )


def validate_charters(charters, errors, level="L1"):
    ids = {c["object"]["id"] for c in charters}
    for c in charters:
        cid = c["object"]["id"]
        schema_issues = schema_errors(c, CHARTER_SCHEMA)
        for e in schema_issues:
            errors.append(f"{cid}: {e}")
        check_views(c, errors)
        for ref in referenced_object_ids(c):
            if ref not in ids:
                errors.append(f"{cid}: references unknown object id '{ref}'")
        # L1 checks operate on expressions; skip if the document is structurally
        # broken, since the schema errors above are the actionable ones.
        if level in ("L1", "L2") and not schema_issues:
            check_expressions(c, errors)
            check_definition_cycles(c, errors)
            check_local_references(c, errors)

    # L2 is compositional: it reasons over the whole set of charters at once,
    # and only when each is at least structurally sound.
    if level == "L2" and not errors:
        l2check.check_system(charters, errors)


def main(argv):
    args = argv[1:]
    level = "L1"
    paths = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--level":
            i += 1
            if i >= len(args) or args[i] not in ("L0", "L1", "L2"):
                print("error: --level requires L0, L1, or L2")
                return 2
            level = args[i]
        elif arg.startswith("--level="):
            level = arg.split("=", 1)[1]
            if level not in ("L0", "L1", "L2"):
                print("error: --level requires L0, L1, or L2")
                return 2
        else:
            paths.append(arg)
        i += 1

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

    validate_charters(charters, errors, level=level)

    if errors:
        print(f"FAIL ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1

    names = ", ".join(sorted(c["object"]["id"] for c in charters))
    print(f"OK: {level} conformance passed for {len(charters)} charter(s): {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
