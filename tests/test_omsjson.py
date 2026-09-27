"""OMS JSON mapping (OMSC-SPC-013 6.1) on the spec's example constructs and on the A-GRA schema."""
import json

import pytest
from lxml import etree

from server.omsjson import UCI_NS, InvalidMessage
from tools.json_skeleton import SkeletonBuilder

NS = f"{{{UCI_NS}}}"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def roundtrip(codec, obj):
    root = codec.json_to_xml(obj)
    codec.validate_xml(root)
    assert codec.xml_to_json(root) == obj
    return root


def test_global_simple_element(mini_codec):
    root = roundtrip(mini_codec, {"MyElement": True})
    assert root.tag == NS + "MyElement" and root.text == "true"


def test_complex_with_array_uuid_numbers(mini_codec):
    obj = {"MyComplex": {"A": "Hello world", "B": ["x", "y"], "Id": "697eba54-6f37-11ed-a1eb-0242ac120002",
                         "Count": 9999, "Ratio": 99.99, "Price": 12.5, "Flag": False, "Code": -9999}}
    root = roundtrip(mini_codec, obj)
    assert root.find(NS + "Id").text == "697EBA546F3711EDA1EB0242AC120002"  # hexBinary on the XML side


def test_member_order_is_free(mini_codec):
    root = mini_codec.json_to_xml({"MyComplex": {"Color": "RED", "B": ["x"], "A": "first"}})
    mini_codec.validate_xml(root)
    assert [c.tag.split("}")[1] for c in root] == ["A", "B", "Color"]


@pytest.mark.parametrize("json_val,xml_val", [("NaN", "NaN"), ("Infinity", "INF"), ("-Infinity", "-INF")])
def test_float_special_values(mini_codec, json_val, xml_val):
    root = roundtrip(mini_codec, {"MyComplex": {"A": "a", "Ratio": json_val}})
    assert root.find(NS + "Ratio").text == xml_val


def test_empty_content(mini_codec):
    roundtrip(mini_codec, {"MyEmpty": {}})


def test_type_substitution(mini_codec):
    root = roundtrip(mini_codec, {"Shapes": {"Shape": [{"$type": "CircleType", "Name": "c", "Radius": 1.5}]}})
    assert root.find(NS + "Shape").get(XSI_TYPE) == "CircleType"


def test_abstract_type_without_type_is_rejected(mini_codec):
    with pytest.raises(InvalidMessage, match="schema validation"):
        mini_codec.parse_message(json.dumps({"Shapes": {"Shape": [{"Name": "c"}]}}))


@pytest.mark.parametrize("obj,msg", [
    ({"MyComplex": {"A": "a", "Nope": 1}}, "unexpected member"),
    ({"MyComplex": {"A": "a", "B": "not-an-array"}}, "expected an array"),
    ({"MyComplex": {"A": "a", "Id": "zzz"}}, "UUID"),
    ({"MyComplex": {"A": "a", "Flag": "true"}}, "true or false"),
    ({"MyComplex": {"A": "a", "Count": "5"}}, "expected a number"),
    ({"MyComplex": {"A": 5}}, "expected a string"),
    ({"Shapes": {"Shape": [{"$type": "NoSuchType", "Name": "c"}]}}, "unknown complex type"),
    ({"Unknown": {}}, "not a global element"),
    ({"MyElement": True, "MyEmpty": {}}, "exactly one member"),
])
def test_structural_errors(mini_codec, obj, msg):
    with pytest.raises(InvalidMessage, match=msg):
        mini_codec.json_to_xml(obj)


def test_schema_errors_detected(mini_codec):
    with pytest.raises(InvalidMessage, match="schema validation"):
        mini_codec.parse_message(json.dumps({"MyComplex": {"A": "a", "Count": 70000}}))  # > unsignedShort
    with pytest.raises(InvalidMessage, match="schema validation"):
        mini_codec.parse_message(json.dumps({"MyComplex": {"B": ["x"]}}))  # A is required
    with pytest.raises(InvalidMessage, match="not valid JSON"):
        mini_codec.parse_message('{"MyElement": NaN}')


def test_agra_all_messages_roundtrip(agra_codec):
    """Every concrete global element of the A-GRA schema: skeleton -> JSON -> XML -> XSD -> JSON."""
    builder = SkeletonBuilder(agra_codec)
    names = [n for n in agra_codec.message_names() if not agra_codec.global_element(n).type.abstract]
    assert len(names) > 800
    for name in names:
        _, obj, root = agra_codec.parse_message(json.dumps(builder.message(name)))
        assert agra_codec.xml_to_json(root) == obj, name


def test_agra_xml_bytes_roundtrip(agra_codec):
    msg = json.loads(json.dumps(SkeletonBuilder(agra_codec).message("SystemStatus")))
    assert agra_codec.xml_to_json(etree.fromstring(agra_codec.to_xml_bytes(msg))) == msg
