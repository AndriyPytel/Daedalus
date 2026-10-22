import make_sheets


def test_split_is_70_30_seeded_and_disjoint():
    ids = [f"{i}_x" for i in range(100)]
    test, validation = make_sheets.split(ids, 70, seed=0)
    assert (len(test), len(validation)) == (70, 30)
    assert not set(test) & set(validation)
    assert (test, validation) == make_sheets.split(ids, 70, seed=0)
    assert test != make_sheets.split(ids, 70, seed=1)[0]


def test_test_share_rounds_up():
    assert [make_sheets.test_count(n) for n in (150, 149, 10)] == [105, 105, 7]
