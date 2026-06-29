"""Minimal CEL type-checker for ACA L1 conformance.

This is *not* a full CEL implementation. It is a self-contained, dependency-free
tokenizer + Pratt parser + conservative type-checker covering the CEL subset that
ACA charters use (boolean / comparison / arithmetic operators, function calls,
member access, indexing, and literals).

Its job is to support conformance level **L1 — Local validity** from
``aca-spec.md``: every ``expr`` / ``when`` / ``precondition`` in a charter must
*compile* and *type-check* against the CEL environment built from that charter's
representation space ``R`` (see ``cel-environment.md``).

Design choices:

* The high-value check is **referential integrity at the expression level**:
  every identifier used in an expression must be declared in ``R`` (as a term,
  state machine variable ``state``, predicate, or success condition) or be a
  standard binding (``now``). This catches "symbol used but not declared in R"
  that L0 cannot see inside expression strings.
* Type-checking is **conservative**: an error is reported only when the checker
  is confident (e.g. a boolean operator applied to a number, or a comparison
  across clearly incompatible base types). Abstract / namespaced types are
  opaque (``DYN``) per the spec, and any operation on ``DYN`` is accepted, so the
  checker never produces false positives on opaque values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- Types ------------------------------------------------------------------

INT = "int"
DOUBLE = "double"
BOOL = "bool"
STRING = "string"
DURATION = "duration"
TIMESTAMP = "timestamp"
DYN = "dyn"  # unknown / opaque (abstract types); suppresses type errors

NUMERIC = {INT, DOUBLE}

# Maps an ACA representation `type` to a CEL type (see cel-environment.md §2.1).
BASE_TYPE_MAP = {
    "real": DOUBLE,
    "int": INT,
    "bool": BOOL,
    "string": STRING,
    "duration": DURATION,
    "timestamp": TIMESTAMP,
    "enum": STRING,
}


def aca_type_to_cel(aca_type: str) -> str:
    """Map an ACA `term.type` to its CEL type; namespaced/abstract types are DYN."""
    if aca_type in BASE_TYPE_MAP:
        return BASE_TYPE_MAP[aca_type]
    # `<ns>.<name>` abstract types are opaque in v0.1.
    return DYN


# --- Lexer ------------------------------------------------------------------


class CelError(Exception):
    """A parse-time or lex-time CEL error."""


@dataclass
class Token:
    kind: str  # 'num' | 'str' | 'ident' | 'op'
    value: str
    pos: int
    is_float: bool = False


_TWO_CHAR_OPS = {"||", "&&", "==", "!=", "<=", ">="}
_ONE_CHAR_OPS = set("<>+-*/%!().,[]")


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in " \t\r\n":
            i += 1
            continue
        # strings
        if ch in "'\"":
            quote = ch
            j = i + 1
            buf = []
            while j < n and src[j] != quote:
                if src[j] == "\\" and j + 1 < n:
                    buf.append(src[j + 1])
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            if j >= n:
                raise CelError(f"unterminated string at {i}")
            tokens.append(Token("str", "".join(buf), i))
            i = j + 1
            continue
        # numbers
        if ch.isdigit() or (ch == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            is_float = False
            if ch == "0" and i + 1 < n and src[i + 1] in "xX":
                j = i + 2
                while j < n and (src[j].isdigit() or src[j] in "abcdefABCDEF"):
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
                if j < n and src[j] == ".":
                    is_float = True
                    j += 1
                    while j < n and src[j].isdigit():
                        j += 1
                if j < n and src[j] in "eE":
                    is_float = True
                    j += 1
                    if j < n and src[j] in "+-":
                        j += 1
                    while j < n and src[j].isdigit():
                        j += 1
            tokens.append(Token("num", src[i:j], i, is_float=is_float))
            i = j
            continue
        # identifiers
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            tokens.append(Token("ident", src[i:j], i))
            i = j
            continue
        # operators
        two = src[i : i + 2]
        if two in _TWO_CHAR_OPS:
            tokens.append(Token("op", two, i))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            tokens.append(Token("op", ch, i))
            i += 1
            continue
        raise CelError(f"unexpected character {ch!r} at {i}")
    return tokens


# --- AST --------------------------------------------------------------------


@dataclass
class Lit:
    type: str
    raw: str


@dataclass
class Ident:
    name: str


@dataclass
class Unary:
    op: str
    operand: object


@dataclass
class Binary:
    op: str
    left: object
    right: object


@dataclass
class Call:
    func: object  # Ident (function) or Member (method)
    args: list


@dataclass
class Member:
    base: object
    name: str


@dataclass
class Index:
    base: object
    index: object


# --- Parser (Pratt / precedence-climbing) -----------------------------------


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> Optional[Token]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect_op(self, op: str) -> None:
        t = self.peek()
        if t is None or t.kind != "op" or t.value != op:
            raise CelError(f"expected {op!r}")
        self.next()

    def at_op(self, *ops: str) -> bool:
        t = self.peek()
        return t is not None and t.kind == "op" and t.value in ops

    def parse(self):
        node = self.parse_or()
        if self.peek() is not None:
            raise CelError(f"trailing tokens near {self.peek().value!r}")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.at_op("||"):
            self.next()
            node = Binary("||", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_equality()
        while self.at_op("&&"):
            self.next()
            node = Binary("&&", node, self.parse_equality())
        return node

    def parse_equality(self):
        node = self.parse_rel()
        while self.at_op("==", "!="):
            op = self.next().value
            node = Binary(op, node, self.parse_rel())
        return node

    def parse_rel(self):
        node = self.parse_add()
        while self.at_op("<", "<=", ">", ">="):
            op = self.next().value
            node = Binary(op, node, self.parse_add())
        return node

    def parse_add(self):
        node = self.parse_mul()
        while self.at_op("+", "-"):
            op = self.next().value
            node = Binary(op, node, self.parse_mul())
        return node

    def parse_mul(self):
        node = self.parse_unary()
        while self.at_op("*", "/", "%"):
            op = self.next().value
            node = Binary(op, node, self.parse_unary())
        return node

    def parse_unary(self):
        if self.at_op("!", "-"):
            op = self.next().value
            return Unary(op, self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        node = self.parse_primary()
        while True:
            if self.at_op("("):
                self.next()
                args = []
                if not self.at_op(")"):
                    args.append(self.parse_or())
                    while self.at_op(","):
                        self.next()
                        args.append(self.parse_or())
                self.expect_op(")")
                node = Call(node, args)
            elif self.at_op("."):
                self.next()
                t = self.peek()
                if t is None or t.kind != "ident":
                    raise CelError("expected field name after '.'")
                self.next()
                node = Member(node, t.value)
            elif self.at_op("["):
                self.next()
                idx = self.parse_or()
                self.expect_op("]")
                node = Index(node, idx)
            else:
                return node

    def parse_primary(self):
        t = self.peek()
        if t is None:
            raise CelError("unexpected end of expression")
        if t.kind == "op" and t.value == "(":
            self.next()
            node = self.parse_or()
            self.expect_op(")")
            return node
        if t.kind == "num":
            self.next()
            return Lit(DOUBLE if t.is_float else INT, t.value)
        if t.kind == "str":
            self.next()
            return Lit(STRING, t.value)
        if t.kind == "ident":
            self.next()
            if t.value in ("true", "false"):
                return Lit(BOOL, t.value)
            if t.value == "null":
                return Lit(DYN, t.value)
            return Ident(t.value)
        raise CelError(f"unexpected token {t.value!r}")


def parse(src: str):
    return Parser(tokenize(src)).parse()


# --- Type environment -------------------------------------------------------


@dataclass
class Env:
    """The CEL declaration environment built from a charter's R."""

    vars: dict[str, str] = field(default_factory=dict)  # name -> CEL type
    states: set[str] = field(default_factory=set)  # declared state names
    enums: dict[str, set[str]] = field(default_factory=dict)  # term -> allowed values


