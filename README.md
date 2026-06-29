# ACA — Agent Charter Abstraction

ACA is a generic, machine-readable way to describe **autonomous agents inside
hierarchical or graph-structured systems**. It pairs a conceptual model with a
concrete, validatable file format.

The core idea: a hierarchical system is not a stack of layers but a **graph of
autonomy objects**. Each object owns a slice of the world — what it can
represent, what it may decide, what it can do, the constraints it lives under,
what it exposes to whom, what it delegates, and when it must escalate. A system
works when local autonomous choices preserve higher-level commitments.

Formally, each agent is described by one **charter** — an autonomy object:

```text
o = (R, D, A, C, E, F, X)
```

| Symbol | Block            | Meaning |
|--------|------------------|---------|
| `R`    | `representation` | Vocabulary: terms, states, events, predicates, success conditions |
| `D`    | `decisions`      | Choices the object may make (and where that authority lives) |
| `A`    | `actions`        | What it may execute, request, block, delegate, or escalate |
| `C`    | `constraints`    | Limits under which decisions and actions stay valid |
| `E`    | `exposure`       | Target-specific projections of `R` to other objects |
| `F`    | `delegation`     | Refinement of commitments into child obligations |
| `X`    | `escalation`     | When and to whom hidden distinctions must be exposed |

A system manifest then composes objects:

```text
S = (Obj, H, K)
```

where `Obj` is the set of objects, `H` the hierarchical (delegation) edges, and
`K` the lateral (shared-dependency) edges. **`H` and `K` are derived from the
charters, never declared independently.**

## Repository layout

```text
.
├── hierarchical_autonomy_abstraction.md   # The theory: autonomy objects as a graph
├── spec/
│   ├── aca-spec.md                         # ACA-Spec v0.1 — the file format
│   ├── cel-environment.md                  # How R maps to a CEL expression environment
│   ├── schema/
│   │   ├── charter.schema.json             # JSON Schema for a single charter
│   │   └── system.schema.json              # JSON Schema for a system manifest
│   └── tools/
│       ├── validate.py                     # L0 + L1 + L2 reference validator
│       ├── celcheck.py                     # CEL subset type-checker (L1)
│       ├── l2check.py                      # compositional satisfiability + provenance (L2)
│       └── test_*.py                       # unittest suite for the tools
└── examples/
    ├── drone-mission/                      # A complete worked example
    │   ├── system.aca.yaml                 # System manifest (membership only)
    │   ├── mission-planner.aca.yaml
    │   ├── nav-planner.aca.yaml
    │   ├── motion-controller.aca.yaml
    │   ├── energy-manager.aca.yaml
    │   └── human-supervisor.aca.yaml
    └── invalid/
        ├── broken-sensor.aca.yaml          # Passes L0, fails L1 (demo)
        └── over-budget/                    # Passes L1, fails L2 (demo)
```

## Quick start

A charter is a single YAML document. Only `aca_version`, `kind`, `object`, and
`representation` are required; an object with no decisions or actions is a pure
representation boundary.

```yaml
aca_version: "0.1"
kind: "Charter"
object:
  id: "nav-planner"
  role: "Navigation Planner"
representation:
  terms:
    - { name: "eta", type: "duration", unit: "s" }
    - { name: "route_risk", type: "real", range: [0, 1] }
  success:
    - { name: "leg_ok", expr: "target_reached && on_time" }
constraints:
  - { id: "risk_budget", expr: "route_risk <= 0.2", scope: "local", owner: "nav-planner", severity: "hard" }
```

All `expr`, `when`, and `precondition` fields are
[CEL](https://github.com/google/cel-spec) expressions, so use `&&`, `||`, `!`
rather than English operators. Every symbol referenced anywhere in a charter
must be declared in its `representation` block — that single rule is what makes
abstraction validity checkable.

## Validating

The reference validator performs **L0 structural**, **L1 local-validity**, and
**L2 compositional** checks. L0: JSON Schema conformance, referential integrity
(every symbol used is declared in `R`), object-id resolution across the system,
and escalation-projection consistency. L1: every `expr`/`when`/`precondition`
parses and type-checks against the CEL environment built from `R` (catching
symbols used inside expressions but not declared in `R`, plus confident type
errors), enum and state literals are validated, predicate/success definitions are
acyclic, and `governed_by`/`realizes_decision` resolve within the charter. L2:
each object's `hard` constraints (with `range` bounds) are jointly satisfiable,
and every `inherited` constraint traces to a real delegation edge from its
`owner`.

```bash
pip install pyyaml jsonschema

# Validate a whole system at L1 (default; loads every member charter):
python3 spec/tools/validate.py examples/drone-mission/system.aca.yaml

# Add compositional (L2) checks:
python3 spec/tools/validate.py --level L2 examples/drone-mission/system.aca.yaml

# Or validate charters directly:
python3 spec/tools/validate.py examples/drone-mission/*.aca.yaml

# Structural checks only:
python3 spec/tools/validate.py --level L0 examples/drone-mission/system.aca.yaml
```

The L1 type-checker is a small, dependency-free CEL subset checker in
[`spec/tools/celcheck.py`](./spec/tools/celcheck.py); the L2 checker in
[`spec/tools/l2check.py`](./spec/tools/l2check.py) is an equally dependency-free,
conservative satisfiability prototype (Fourier–Motzkin over a linear fragment).
For a deliberately broken charter that passes L0 but fails L1, see
[`examples/invalid/broken-sensor.aca.yaml`](./examples/invalid/broken-sensor.aca.yaml);
for a system that passes L1 but fails L2, see
[`examples/invalid/over-budget/`](./examples/invalid/over-budget/).

## Testing

The tools ship with a `unittest` suite (no extra dependencies beyond the
validator's own):

```bash
python3 -m unittest discover -s spec/tools    # or: python3 -m pytest spec/tools
```

## Conformance levels

| Level | Checks | Tooling |
|-------|--------|---------|
| **L0** Structural | Schema validity, symbol declaration, id resolution | `validate.py` (included) |
| **L1** Local validity | Every expression compiles and type-checks against the `R` CEL environment | `validate.py` (included; `celcheck.py`) |
| **L2** Compositional validity | Each object's hard constraints are jointly satisfiable; inherited constraints trace to a delegation edge | `validate.py --level L2` (included; `l2check.py`, conservative linear-arithmetic prototype) |

## Relation to A2A

ACA is orthogonal and complementary to [A2A](https://github.com/google/A2A). A2A
is an acquaintance protocol — it tells you *who an agent is and what it can do*.
A charter says *what this particular system makes the agent responsible for*:
its role, constraints, the commitments it must preserve, what it may decide
locally, what it reports, and when it must escalate. The optional
`object.binds_agent` field links a charter to the A2A identity it governs.

## Learn more

- [`hierarchical_autonomy_abstraction.md`](./hierarchical_autonomy_abstraction.md) — the conceptual model and motivation.
- [`spec/aca-spec.md`](./spec/aca-spec.md) — the full v0.1 specification.
- [`spec/cel-environment.md`](./spec/cel-environment.md) — how `R` becomes a CEL environment.
- [`examples/drone-mission/`](./examples/drone-mission/) — an end-to-end example with a hierarchical chain, a lateral dependency, and a human escalation loop.

> Status: ACA-Spec is a **v0.1 draft**.
