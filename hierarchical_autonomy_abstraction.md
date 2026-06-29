# A General Abstraction for Hierarchical Systems

## Abstract

Hierarchical systems appear in robotics, software, organizations, networks, compilers, military command structures, and AI agent architectures. They are often described as layers, modules, chains of command, interfaces, contracts, or control loops. These descriptions are useful, but they tend to emphasize one aspect of hierarchy at a time: authority, information hiding, communication, control, or task decomposition.

This article proposes a more general abstraction. A hierarchical system can be understood as a graph of autonomous semantic units, called **autonomy objects**. Each autonomy object owns a representation of the world, a decision space, an action space, a set of constraints, exposure rules, refinement relations, and escalation policies. The purpose of hierarchy is not merely to divide work, but to preserve meaning and responsibility across different levels of abstraction.

The main claim is simple: a hierarchical system works when local autonomous choices preserve higher-level commitments. It fails when hidden lower-level distinctions become relevant to commitments that were supposed to remain abstract.

## 1. The Problem

Complex systems cannot be reasoned about at a single level of detail.

A mission planner should not need to reason in motor voltages. A motor controller should not need to reason in mission goals. A software application should not need to understand packet timing. A network driver should not need to understand user intent. A human commander may express an objective, while subordinate teams decide how to realize it under local conditions.

Hierarchy makes this possible by separating semantic worlds. Each part of the system operates in a vocabulary appropriate to its role. Higher levels express intent, goals, constraints, and commitments. Lower levels refine those commitments into concrete decisions and actions.

However, real hierarchical systems are rarely simple stacks. A single high-level commitment may depend on several lower-level components at once. A navigation object may depend on energy management, safety monitoring, localization, communication, and actuator control. A software agent may delegate to planners, tool executors, policy checkers, memory systems, and external services. These relations are partly hierarchical and partly lateral.

The right abstraction is therefore not a linear stack of layers. It is a graph of semantic boundaries.

## 2. Autonomy Objects

The primitive unit of the proposed model is the **autonomy object**.

An autonomy object is not an object in the object-oriented programming sense. It is a semantic unit of autonomy: a boundary within which some representation, decisions, actions, constraints, and responsibilities are locally owned.

An autonomy object can be described as:

```text
o = (R, D, A, C, E, F, X)
```

where:

- `R` is the representation space: the vocabulary, distinctions, events, states, and success conditions the object can express.
- `D` is the decision space: the choices the object is allowed or expected to make.
- `A` is the action space: the actions the object may perform, request, block, or delegate.
- `C` is the constraint space: the limits under which its decisions and actions remain valid.
- `E` is the set of exposure functions: what the object reveals to other objects, depending on the target.
- `F` is the refinement or delegation relation: how abstract commitments are translated into lower-level obligations.
- `X` is the escalation policy: when the object must expose hidden distinctions, request help, transfer authority, or stop acting locally.

The key idea is that autonomy is not the absence of constraint. Autonomy is local choice within a constrained abstraction.

An object is autonomous relative to another object when it may choose among several lower-level refinements that are equivalent from the higher-level point of view. For example, a navigation object may choose between routes as long as each route preserves the mission-level commitment: reach the target safely before the deadline. The mission object does not need to know every route considered. It only needs the exposed commitment to remain valid.

## 3. Hierarchy as a Graph

A hierarchical system can be modeled as:

```text
S = (Obj, H, K)
```

where:

- `Obj` is the set of autonomy objects.
- `H` is the set of hierarchical refinement or delegation edges.
- `K` is the set of lateral dependency edges.

Hierarchical edges connect objects across levels of abstraction. A parent expresses a commitment in its own representation space. A child receives a refined obligation in another representation space.

For example:

```text
Mission Planner -> Navigation Planner
Mission Planner -> Inspection Planner
Navigation Planner -> Motion Controller
Motion Controller -> Actuator Interface
```

Lateral edges connect objects whose responsibilities interact without one simply being the parent of the other.

