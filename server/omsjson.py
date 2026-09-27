"""OMS JSON <-> XML codec (OMSC-SPC-013 RevB, Section 6.1).

The codec walks the XML Schema component model (via ``xmlschema``) so that:

* JSON members, which may appear in any order, are emitted in content-model order;
* elements with maxOccurs > 1 map to JSON arrays;
* ``"$type"`` maps to ``xsi:type`` for derived complex types;
* simple values follow Section 6.1.4 (boolean/float/double/decimal-family -> JSON
  literals, UniversallyUniqueIdentifierType -> RFC 4122 string, everything else -> string).

Schema validity of the resulting XML is checked with lxml (libxml2), which is how
"An OMS JSON message is valid" (Section 6.1.5) is decided.
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import xmlschema
from lxml import etree

UCI_NS = "https://www.vdl.afrl.af.mil/programs/oam"
XSD_NS = "http://www.w3.org/2001/XMLSchema"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPE = f"{{{XSI_NS}}}type"
UUID_TYPE = f"{{{UCI_NS}}}UniversallyUniqueIdentifierType"

DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "A-GRA_MessageDefinitions_v6_0_a.xsd"


_META_NAMESPACES = {XSD_NS, XSI_NS, "http://www.w3.org/XML/1998/namespace"}


def _namespace(qname: str) -> str:
    return qname[1:].partition("}")[0] if qname.startswith("{") else ""


class InvalidMessage(ValueError):
    """Raised when a CAL Message is not a valid OMS JSON message."""


def json_key(qname: str) -> str:
    """Member string for an element/type name (Section 6.1.1 / 6.1.2)."""
    ns, _, local = qname[1:].partition("}") if qname.startswith("{") else ("", "", qname)
    return local if ns == UCI_NS else f"{{{ns}}}{local}"


def qname_of(key: str) -> str:
    """Inverse of json_key (Section 6.1.5.2)."""
    if key.startswith("{"):
        return key
    return f"{{{UCI_NS}}}{key}"


@dataclass(frozen=True)
class _Particle:
    key: str
    element: Any  # xmlschema XsdElement
    repeated: bool


class OmsJsonCodec:
    def __init__(self, schema_path: str | Path = DEFAULT_SCHEMA):
        self.schema_path = Path(schema_path)
        self.xs = xmlschema.XMLSchema10(str(self.schema_path))
        self.lxml_schema = etree.XMLSchema(etree.parse(str(self.schema_path)))
        self.version = self.xs.version
        self._integer = self.xs.maps.types[f"{{{XSD_NS}}}integer"]
        self._particles: dict[Any, tuple[_Particle, ...]] = {}

    # ------------------------------------------------------------------ schema helpers
    def global_element(self, message_name: str):
        """Return the global element for a SUB <message name>, or None."""
        qname = qname_of(message_name)
        if _namespace(qname) in _META_NAMESPACES:
            return None
        return self.xs.maps.elements.get(qname)

    def message_names(self) -> list[str]:
        return sorted(json_key(q) for q in self.xs.maps.elements if _namespace(q) not in _META_NAMESPACES)

    def particles(self, ctype) -> tuple[_Particle, ...]:
        """Element particles of a complex type in content-model (document) order.

        UCI-style schemas have no repeated model groups, so a depth-first walk of the
        content model gives a valid element order for any single choice branch.
        """
        cached = self._particles.get(ctype)
        if cached is not None:
            return cached
        out: list[_Particle] = []

        def walk(group, repeated_group: bool) -> None:
            rep = repeated_group or group.max_occurs is None or group.max_occurs > 1
            for item in group:
                if isinstance(item, xmlschema.validators.XsdGroup):
                    walk(item, rep)
                elif isinstance(item, xmlschema.validators.XsdElement):
                    many = rep or item.max_occurs is None or item.max_occurs > 1
                    out.append(_Particle(json_key(item.name), item, many))

        content = ctype.content
        if ctype.has_complex_content() and content is not None:
            walk(content, False)
        result = tuple(out)
        self._particles[ctype] = result
        return result

    def resolve_type(self, type_key: str):
        t = self.xs.maps.types.get(qname_of(type_key))
        if t is None or not t.is_complex():
            raise InvalidMessage(f'unknown complex type in "$type": {type_key}')
        return t

    def derived_types(self, base) -> list:
        return [t for t in self.xs.maps.types.values()
                if t.is_complex() and t is not base and not t.abstract and t.is_derived(base)]

    # ------------------------------------------------------------------ JSON -> XML
    def json_to_xml(self, obj: Any) -> etree._Element:
        if not isinstance(obj, dict) or len(obj) != 1:
            raise InvalidMessage("an OMS JSON message is an object with exactly one member")
        (key, value), = obj.items()
        decl = self.global_element(key)
        if decl is None:
            raise InvalidMessage(f"{key} is not a global element of the schema")
        nsmap = {None: UCI_NS, "xsi": XSI_NS}
        root = etree.Element(qname_of(key), nsmap=nsmap)
        self._fill(root, decl.type, value, key)
        return root

    def _fill(self, node, xtype, value, path: str) -> None:
        if xtype.is_simple():
            node.text = self._simple_to_text(xtype, value, path)
            return
        if not isinstance(value, dict):
            raise InvalidMessage(f"{path}: expected an object")
        if "$type" in value:
            actual = self.resolve_type(value["$type"])
            if actual is not xtype and not actual.is_derived(xtype):
                raise InvalidMessage(f"{path}: $type {value['$type']} is not derived from {json_key(xtype.name or '')}")
            node.set(XSI_TYPE, self._qname_text(node, actual.name))
            xtype = actual
        if xtype.has_simple_content():  # not used by UCI-style schemas, kept for safety
            raise InvalidMessage(f"{path}: simple content complex types are not supported")
        parts = self.particles(xtype)
        known = {p.key for p in parts}
        extra = [k for k in value if k != "$type" and k not in known]
        if extra:
            raise InvalidMessage(f"{path}: unexpected member(s) {extra}")
        for p in parts:
            if p.key not in value:
                continue
            v = value[p.key]
            items = v if p.repeated else [v]
            if p.repeated and not isinstance(v, list):
                raise InvalidMessage(f"{path}.{p.key}: expected an array (maxOccurs > 1)")
            for i, item in enumerate(items):
                child = etree.SubElement(node, p.element.name)
                self._fill(child, p.element.type, item, f"{path}.{p.key}" + (f"[{i}]" if p.repeated else ""))

    @staticmethod
    def _qname_text(node, qname: str) -> str:
        ns, _, local = qname[1:].partition("}")
        if ns == UCI_NS:
            return local  # default namespace on the root
        prefix = next((p for p, u in node.nsmap.items() if u == ns and p), None)
        if prefix is None:
            # Schema extensions in other namespaces would need their prefix declared on
            # the root element; the A-GRA 6.0a schema has a single target namespace.
            raise InvalidMessage(f"$type in namespace {ns} is not supported by this codec")
        return f"{prefix}:{local}"

    def _simple_to_text(self, stype, value, path: str) -> str:
        prim = _primitive(stype)
        if isinstance(value, (dict, list)) or value is None:
            raise InvalidMessage(f"{path}: expected a simple value")
        if self._is_uuid(stype):
            if not isinstance(value, str):
                raise InvalidMessage(f"{path}: UUID must be a string")
            try:
                return uuid.UUID(value).hex.upper()
            except ValueError as exc:
                raise InvalidMessage(f"{path}: invalid RFC 4122 UUID {value!r}") from exc
        if prim == "boolean":
            if not isinstance(value, bool):
                raise InvalidMessage(f"{path}: expected true or false")
            return "true" if value else "false"
        if prim in ("float", "double"):
            if isinstance(value, str):
                special = {"NaN": "NaN", "Infinity": "INF", "-Infinity": "-INF"}
                if value not in special:
                    raise InvalidMessage(f"{path}: expected a number, \"NaN\", \"Infinity\" or \"-Infinity\"")
                return special[value]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidMessage(f"{path}: expected a number")
            return repr(float(value)) if isinstance(value, float) else str(value)
        if prim == "decimal":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidMessage(f"{path}: expected a number")
            if isinstance(value, float) and value.is_integer() and stype.is_derived(self._integer):
                return str(int(value))
            return str(value)
        if not isinstance(value, str):
            raise InvalidMessage(f"{path}: expected a string")
        return value

    # ------------------------------------------------------------------ XML -> JSON
    def xml_to_json(self, root: etree._Element) -> dict:
        decl = self.global_element(root.tag)
        if decl is None:
            raise InvalidMessage(f"{root.tag} is not a global element of the schema")
        return {json_key(root.tag): self._read(root, decl.type)}

    def _read(self, node, xtype):
        if xtype.is_simple():
            return self._text_to_simple(xtype, node.text or "")
        out: dict[str, Any] = {}
        xsi = node.get(XSI_TYPE)
        if xsi:
            prefix, _, local = xsi.rpartition(":")
            ns = node.nsmap.get(prefix or None, UCI_NS)
            xtype = self.xs.maps.types[f"{{{ns}}}{local}"]
            out["$type"] = json_key(xtype.name)
        by_name = {p.element.name: p for p in self.particles(xtype)}
        for child in node:
            if not isinstance(child.tag, str):
                continue  # comments / PIs
            p = by_name.get(child.tag)
            if p is None:
                raise InvalidMessage(f"unexpected element {child.tag}")
            val = self._read(child, p.element.type)
            if p.repeated:
                out.setdefault(p.key, []).append(val)
            else:
                out[p.key] = val
        return out

    def _text_to_simple(self, stype, text: str):
        text = text.strip()
        if self._is_uuid(stype):
            return str(uuid.UUID(hex=text))
        prim = _primitive(stype)
        if prim == "boolean":
            return text in ("true", "1")
        if prim in ("float", "double"):
            special = {"NaN": "NaN", "INF": "Infinity", "+INF": "Infinity", "-INF": "-Infinity"}
            if text in special:
                return special[text]
            f = float(text)
            return f
        if prim == "decimal":
            if stype.is_derived(self._integer):
                return int(text)
            f = float(text)
            return int(f) if f.is_integer() and "." not in text and "e" not in text.lower() else f
        return text

    def _is_uuid(self, stype) -> bool:
        uuid_type = self.xs.maps.types.get(UUID_TYPE)
        return uuid_type is not None and (stype is uuid_type or stype.is_derived(uuid_type))

    # ------------------------------------------------------------------ public API
    def validate_xml(self, root: etree._Element) -> None:
        if not self.lxml_schema.validate(root):
            err = self.lxml_schema.error_log.last_error
            raise InvalidMessage(f"schema validation failed: line {err.line}: {err.message}")

    def parse_message(self, text: str) -> tuple[str, dict, etree._Element]:
        """Parse + validate a CAL Message. Returns (message name, JSON object, XML)."""
        try:
            obj = json.loads(text, parse_constant=_reject_constant)
        except (ValueError, TypeError) as exc:
            raise InvalidMessage(f"not valid JSON text: {exc}") from exc
        root = self.json_to_xml(obj)
        self.validate_xml(root)
        (name,) = obj.keys()
        return name, obj, root

    def to_xml_bytes(self, obj: dict) -> bytes:
        root = self.json_to_xml(obj)
        self.validate_xml(root)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _reject_constant(token: str):
    raise ValueError(f"{token} is not valid JSON (RFC 8259)")


@lru_cache(maxsize=None)
def _primitive_name(qname: str | None) -> str:
    return qname.rpartition("}")[2] if qname else ""


def _primitive(stype) -> str:
    """Local name of the XSD primitive type definition, or 'list'/'union'."""
    if stype.is_list():
        return "list"
    if stype.is_union():
        return "union"
    prim = getattr(stype, "primitive_type", None)
    return _primitive_name(getattr(prim, "name", None))


__all__ = ["OmsJsonCodec", "InvalidMessage", "UCI_NS", "json_key", "qname_of"]
