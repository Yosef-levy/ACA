# ACA-Spec v0.1 — Agent Charter Abstraction Specification

> Status: Draft (v0.1)
> Companion theory: [`../hierarchical_autonomy_abstraction.md`](../hierarchical_autonomy_abstraction.md)

## Abstract

ACA defines a generic, machine-readable **charter** for autonomous agents
operating inside hierarchical or graph-structured systems. A charter specifies
what an agent **represents**, **decides**, **does**, is **constrained by**,
**exposes**, **delegates**, and **escalates**.

Formally, an ACA charter describes one **autonomy object**:

```text
o = (R, D, A, C, E, F, X)
```

| Symbol | Block            | Meaning |
|--------|------------------|---------|
| `R`    | `representation` | Vocabulary: terms, states, events, predicates, success conditions |
| `D`    | `decisions`      | Choices the object may make |
| `A`    | `actions`        | Actions the object may execute, request, block, delegate, or escalate |
| `C`    | `constraints`    | Limits under which decisions and actions remain valid |
| `E`    | `exposure`       | Target-specific projections of `R` to other objects |
| `F`    | `delegation`     | Refinement of commitments into child obligations |
| `X`    | `escalation`     | When and to whom hidden distinctions must be exposed |

A system of objects is described by a separate manifest:

```text
S = (Obj, H, K)
```

where `Obj` is the set of objects, `H` the hierarchical (delegation) edges, and
`K` the lateral (shared-dependency) edges. `H` and `K` are **derived** from the
charters, never declared independently.

## 0. Design principles

1. **Charter is not the agent.** A charter *places* an agent into a role inside
   one system. It is separable from the agent's own self-description (e.g. an
   A2A agent card). The same agent may hold different charters in different
   systems. The optional `object.binds_agent` field is the bridge to A2A.
2. **Everything references `R`.** `R` is the single source of vocabulary. `C`,
   `D`, `E`, `F`, and `X` may only reference symbols declared in `R`. This is
   what makes abstraction validity checkable.
3. **Authority is not a primitive.** There is no `authority` field. Authority is
   expressed through `decisions.locality` plus the constraints that govern a
   decision or action.
4. **Exposure is projection, not access.** `E` maps a *target role* to a *view*
   (a subset of `R`), not a single generic public interface.
5. **Three representations must agree.** The canonical authoring form is YAML;
   validation is via JSON Schema (`schema/charter.schema.json`,
   `schema/system.schema.json`); the formal model is the tuple above.

## 1. Document model

Every charter is one YAML (or JSON) document with these top-level keys:

```yaml
aca_version: "0.1"
kind: "Charter"
object: { id, role, binds_agent? }
representation: { ... }   # R  (required)
decisions:      [ ... ]   # D  (optional)
actions:        [ ... ]   # A  (optional)
constraints:    [ ... ]   # C  (optional)
exposure:       [ ... ]   # E  (optional)
delegation:     [ ... ]   # F  (optional)
escalation:     [ ... ]   # X  (optional)
```

Only `aca_version`, `kind`, `object`, and `representation` are required; an
object with no decisions/actions is a pure representation boundary.

### 1.1 Object header

```yaml
object:
  id: "nav-planner"            # unique within the system; matches ^[a-zA-Z][a-zA-Z0-9_-]*$
  role: "Navigation Planner"
  binds_agent:                 # optional A2A bridge
    a2a_card: "https://.../agent-card.json"
    agent_id: "drone-7/nav"
```

A2A answers *who an agent is and what it can do*. The charter answers *what this
system makes it responsible for*.

## 2. `R` — Representation space

`R` declares the object's complete vocabulary. No symbol may be used elsewhere
in the charter unless it is declared here.

