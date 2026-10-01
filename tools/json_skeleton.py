"""Generate a minimal, schema-valid OMS JSON message for any global element.

Only required members are emitted. For choices the cheapest branch is picked, abstract
types are replaced by their cheapest concrete derivation (with "$type"), and simple values
are synthesised from facets (enumeration, length, range, pattern).

Usage:
    uv run python tools/json_skeleton.py SystemStatus [--check]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xmlschema  # noqa: E402

from server.omsjson import OmsJsonCodec, _primitive, json_key  # noqa: E402

try:  # Python 3.11+
    import re._parser as sre_parse  # type: ignore[import-not-found]
    from re import _constants as sre_c  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_parse  # type: ignore[no-redef]
    import sre_constants as sre_c  # type: ignore[no-redef]

INF = float("inf")


class SkeletonBuilder:
    def __init__(self, codec: OmsJsonCodec):
        self.codec = codec
        self._cost: dict = {}
        self._concrete: dict = {}

    # ---------------------------------------------------------------- cost model
    def type_cost(self, xtype, stack=()) -> float:
        """Number of required elements needed to instantiate a type (inf if recursive)."""
        if xtype.is_simple():
            return 1
        if xtype in self._cost:
            return self._cost[xtype]
        if xtype in stack:
            return INF
        stack = stack + (xtype,)
        if xtype.abstract:
            cands = [self.type_cost(t, stack) for t in self.concrete(xtype)]
            cost = min(cands, default=INF)
        else:
            cost = 1 + self._group_cost(xtype.content, stack) if xtype.content is not None else 1
        if not stack[:-1]:  # only memoise results that do not depend on the stack
            self._cost[xtype] = cost
        return cost

    def _group_cost(self, group, stack) -> float:
        if group.min_occurs == 0:
            return 0
        costs = []
        for item in group:
            if isinstance(item, xmlschema.validators.XsdGroup):
                costs.append(self._group_cost(item, stack))
            elif item.min_occurs == 0:
                costs.append(0)
            else:
                costs.append(item.min_occurs * self.type_cost(item.type, stack))
        if group.model == "choice":
            return min(costs, default=0)
        return sum(costs)

    def concrete(self, base) -> list:
        if base not in self._concrete:
            self._concrete[base] = self.codec.derived_types(base)
        return self._concrete[base]

    # ---------------------------------------------------------------- builders
    def message(self, name: str) -> dict:
        decl = self.codec.global_element(name)
        if decl is None:
            raise KeyError(name)
        return {name: self.value(decl.type, decl.type)}

    def value(self, xtype, declared, stack=()):
        if xtype.is_simple():
            return self.simple(xtype)
        out: dict = {}
        if xtype.abstract:
            xtype = min(self.concrete(xtype), key=lambda t: self.type_cost(t, stack))
        if xtype is not declared:
            out["$type"] = json_key(xtype.name)
        stack = stack + (xtype,)
        if xtype.content is not None:
            self._fill_group(xtype.content, out, stack)
        return out

    def _fill_group(self, group, out: dict, stack) -> None:
        items = list(group)
        if group.model == "choice":
            items = [min(items, key=lambda it: self._item_cost(it, stack))] if items else []
        for item in items:
            if isinstance(item, xmlschema.validators.XsdGroup):
                if item.min_occurs > 0:
                    self._fill_group(item, out, stack)
                continue
            if item.min_occurs == 0 and group.model != "choice":
                continue
            v = self.value(item.type, item.type, stack)
            many = item.max_occurs is None or item.max_occurs > 1
            out[json_key(item.name)] = [v] * max(1, item.min_occurs) if many else v

    def _item_cost(self, item, stack) -> float:
        if isinstance(item, xmlschema.validators.XsdGroup):
            return self._group_cost(item, stack)
        return self.type_cost(item.type, stack)

    # ---------------------------------------------------------------- simple values
    def simple(self, stype):
        if self.codec._is_uuid(stype):
            return str(uuid.uuid4())
        enum = getattr(stype, "enumeration", None)
        if enum:
            return enum[0]
        prim = _primitive(stype)
        if prim == "boolean":
            return False
        if prim in ("float", "double", "decimal"):
            return self._number(stype, prim)
        if prim == "dateTime":
            return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        if prim == "duration":
            return "PT0S"
        if prim == "time":
            return "00:00:00Z"
        if prim == "date":
            return dt.date.today().isoformat()
        if prim == "hexBinary":
            n = _facet(stype, "length") or _facet(stype, "minLength") or 1
            return "00" * int(n)
        if prim == "base64Binary":
            return "AA=="
        if prim == "anyURI":
            return "urn:example"
        return self._string(stype)

    def _number(self, stype, prim):
        lo = _facet(stype, "minInclusive")
        lo_x = _facet(stype, "minExclusive")
        hi = _facet(stype, "maxInclusive")
        if lo is not None:
            v = lo
        elif lo_x is not None:
            v = lo_x + 1 if stype.is_derived(self.codec._integer) else lo_x + (1 if hi is None else (hi - lo_x) / 2)
        elif hi is not None and hi < 0:
            v = hi
        else:
            v = 0
        if stype.is_derived(self.codec._integer):
            return int(v)
        return float(v)

    def _string(self, stype):
        n_min = int(_facet(stype, "minLength") or _facet(stype, "length") or 1)
        n_max = _facet(stype, "maxLength") or _facet(stype, "length")
        patterns = getattr(stype, "patterns", None)
        if patterns:
            for regex in patterns.regexps:
                s = _sample(regex, n_min)
                if s is not None and stype.is_valid(s):
                    return s
        s = "A" * n_min
        if n_max is not None:
            s = s[: int(n_max)]
        return s


def _facet(stype, name):
    """Walk the restriction chain looking for a facet value."""
    tag = f"{{http://www.w3.org/2001/XMLSchema}}{name}"
    t = stype
    while t is not None and hasattr(t, "facets"):
        f = t.facets.get(tag)
        if f is not None:
            return f.value
        t = getattr(t, "base_type", None)
    return None


def _sample(regex: str, min_len: int) -> str | None:
    """Produce one string matching a simple regex (char classes, literals, repeats)."""
    try:
        tree = sre_parse.parse(regex)
    except Exception:
        return None

    def first_char(items) -> str | None:
        chars = []
        for op, av in items:
            if op == sre_c.LITERAL:
                chars.append(chr(av))
            elif op == sre_c.RANGE:
                lo, hi = av
                chars.extend(chr(c) for c in range(lo, min(hi, lo + 80) + 1))
            elif op == sre_c.CATEGORY:
                chars.append({sre_c.CATEGORY_DIGIT: "0", sre_c.CATEGORY_SPACE: " "}.get(av, "A"))
            elif op == sre_c.NEGATE:
                return "A"
        for pref in ("A", "a", "0"):
            if pref in chars:
                return pref
        printable = [c for c in chars if c.isalnum()]
        return (printable or chars or [None])[0]

    def gen(seq) -> str | None:
        out = []
        for op, av in seq:
            if op == sre_c.LITERAL:
                out.append(chr(av))
            elif op == sre_c.IN:
                c = first_char(av)
                if c is None:
                    return None
                out.append(c)
            elif op == sre_c.ANY:
                out.append("A")
            elif op in (sre_c.MAX_REPEAT, sre_c.MIN_REPEAT):
                lo, hi, sub = av
                piece = gen(sub)
                if piece is None:
                    return None
                n = max(lo, 1 if hi != 0 else 0)
                if piece and hi is not sre_c.MAXREPEAT:
                    n = min(max(n, min_len // max(len(piece), 1)), hi)
                elif piece:
                    n = max(n, min_len // max(len(piece), 1))
                out.append(piece * n)
            elif op == sre_c.SUBPATTERN:
                piece = gen(av[-1])
                if piece is None:
                    return None
                out.append(piece)
            elif op == sre_c.BRANCH:
                piece = gen(av[1][0])
                if piece is None:
                    return None
                out.append(piece)
            elif op == sre_c.AT:
                continue
            else:
                return None
        return "".join(out)

    return gen(tree)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("message", nargs="+")
    ap.add_argument("--check", action="store_true", help="validate the skeleton against the XSD")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()
    codec = OmsJsonCodec()
    builder = SkeletonBuilder(codec)
    for name in args.message:
        msg = builder.message(name)
        print(json.dumps(msg, indent=None if args.compact else 2))
        if args.check:
            codec.to_xml_bytes(msg)
            print(f"# {name}: valid", file=sys.stderr)


if __name__ == "__main__":
    main()