For example:

```text
Navigation <-> Energy
Navigation <-> Safety
Inspection <-> Communications
Tool Executor <-> Policy Monitor
Planner <-> Resource Manager
```

These lateral edges matter because many commitments are valid only compositionally. Navigation may be locally feasible. Inspection may be locally feasible. Energy management may be locally feasible. Yet the mission can still fail if the three commitments cannot be satisfied together.

Thus, hierarchy is not only decomposition. It is coordination across representation boundaries.

## 4. Representation, Decision, and Action

The model separates three concepts that are often blurred together.

A **representation space** defines what an object can describe. A mission object may represent goals, regions, deadlines, risk, and mission phase. A controller may represent velocity, error signals, stability margins, and actuator limits. These are not merely different amounts of information. They are different semantic vocabularies.

A **decision space** defines what an object may choose. This is where autonomy appears. A planner may choose a route. A safety monitor may choose whether to block an action. A tool executor may choose whether to retry, fail, or escalate. A decision is local when the object is expected to resolve the trade-off without exposing every detail upward.

An **action space** defines what an object can cause. It may execute a physical command, invoke an API, allocate a resource, send a message, request a subordinate action, block an operation, or escalate a case. Decision ownership and action capability are related but not identical. An object may own a decision without directly performing the resulting action, and it may perform actions whose decision authority belongs elsewhere.

This separation clarifies authority. Authority is not a separate magical property. It is expressed through constraints over decisions and actions: what may be chosen, what may be executed, what may be delegated, and what must be escalated.

## 5. Constraints and Validity

Every autonomy object acts under constraints. These may include safety limits, resource budgets, timing requirements, legal rules, policy restrictions, mission objectives, security boundaries, or inherited commitments.

A local decision is valid only if it preserves the relevant constraints. But not all constraints are local. A battery budget may be owned by an energy object while depending on navigation, inspection, and communication behavior. A safety invariant may be owned by a safety object while constraining motion planning and actuator control. A human-approval rule may be owned by a policy object while applying to several tool executors.

This creates two kinds of validity.

**Local validity** asks whether an object preserves the abstraction it exposes. If a navigation object reports that it can reach the target safely, local validity asks whether its internal route, risk model, and motion assumptions justify that claim.

**Compositional validity** asks whether several local commitments jointly preserve a higher-level commitment. The mission may require navigation, inspection, energy, and communication objects to satisfy their commitments together. Each object may be locally correct while the combination is globally impossible.

Responsibility in hierarchical systems should therefore be understood as ownership of abstraction validity. A child is responsible for the commitments it exposes. A parent is responsible for whether the delegated subgraph preserves the parent commitment.

## 6. Exposure and Reporting

Reporting is not merely sending information. It is projection from one representation space into another.

An autonomy object usually exposes different views to different targets. A navigation object may report estimated arrival time to the mission planner, route risk to the safety object, energy cost to the energy object, and waypoints to the motion controller. Each report is a target-specific abstraction of internal state.

This is why a single generic interface is often insufficient. The meaning of a report depends on the receiving object and the commitment being preserved. Exposure should reveal enough to maintain coordination and accountability, but not so much that the receiving object is forced to reason in the wrong representation space.

## 7. Refinement and Delegation

Delegation is not merely assigning a task. It is transferring a constrained decision and action space across a semantic boundary.

When a parent delegates, it gives a child or subgraph an objective, constraints, authority boundaries, reporting expectations, and escalation conditions. The child is then free to choose among lower-level refinements that preserve the delegated commitment.

In real systems, delegation is often one-to-many. A mission-level goal may be refined into navigation, inspection, energy, safety, and communication obligations. No single child object fully owns the parent commitment. The commitment is preserved only through coordination among several objects.

This is why the model treats hierarchical systems as graphs rather than trees. A parent commitment may refine into a subgraph, and a lower-level issue may become relevant to multiple higher-level objects at once.

## 8. Escalation

Escalation occurs when local abstraction is no longer sufficient.