```yaml
representation:
  terms:        # typed quantities the object can express
    - { name: "eta", type: "duration", unit: "s" }
    - { name: "route_risk", type: "real", range: [0, 1] }
    - { name: "mode", type: "enum", values: ["a", "b"] }
  states:       # named discrete situations (a finite mode set)
    - { name: "planning" }
  events:       # things observed or emitted, optionally carrying terms
    - { name: "obstacle_detected", carries: ["route_risk"] }
  predicates:   # reusable named CEL conditions
    - { name: "on_time", expr: "eta <= duration(deadline - now)" }
  success:      # what "commitment satisfied" means for THIS object
    - { name: "leg_ok", expr: "target_reached && on_time" }
```

### 2.1 Type registry

`type` is one of the base types or a namespaced abstract type:

| Type        | CEL mapping                 |
|-------------|-----------------------------|
| `real`      | `double`                    |
| `int`       | `int`                       |
| `bool`      | `bool`                      |
| `string`    | `string`                    |
| `duration`  | `google.protobuf.Duration`  |
| `timestamp` | `google.protobuf.Timestamp` |
| `enum`      | `string` constrained by `values` |
| `<ns>.<name>` (e.g. `geo.region`) | CEL **abstract** type (opaque in v0.1) |

Namespaced abstract types are opaque in v0.1: they may be passed and compared by
identity but have no introspectable fields. See
[`cel-environment.md`](./cel-environment.md).

### 2.2 The expression language

All `expr`, `when`, and `preconditions` fields are
[CEL](https://github.com/google/cel-spec) expressions. Operators are CEL
(`&&`, `||`, `!`), not English words. The declaration environment is built from
`R` as described in `cel-environment.md`.

## 3. `D` — Decision space

A decision is a choice point. `locality` is where autonomy lives.

```yaml
decisions:
  - id: "choose_route"
    description: "Select among feasible routes."
    options: { type: "set", over: "route" }   # set | enum | interval | bool
    locality: "local"                          # local | approval | shared
    approver: "mission-planner"                # required iff locality == approval
    preconditions: ["state == 'planning'"]
    governed_by: ["risk_budget", "energy_commitment"]   # -> constraint ids
```

- `locality: local` — the object resolves the trade-off without exposing it upward.
- `locality: approval` — requires consent from `approver` (an object id).
- `locality: shared` — co-decided with lateral peers.
- `governed_by` lists the constraints that decide whether a chosen option is valid.

## 4. `A` — Action space

Actions are what an object can **cause**. Decision ownership and action
capability are distinct; an object may own a decision without performing the
action, and vice versa.

```yaml
actions:
  - id: "send_waypoints"
    mode: "execute"          # execute | request | block | delegate | escalate
    effect: "emit waypoints to the motion controller"
    target: "motion-controller"           # optional object id
    realizes_decision: "choose_route"     # optional link D -> A
    governed_by: ["safety_invariant"]     # optional
```

`mode` encodes the action's relationship to other objects. `target` is omitted
for actions on the environment (e.g. physical actuation).

## 5. `C` — Constraint space

Each constraint carries a `scope` distinguishing local, inherited, and shared
validity.

```yaml
constraints:
  - id: "risk_budget"
    expr: "route_risk <= 0.2"
    scope: "local"           # local | inherited | shared
    owner: "nav-planner"
    severity: "hard"         # hard | soft
  - id: "energy_commitment"
    expr: "energy_cost <= reserve"
    scope: "shared"
    owner: "energy-manager"
    shared_with: ["nav-planner"]   # required iff scope == shared
  - id: "deadline_commitment"
    expr: "on_time"
    scope: "inherited"
    owner: "mission-planner"
```

- `local` — owned and checkable here.
- `inherited` — received through a delegation edge (`F`); `owner` is the parent.
- `shared` — a lateral (`K`) dependency; `owner` is elsewhere, `shared_with`
  lists the bound objects.

## 6. `E` — Exposure functions

Each entry is a projection to one target role.

```yaml
exposure:
  - to: "mission-planner"
    view: ["eta", "leg_ok"]    # subset of R; every symbol must be declared in R
    trigger: "on_change"       # periodic | on_change | on_request
  - to: "safety-monitor"
    view: ["route_risk"]
    trigger: "periodic"
    period: "1s"               # required iff trigger == periodic
```

Different targets receive different views of the same internal state; a single
generic interface is insufficient.

## 7. `F` — Refinement / delegation relation

Delegation transfers a constrained objective across a semantic boundary, and is
one-to-many.

```yaml
delegation:
  - delegates: "leg_ok"             # a success/commitment symbol in R
    to: ["motion-controller"]       # child object id(s); defines H edges
    objective: "follow the waypoint corridor"
    authority_boundary:
      may_decide: ["choose_velocity_profile"]
      may_not: ["change_target"]
    reporting: ["tracking_error", "position"]   # expected exposure from child
    escalation_conditions: ["tracking_error > 0.5"]
    refinement:                                 # optional; how the child discharges it
      assume: ["tracking_error <= corridor_width"]
```

The union of all `to` references across the system defines `H`.

### 7.1 `refinement.assume` — discharging the commitment

`delegates` names a commitment the parent owns; the child realizes it in its own
representation space. `refinement.assume` records the guarantees the parent
*relies on* the delegated subgraph to provide, written in the parent's `R`
extended with the delegation's `reporting` symbols (the same environment as
`escalation_conditions`; see `cel-environment.md` §6). The intended reading is a
proof obligation:

