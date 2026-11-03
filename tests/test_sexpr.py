import pytest

from kicad_adapter import sexpr

from .conftest import SYMBOL_DIR


def test_parse_and_dump():
    doc = sexpr.parse('(a "b c" 1.5 (d (e 1)) (xy 1 2) (xy 3 4))')
    assert doc == ["a", '"b c"', "1.5", ["d", ["e", "1"]], ["xy", "1", "2"], ["xy", "3", "4"]]
    assert sexpr.dumps(doc) == '(a "b c" 1.5\n\t(d\n\t\t(e 1)\n\t)\n\t(xy 1 2) (xy 3 4)\n)\n'


@pytest.mark.parametrize("text", ["(a", "a)", "(a))", "", "(a) (b)"])
def test_parse_errors(text):
    with pytest.raises(sexpr.SExprError):
        sexpr.parse(text)


def test_quoting_round_trip():
    raw = 'say "hi"\\ok\nnext'
    atom = sexpr.q(raw)
    assert sexpr.parse(f"(x {atom})")[1] == atom
    assert sexpr.unquote(atom) == raw


@pytest.mark.parametrize(
    "value, text",
    [(0, "0"), (-0.0, "0"), (1.27, "1.27"), (88.9 + 2.54, "91.44"), (-3.81, "-3.81"),
     (1 / 3, "0.3333"), (100.00004, "100")],
)
def test_number_format(value, text):
    assert sexpr.num(value) == text


@pytest.mark.parametrize("name", ["Device.kicad_sym", "Connector_Generic.kicad_sym"])
def test_kicad_library_file_round_trips_byte_for_byte(name):
    text = (SYMBOL_DIR / name).read_text(encoding="utf-8")
    assert sexpr.dumps(sexpr.parse(text)) == text