An object escalates when a hidden distinction becomes relevant to preserving a higher-level commitment, when constraints conflict, when uncertainty exceeds local authority, when a required action lies outside its action space, or when compositional validity can no longer be guaranteed locally.

Escalation is therefore not just exception handling. It is abstraction failure. It marks the moment when something that was supposed to remain internal must become visible to another object.

Escalation may have more than one target. A navigation failure may need to reach the mission planner, the energy manager, the safety monitor, and a human supervisor. Each target may receive a different projection of the same underlying problem.

## 9. Why This Abstraction Matters

This abstraction unifies several familiar ideas:

- Information hiding becomes representation encapsulation.
- Authority becomes freedom within a constrained decision and action space.
- Responsibility becomes ownership of exposed commitments.
- Reporting becomes target-specific projection.
- Delegation becomes constrained refinement across semantic boundaries.
- Escalation becomes the exposure of distinctions that can no longer remain hidden.
- Hierarchy becomes a graph of autonomy objects rather than a stack of layers.

The value of the model is not that it replaces existing theories of control, planning, modularity, contracts, security, or organization. Its value is that it gives them a common language. It helps describe how intent moves downward, how commitments move upward, how local choices remain accountable, and how failures cross semantic boundaries.

## 10. Application to AI Agent Systems

The abstraction is especially useful for AI agent systems.

An agent architecture may include a user-facing agent, a planner, a tool selector, a code editor, a shell executor, a memory system, a policy checker, a browser agent, and a human approval loop. Each object operates in a different representation space. Each owns different decisions and actions. Each exposes different information to different targets.

For example, a planner may represent tasks and dependencies. A shell executor represents commands, exit codes, files, and side effects. A policy checker represents permissions and prohibited actions. A user-facing agent represents user intent and conversational commitments. A reliable system must preserve intent across all of these boundaries.

This model helps ask the right design questions:

- What representation does each agent or component own?
- Which decisions are local, and which require approval?
- What actions can each object perform or delegate?
- Which constraints are private, inherited, or shared through lateral dependencies?
- What must each object expose, and to whom?
- When does uncertainty, conflict, or risk require escalation?

These questions are often more useful than asking only what the interface is. The interface matters, but the deeper issue is whether the interface preserves abstraction validity.

## 11. Relation to the A2A Protocol

A2A is best understood as an acquaintance and communication protocol between agents. It lets one agent discover another: who it is, what skills it exposes, which formats it supports, how it can be contacted, and what security requirements apply.

This is valuable, but it is not the same as defining a hierarchy. A2A describes an agent as it presents itself to others. The autonomy-object abstraction describes how a system places that agent inside a role: this is your representation space, these are your constraints, this is the commitment you must preserve, this is what you may decide or do locally, this is how you report, and this is when you must escalate.

The difference is clear in a drone-swarm system. A2A may tell a mission agent that a drone agent supports mapping, inspection, navigation reports, and battery telemetry. The autonomy abstraction says what that drone is responsible for in this mission: which sector to scan, which limits to obey, which local choices it owns, what it must report, and when it must stop acting locally.

## 12. Conclusion

Hierarchical systems are not merely chains of command, stacks of layers, or trees of tasks. They are graphs of semantic boundaries. Each boundary simplifies the world, owns local choices, acts under constraints, exposes selected commitments, refines higher-level intent, and escalates when its abstraction fails.

The proposed autonomy-object model provides a compact way to describe this structure:

```text
o = (R, D, A, C, E, F, X)
S = (Obj, H, K)
```

Its central claim is that hierarchy works by preserving meaning across representation changes. Autonomy is the freedom to choose among lower-level alternatives that remain equivalent at a higher level. Responsibility is the obligation to keep exposed abstractions valid. Escalation is the moment when hidden distinctions must become visible.

Seen this way, hierarchical systems are not just organized systems. They are systems that manage complexity by distributing representation, decision, action, constraint, and accountability across coordinated autonomous objects.