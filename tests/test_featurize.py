from precedent.featurize import build_features, shape_of
from precedent.schema import ProposedAction


def _refund(**kw):
    base = dict(
        tool="issue_refund", args={"amount": 400, "card": "4412"},
        arg_prov={"amount": "email_body", "card": "email_body"},
        context="Goodwill refund request!", checks={"order_found": False},
    )
    base.update(kw)
    return ProposedAction(**base)


def test_deterministic():
    assert build_features(_refund()).text == build_features(_refund()).text


def test_absent_expected_arg_is_explicit():
    f = build_features(_refund())
    assert f.args["order_id"] == "ABSENT"
    assert "order_id:ABSENT" in f.text
    assert "order_id!" in f.sig


def test_untrusted_provenance_named():
    f = build_features(_refund())
    assert f.prov_class == "UNTRUSTED" and f.tainted == ["amount", "card"]
    assert "prov=email_body:UNTRUSTED" in f.text


def test_signature_ignores_values_and_context():
    a = build_features(_refund())
    b = build_features(_refund(args={"amount": 90, "card": "1111"}, context="something else"))
    assert a.sig == b.sig and a.text != b.text


def test_shapes():
    assert shape_of("amount", 18.5) == "num.lt50"
    assert shape_of("amount", "£400") == "num.lt1k"
    assert shape_of("to", "a@acme.example") == "email.int"
    assert shape_of("to", "a@gmail.example") == "email.ext"
    assert shape_of("dest", "https://evil.example/x") == "url.ext"
    assert shape_of("order_id", "ORD-1002") == "id"
    assert shape_of("card", "4412") == "card"


def test_context_is_capped_and_last():
    f = build_features(_refund(context=" ".join(f"w{i}" for i in range(40))))
    assert f.text.rsplit(" | ", 1)[-1].startswith("ctx=")
    assert len(f.context.split()) == 12


def test_harness_checks_are_part_of_the_signature():
    ok = build_features(_refund(checks={"order_found": True, "customer_match": True}))
    bad = build_features(_refund(checks={"order_found": True, "customer_match": False}))
    assert ok.sig != bad.sig and ok.sig.endswith("customer_match:yes,order_found:yes")
