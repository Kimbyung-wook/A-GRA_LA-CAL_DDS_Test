import pytest

from server import owp_codec as owp


def test_parse_ops():
    assert owp.parse_client('INIT {"a": 1, "b": [1, 2]}') == owp.Operation("INIT", ('{"a": 1, "b": [1, 2]}',))
    assert owp.parse_client('PUB\tt.1  {"X": { "A": "a b" }}').args == ("t.1", '{"X": { "A": "a b" }}')
    assert owp.parse_client("SUB s1 SystemStatus ms.dms").args == ("s1", "SystemStatus", "ms.dms")
    assert owp.parse_client("SUB s1 SystemStatus ms.dms grp").args[-1] == "grp"
    assert owp.parse_client("UNSUB s1").args == ("s1",)


def test_multiline_message_field():
    op = owp.parse_client('PUB t {\n  "MyElement": true\n}')
    assert op.args[1] == '{\n  "MyElement": true\n}'


@pytest.mark.parametrize("text,code", [
    ("HELLO x", owp.ILLEGAL_OPERATION),
    ("MSG s1 {}", owp.ILLEGAL_OPERATION),      # server operation sent by a client
    ("+OK", owp.ILLEGAL_OPERATION),
    ("", owp.ILLEGAL_OPERATION),
    ("INIT", owp.ILLEGAL_ARGUMENT),
    ("PUB topic", owp.ILLEGAL_ARGUMENT),
    ("PUB bad/topic {}", owp.ILLEGAL_ARGUMENT),
    ("SUB s1 SystemStatus", owp.ILLEGAL_ARGUMENT),
    ("SUB s1 SystemStatus t g extra", owp.ILLEGAL_ARGUMENT),
    ("SUB s!1 SystemStatus t", owp.ILLEGAL_ARGUMENT),
    ("SUB s1 SystemStatus t g#", owp.ILLEGAL_ARGUMENT),
    ("UNSUB", owp.ILLEGAL_ARGUMENT),
    ("UNSUB a b", owp.ILLEGAL_ARGUMENT),
])
def test_errors(text, code):
    with pytest.raises(owp.OwpError) as ei:
        owp.parse_client(text)
    assert ei.value.code == code


def test_format_and_client_parse():
    assert owp.fmt_err(owp.ILLEGAL_STATE, "a\nb") == "-ERR Illegal-State a b"
    assert owp.fmt_err(owp.INTERNAL_ERROR) == "-ERR Internal-Error"
    assert owp.parse_server('MSG s1 {"a": 1}').args == ("s1", '{"a": 1}')
    assert owp.parse_server("-ERR Illegal-State sub id in use").args == ("Illegal-State", "sub id in use")
