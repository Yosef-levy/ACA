# Mapping `R` to a CEL Declaration Environment

> Companion to [`aca-spec.md`](./aca-spec.md). Defines how the representation
> space `R` of a charter becomes the [CEL](https://github.com/google/cel-spec)
> environment against which every `expr`, `when`, and `precondition` is compiled
> and evaluated.

## 1. Why CEL

ACA does not invent an expression language. Every condition in a charter is a
CEL expression. This buys three things for free:

1. **Type checking.** CEL compilation rejects ill-typed expressions, so "symbol
   used but not declared in `R`" and "comparing a duration to a real" are caught
   at validation time rather than runtime.
2. **A well-specified, side-effect-free evaluation model.** CEL is designed for
   policy/guard expressions: total, terminating, no I/O.
3. **Mature runtimes** (`cel-go`, `cel-java`, `cel-cpp`, `cel-python`).

Operators are CEL, not English: use `&&`, `||`, `!` (never `and`, `or`, `not`).

## 2. Building the environment from `R`

Given a charter's `representation`, construct one CEL environment as follows.

### 2.1 Variables (from `terms`)

Each `terms[i]` becomes a CEL variable declaration `name : T`, where `T` is the
CEL type for its `type`:

| ACA `type` | CEL type |
|------------|----------|
| `real`     | `double` |
| `int`      | `int` |
| `bool`     | `bool` |
| `string`   | `string` |
| `duration` | `google.protobuf.Duration` |
| `timestamp`| `google.protobuf.Timestamp` |
| `enum`     | `string` (value set enforced separately, see §4) |
| `<ns>.<name>` | a CEL **abstract type** named `<ns>.<name>` |

`unit` and `range` are metadata; they do not affect the CEL type (but may be
enforced by L1/L2 checks, see §4).

### 2.2 States (from `states`)

The state machine is exposed to CEL as a single variable:

```text
state : string
```

whose value at evaluation time is the name of the current state. This is why
charters write `state == 'planning'`. A validator MAY additionally restrict the
comparison literals to declared state names.

### 2.3 Events (from `events`)

Events are not variables in the steady-state environment; they are evaluation
*triggers*. When an `expr` must reference data carried by an event, that data is
declared as a `term` and listed in the event's `carries`. Event-scoped
evaluation (e.g. "on `obstacle_detected`, check …") binds those carried terms in
the activation.

### 2.4 Predicates and success (from `predicates`, `success`)

Each `predicates[i]` and `success[i]` is compiled in dependency order and made
available as a **named boolean** that later expressions may reference by name.
Two equivalent implementations are permitted:

- **Inlining**: substitute the predicate's `expr` wherever its name appears.
- **Macro/ident binding**: register the name as a derived `bool` identifier.

Either way, `leg_ok` in one expression resolves to the compiled body of the
`leg_ok` success entry. Definitions must be acyclic.

### 2.5 Standard bindings

The following are always in scope:

- `now : google.protobuf.Timestamp` — evaluation time. (Used e.g. in
  `eta <= duration(deadline - now)`.)
- The CEL standard library macros and functions (`size`, `has`, `duration`,
  `timestamp`, arithmetic, comparison, etc.).

## 3. Worked example

For `nav-planner.aca.yaml`, `R` produces this environment (pseudo-declarations):

```text
// from terms
eta            : google.protobuf.Duration
deadline       : google.protobuf.Timestamp
route_risk     : double
energy_cost    : double
reserve        : double
target_reached : bool
// from states
state          : string
// standard
now            : google.protobuf.Timestamp
```

The predicate and success entries then compile against it:

```cel
// predicate on_time
eta <= duration(deadline - now)

// success leg_ok  (on_time resolved by name)
target_reached && (eta <= duration(deadline - now))
```

And every other expression in the charter must compile in the same environment:

```cel
route_risk <= 0.2          // constraint risk_budget
energy_cost <= reserve     // constraint energy_commitment
state == 'planning'        // decision precondition
route_risk > 0.2           // escalation risk_exceeded
```

## 4. Beyond CEL's type system

CEL checks types but not all ACA metadata. The following are enforced by the
validator, not by CEL:

- **`range`**: e.g. `route_risk` in `[0,1]`. A validator MAY inject implicit
  bounds constraints or check literals against the range.
- **`enum.values`**: comparisons of an enum/`state` variable should only use
  declared values.
- **`unit`**: ACA v0.1 does not perform dimensional analysis; units are
  documentation. A future version may add unit-aware checking.

## 5. Abstract (namespaced) types in v0.1

`geo.region`, `geo.point`, and similar are declared as CEL abstract types. In
v0.1 they are **opaque**: expressions may bind, pass, and test them for equality,
but field access (`p.lat`) is not available because no schema is attached. A
future version may back abstract types with protobuf messages to enable field
access inside expressions.

## 6. Delegation escalation conditions and refinement assumptions

`delegation[].escalation_conditions` and `delegation[].refinement.assume` are a
special case: they describe the *child's* reported behaviour, not the parent's
own state. They are therefore type-checked against the parent's `R` **extended
with the delegation's `reporting` symbols**. Reporting symbols not otherwise
declared in the parent's `R` are treated as opaque (`DYN`), because their
concrete types live in the child's `R`, which the parent does not own. (A
reporting symbol that *is* also a declared parent term keeps that term's type,
which is what lets L2 reason numerically about a `refinement.assume` over it.)
All other `expr`/`when`/`precondition` fields use the unextended `R` environment
of §2.

## 7. Reference runtime

The reference validator (`spec/tools/validate.py`) performs L0 structural checks
and L1 local-validity checks. For L1 it embeds a small, dependency-free CEL
*subset* type-checker (`spec/tools/celcheck.py`) that builds the environment of
§2 from `R` and verifies that every expression parses and type-checks. The
checker is intentionally conservative: it reports an error only when confident
(undeclared identifier, a boolean operator on a number, a comparison across
incompatible base types, an invalid enum/state literal), and treats abstract
(namespaced) types as opaque so opaque values never produce false positives.

For full CEL coverage in production tooling, bind `R` to a complete CEL runtime;
`cel-go` is the suggested implementation because of its mature type-checker API.