```text
(conjunction of assume)  ⟹  delegates
```

i.e. if the child delivers the assumed guarantees, the parent's commitment holds.
L2 discharges this obligation where it can (§10). `assume` is optional; omit it
when the refinement is not yet formalized.

## 8. `X` — Escalation policy

Escalation is abstraction failure: a hidden distinction must become visible.

```yaml
escalation:
  - id: "risk_exceeded"
    when: "route_risk > 0.2"
    to: ["safety-monitor", "mission-planner"]
    projection:                       # per-target view; keys must appear in `to`
      safety-monitor: ["route_risk"]
      mission-planner: ["eta", "leg_ok"]
    action: "request_help"            # request_help | transfer_authority | stop | expose
```

`to` is a list and `projection` is per-target: one underlying problem may reach
several objects, each receiving a different projection.

## 9. System composition — `S = (Obj, H, K)`

```yaml
aca_version: "0.1"
kind: "System"
id: "drone-mission-7"
objects:
  - { charter: "./mission-planner.aca.yaml" }
  - { charter: "./nav-planner.aca.yaml" }
  - { charter: "./motion-controller.aca.yaml" }
  - { charter: "./energy-manager.aca.yaml" }
  - { charter: "./human-supervisor.aca.yaml" }
```

The manifest lists membership only. Edges are derived:

- `H` (hierarchical) = all `delegation.to` references.
- `K` (lateral) = all `constraints[scope == shared].shared_with` pairs.

## 10. Conformance levels

A conforming validator checks, in increasing depth:

### L0 — Structural
- Document validates against the JSON Schema.
- Every symbol used in `C/D/E/F/X` is declared in `R` (referential integrity).
- Every object id referenced (`to`, `approver`, `target`, `owner`,
  `shared_with`, delegation `to`, escalation `to`) resolves within the system.
- Every `escalation.projection` key appears in that rule's `to` list.

### L1 — Local validity
- Every `expr`, `when`, and `precondition` compiles and type-checks against the
  `R` CEL environment. (`delegation.escalation_conditions` are checked against
  `R` extended with that delegation's `reporting` symbols; see
  `cel-environment.md` §6.)
- `state == '...'` and enum comparisons reference only declared state/enum values.
- `predicate`/`success` definitions are acyclic.
- `decisions[].governed_by` / `actions[].governed_by` resolve to a constraint in
  the same charter, and `actions[].realizes_decision` resolves to a decision in
  the same charter.
