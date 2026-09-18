from precedent.decide import DecisionConfig, decide
from precedent.schema import Precedent

SIG = "issue_refund|amount,order_id|TRUSTED"
CFG = DecisionConfig()


def P(i, verdict, score, sig=SIG, source="seed"):
    return Precedent(id=str(i), text="", tool="issue_refund", verdict=verdict, sig=sig,
                     score=score, source=source, reason_code="over_limit" if verdict == "block" else "")


def test_no_precedent_escalates():
    assert decide(SIG, False, [], CFG).reason_code == "no_precedent"


def test_novel_shape_escalates():
    o = decide(SIG, True, [P(1, "allow", 0.9, sig="other|x|TRUSTED")] * 3, CFG)
    assert o.verdict == "escalate" and o.reason_code == "novel_shape"


def test_clear_allow():
    o = decide(SIG, True, [P(i, "allow", 0.95 - i * 0.001) for i in range(5)], CFG)
    assert o.verdict == "allow" and o.confidence > 0.9


def test_clear_block():
    o = decide(SIG, True, [P(i, "block", 0.95 - i * 0.001) for i in range(5)], CFG)
    assert o.verdict == "block"


def test_one_close_block_beats_many_allows():
    ps = [P(0, "block", 0.97)] + [P(i, "allow", 0.93) for i in range(1, 5)]
    assert decide(SIG, True, ps, CFG).verdict == "block"


def test_thin_allow_support_escalates():
    o = decide(SIG, True, [P(0, "allow", 0.95), P(1, "allow", 0.9, sig="other|x|TRUSTED")], CFG)
    assert o.verdict == "escalate"  # only one same-signature allow, needs min_allow_support


def test_human_precedent_carries_more_weight():
    base = [P(0, "allow", 0.95), P(1, "allow", 0.95), P(2, "allow", 0.95), P(3, "block", 0.93, source="seed")]
    with_human = base[:3] + [P(3, "block", 0.93, source="human")]
    a, b = decide(SIG, True, base, CFG), decide(SIG, True, with_human, CFG)
    assert b.p_block > a.p_block


def test_single_human_approval_is_enough_to_allow():
    o = decide(SIG, True, [P(0, "allow", 0.95, source="human")], CFG)
    assert o.verdict == "allow"
    assert decide(SIG, True, [P(0, "allow", 0.95, source="seed")], CFG).verdict == "escalate"


def test_other_signature_neighbours_do_not_outvote_exact_shape_ones():
    # Two closer-scoring blocked neighbours with a different signature vs three exact-shape approvals.
    other = "issue_refund|amount,order_id|TRUSTED|customer_match:no"
    ps = [P(0, "block", 0.97, sig=other), P(1, "block", 0.97, sig=other)]
    ps += [P(i + 2, "allow", 0.96) for i in range(3)]
    assert decide(SIG, True, ps, CFG).verdict == "allow"
    # ...and with equal weighting they would have been enough to stop it
    assert decide(SIG, True, ps, DecisionConfig(other_sig_weight=1.0)).verdict != "allow"
