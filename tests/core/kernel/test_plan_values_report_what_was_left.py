from core.kernel.kernel import KernelResult


def test_a_result_carries_the_unharvested_names_off_the_wire():
    result = KernelResult.from_response(
        {"ok": True, "checkpoint": "c1", "plan_values_unharvested": {"solid": "Shape"}})

    assert result.plan_values_unharvested == {"solid": "Shape"}