- Each `success` condition is expressible from `R`.

The reference validator (`spec/tools/validate.py`, default `--level L1`)
implements the checks above. The remaining L1 ambitions below require a solver
and are deferred to L2 tooling:

- Each `decision` has at least one option capable of satisfying its
  `governed_by` constraints (satisfiability).

### L2 — Compositional validity

The full L2 ambition is:

- The conjunction of all `hard` `inherited` + `shared` constraints over shared
  terms is satisfiable (no two hard constraints mutually contradict).
- For each delegated commitment in `F`, the union of children's exposed
  commitments can entail the parent's `success` condition.
- Every `inherited` constraint traces to a real delegation edge from its
  declared `owner`.

CEL evaluates; it does not solve. General L2 satisfiability requires either an
SMT backend or restriction to a decidable fragment.

The reference validator (`spec/tools/validate.py --level L2`, backed by
`spec/tools/l2check.py`) implements a **conservative prototype** over a decidable
fragment:

- **Hard-constraint satisfiability.** For each object, the conjunction of every
  `severity: hard` constraint that governs it (any scope) plus its terms' numeric
  `range` bounds must be satisfiable. Satisfiability is decided by
  Fourier–Motzkin elimination over the rationals — complete for linear real
  arithmetic. Integer terms are relaxed to reals (sound for UNSAT). Everything
  outside the linear fragment (`||`, `!=`, non-linear terms, durations/
  timestamps, opaque abstract types) is **dropped** from the modelled system.
  Dropping conjuncts only weakens the system, so a reported "unsatisfiable" is
  sound for the full system; the cost is that some real contradictions are left
  unflagged rather than producing false positives (the same conservative stance
  as the L1 checker).
- **Inherited-constraint provenance.** Every `scope: inherited` constraint names
  an `owner`; that owner must declare a delegation edge (`F`) to this object.
- **Delegation reporting coverage.** Each `delegation` entry's `reporting`
  symbols must be exposed by the child back to the parent — i.e. the child has an
  `exposure` entry with `to: <parent>` whose `view` covers them. Without this the
  parent cannot observe the commitment it delegated.
- **Delegation refinement entailment.** Where a delegation supplies
  `refinement.assume` (§7.1), the assumed guarantees must entail the parent's
  `delegates` commitment: `assume ∧ ¬delegates` must be unsatisfiable. Entailment
  is decided in the same linear fragment as the satisfiability check. To stay
  sound it runs only when the assumptions are modelled *exactly* (no `||`, `!=`,
  non-linear, or opaque conjuncts dropped); otherwise the obligation is left
  unverified rather than producing a false positive. A reported failure is a
  genuine counterexample — an assignment that satisfies `assume` yet breaks the
  commitment.

The remaining L2 ambition — entailment of *boolean* or otherwise non-linear
commitments, and satisfiability outside the linear fragment — requires a richer
solver and is not yet implemented. For systems that pass L1 but fail L2, see
[`examples/invalid/over-budget/`](../examples/invalid/over-budget/) (satisfiability)
and [`examples/invalid/under-refined/`](../examples/invalid/under-refined/)
(refinement entailment).

## 11. File conventions

| Artifact | Path | `kind` |
|----------|------|--------|
| Charter  | `*.aca.yaml` | `Charter` |
| System   | `system.aca.yaml` | `System` |
| Charter schema | `spec/schema/charter.schema.json` | — |
| System schema  | `spec/schema/system.schema.json` | — |

## 12. Relation to A2A

A2A is an acquaintance and communication protocol: it lets one agent discover
another's identity, skills, formats, and security requirements. ACA is
orthogonal and complementary: it defines the *role* a system assigns an agent —
its representation, constraints, the commitments it must preserve, what it may
decide or do locally, what it must report, and when it must escalate. The
`object.binds_agent` field links a charter to the A2A identity it governs.
