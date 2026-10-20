import make_sheets

LOCAL = ["1_a_0000", "2_b_0000"]
NAMES = [f"{i}_x_0000.{ext}" for i in range(200) for ext in ("json", "step")] + [
    f"{n}.json" for n in LOCAL
]


def test_pool_excludes_local_designs_and_is_seeded():
    pool = make_sheets.shuffled_pool(NAMES, set(LOCAL), seed=0)
    assert not set(pool) & set(LOCAL)
    assert len(pool) == 200
    assert pool == make_sheets.shuffled_pool(NAMES, set(LOCAL), seed=0)
    assert pool != make_sheets.shuffled_pool(NAMES, set(LOCAL), seed=1)


def test_split_is_70_30_seeded_and_disjoint():
    ids = [f"{i}_x_0000" for i in range(100)]
    test, validation = make_sheets.split(ids, test=70, seed=0)
    assert (len(test), len(validation)) == (70, 30)
    assert not set(test) & set(validation)
    assert set(test) | set(validation) == set(ids)
    assert (test, validation) == make_sheets.split(ids, test=70, seed=0)