# Known global functions: name -> return type. Args are type-checked (so that
# undeclared identifiers inside arguments are still caught) but argument types
# are accepted permissively to avoid false positives.
_KNOWN_FUNCS = {
    "duration": DURATION,
    "timestamp": TIMESTAMP,
    "int": INT,
    "double": DOUBLE,
    "string": STRING,
    "bool": BOOL,
    "size": INT,
    "has": BOOL,
    "matches": BOOL,
    "contains": BOOL,
    "startsWith": BOOL,
    "endsWith": BOOL,
}


# --- Type checker -----------------------------------------------------------


class TypeChecker:
    def __init__(self, env: Env):
        self.env = env
        self.errors: list[str] = []

    def check(self, node) -> str:
        """Type-check a parsed expression, recording errors; return its type."""
        return self._t(node)

    def _err(self, msg: str) -> None:
        self.errors.append(msg)

    def _t(self, node) -> str:
        if isinstance(node, Lit):
            return node.type
        if isinstance(node, Ident):
            if node.name in self.env.vars:
                return self.env.vars[node.name]
            self._err(f"identifier '{node.name}' is not declared in R")
            return DYN
        if isinstance(node, Unary):
            return self._t_unary(node)
        if isinstance(node, Binary):
            return self._t_binary(node)
        if isinstance(node, Call):
            return self._t_call(node)
        if isinstance(node, Member):
            self._t(node.base)
            return DYN  # opaque field access in v0.1
        if isinstance(node, Index):
            self._t(node.base)
            self._t(node.index)
            return DYN
        return DYN

    def _t_unary(self, node: Unary) -> str:
        t = self._t(node.operand)
        if node.op == "!":
            if t not in (BOOL, DYN):
                self._err(f"operator '!' expects bool, got {t}")
            return BOOL
        # unary minus
        if t not in NUMERIC and t not in (DURATION, DYN):
            self._err(f"unary '-' expects a number or duration, got {t}")
        return t if t != DYN else DYN

    def _t_binary(self, node: Binary) -> str:
        op = node.op
        lt = self._t(node.left)
        rt = self._t(node.right)

        if op in ("&&", "||"):
            for side, t in ((node.left, lt), (node.right, rt)):
                if t not in (BOOL, DYN):
                    self._err(f"operator '{op}' expects bool operands, got {t}")
            return BOOL

        if op in ("==", "!="):
            self._check_enum_literal(node)
            if DYN not in (lt, rt) and not self._comparable_eq(lt, rt):
                self._err(f"cannot compare {lt} {op} {rt}")
            return BOOL

        if op in ("<", "<=", ">", ">="):
            if DYN not in (lt, rt) and not self._comparable_ord(lt, rt):
                self._err(f"cannot order-compare {lt} {op} {rt}")
            return BOOL

        # arithmetic
        if DYN in (lt, rt):
            return DYN
        if op == "+":
            return self._t_add(lt, rt)
        if op == "-":
            return self._t_sub(lt, rt)
        if op in ("*", "/", "%"):
            if lt in NUMERIC and rt in NUMERIC:
                return DOUBLE if DOUBLE in (lt, rt) else INT
            self._err(f"operator '{op}' expects numbers, got {lt} and {rt}")
            return DYN
        return DYN

    def _t_add(self, lt: str, rt: str) -> str:
        if lt in NUMERIC and rt in NUMERIC:
            return DOUBLE if DOUBLE in (lt, rt) else INT
        if lt == STRING and rt == STRING:
            return STRING
        if lt == DURATION and rt == DURATION:
            return DURATION
        if {lt, rt} == {TIMESTAMP, DURATION}:
            return TIMESTAMP
        self._err(f"operator '+' is not defined for {lt} and {rt}")
        return DYN

    def _t_sub(self, lt: str, rt: str) -> str:
        if lt in NUMERIC and rt in NUMERIC:
            return DOUBLE if DOUBLE in (lt, rt) else INT
        if lt == DURATION and rt == DURATION:
            return DURATION
        if lt == TIMESTAMP and rt == TIMESTAMP:
            return DURATION
        if lt == TIMESTAMP and rt == DURATION:
            return TIMESTAMP
        self._err(f"operator '-' is not defined for {lt} and {rt}")
        return DYN

    def _t_call(self, node: Call) -> str:
        for arg in node.args:
            self._t(arg)
        if isinstance(node.func, Ident):
            fname = node.func.name
            # A bare identifier in call position is a function name, not a
            # variable, so it is not subject to the R-declaration check.
            return _KNOWN_FUNCS.get(fname, DYN)
        # method call obj.method(...): type-check the receiver, result opaque.
        if isinstance(node.func, Member):
            self._t(node.func.base)
        else:
            self._t(node.func)
        return DYN

    @staticmethod
    def _comparable_eq(lt: str, rt: str) -> bool:
        if lt == rt:
            return True
        return lt in NUMERIC and rt in NUMERIC

    @staticmethod
    def _comparable_ord(lt: str, rt: str) -> bool:
        if lt in NUMERIC and rt in NUMERIC:
            return True
        return lt == rt and lt in (STRING, DURATION, TIMESTAMP)

    def _check_enum_literal(self, node: Binary) -> None:
        """If comparing `state`/an enum term to a string literal, validate it."""
        pairs = ((node.left, node.right), (node.right, node.left))
        for a, b in pairs:
            if isinstance(a, Ident) and isinstance(b, Lit) and b.type == STRING:
                if a.name == "state" and self.env.states:
                    if b.raw not in self.env.states:
                        self._err(
                            f"state literal '{b.raw}' is not a declared state "
                            f"(states: {sorted(self.env.states)})"
                        )
                elif a.name in self.env.enums:
                    allowed = self.env.enums[a.name]
                    if b.raw not in allowed:
                        self._err(
                            f"enum literal '{b.raw}' is not allowed for "
                            f"'{a.name}' (values: {sorted(allowed)})"
                        )


def check_expr(src: str, env: Env) -> list[str]:
    """Parse and type-check one expression; return a list of error strings."""
    try:
        ast = parse(src)
    except CelError as exc:
        return [f"parse error: {exc}"]
    tc = TypeChecker(env)
    tc.check(ast)
    return tc.errors


def build_env(charter: dict) -> Env:
    """Construct the CEL environment from a charter's representation space R."""
    r = charter.get("representation", {})
    env = Env()

    # standard bindings (cel-environment.md §2.5)
    env.vars["now"] = TIMESTAMP

    for term in r.get("terms", []):
        cel_t = aca_type_to_cel(term["type"])
        env.vars[term["name"]] = cel_t
        if term.get("type") == "enum" and term.get("values"):
            env.enums[term["name"]] = set(term["values"])

    states = [s["name"] for s in r.get("states", [])]
    if states:
        env.states = set(states)
        env.vars.setdefault("state", STRING)

    # predicates and success conditions are exposed as named booleans, and may
    # reference one another, so register them all before checking any body.
    for group in ("predicates", "success"):
        for item in r.get(group, []):
            env.vars[item["name"]] = BOOL

    return env
