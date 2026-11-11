import numpy as np
import pytest

from leg_statements.allocation import allocate, allocate_members, integerize, water_fill


def alloc(c, p, key="proportional", shares=None, frac=0.5):
    c = np.atleast_2d(np.array(c, dtype=np.int64))
    p = np.array(p, dtype=np.int64).reshape(len(c))
    shares = np.ones(c.shape[1]) if shares is None else np.array(shares, dtype=float)
    return allocate_members(c, p, key, shares, frac)


def test_proportional_hand_example_partial_coverage():
    # 400 Wh of PV, members use 300 and 500 -> 150 / 250
    assert alloc([[300, 500]], [400]).tolist() == [[150, 250]]


def test_full_coverage_gives_everyone_their_consumption():
    assert alloc([[300, 500]], [1000]).tolist() == [[300, 500]]


def test_largest_remainder_rounding_keeps_total():
    # 10 Wh shared by three equal members -> 4/3/3 (tie broken by position), never 3/3/3
    assert alloc([[10, 10, 10]], [10]).tolist() == [[4, 3, 3]]


def test_static_unused_share_is_reoffered_by_shares():
    # entitlements 300/150/150, member 0 needs only 100 -> its 200 go to the others 1:1
    a = alloc([[100, 500, 500]], [600], "static", [0.5, 0.25, 0.25])
    assert a.tolist() == [[100, 250, 250]]


def test_static_member_without_share_gets_only_leftover():
    a = alloc([[100, 200]], [250], "static", [1.0, 0.0])
    assert a.tolist() == [[100, 150]]
    a = alloc([[300, 200]], [250], "static", [1.0, 0.0])
    assert a.tolist() == [[250, 0]]


def test_hybrid_extremes_match_pure_keys():
    rng = np.random.default_rng(1)
    c = rng.integers(0, 800, (200, 6))
    p = rng.integers(0, 3000, 200)
    s = rng.uniform(0.1, 1, 6)
    assert np.array_equal(allocate_members(c, p, "hybrid", s, 0.0), allocate_members(c, p, "proportional", s))
    assert np.array_equal(allocate_members(c, p, "hybrid", s, 1.0), allocate_members(c, p, "static", s))


@pytest.mark.parametrize("key", ["proportional", "static", "hybrid"])
def test_invariants_random(key):
    rng = np.random.default_rng(42)
    c = rng.integers(0, 2000, (3000, 9))
    c[rng.random(c.shape) < 0.2] = 0
    p = rng.integers(0, 12000, (3000, 2))
    p[rng.random(p.shape) < 0.3] = 0
    a = allocate(c, p, key, rng.uniform(0, 1, 9), 0.3)
    e = np.minimum(p.sum(1), c.sum(1))
    assert np.array_equal(a.community_wh, e)
    assert np.array_equal(a.member_wh.sum(1), e)
    assert (a.member_wh >= 0).all() and (a.member_wh <= c).all()
    assert np.array_equal(a.regulatory_wh.sum(1), e)
    assert np.array_equal(a.sold_wh.sum(1), e)
    assert np.array_equal(a.sold_wh + a.export_wh, p)
    assert (a.export_wh >= 0).all()


def test_no_production_gives_nothing():
    a = allocate(np.array([[100, 200]]), np.array([[0]]), "static", np.array([1.0, 1.0]))
    assert a.member_wh.tolist() == [[0, 0]] and a.export_wh.tolist() == [[0]]


def test_producer_split_follows_feed_in():
    a = allocate(np.array([[300, 300]]), np.array([[900, 300]]), "proportional", np.ones(2))
    assert a.sold_wh.tolist() == [[450, 150]]
    assert a.export_wh.tolist() == [[450, 150]]


def test_negative_input_and_unknown_key_rejected():
    with pytest.raises(ValueError):
        allocate(np.array([[-1, 2]]), np.array([[5]]), "proportional", np.ones(2))
    with pytest.raises(ValueError):
        allocate_members(np.array([[1, 2]]), np.array([5]), "lottery", np.ones(2))


def test_water_fill_saturates_and_respects_weights():
    out = water_fill(np.array([1000.0, 30.0]), np.array([[1.0, 2.0], [1.0, 2.0]]), np.array([[100.0, 100.0], [100.0, 100.0]]))
    assert out[0].tolist() == [100.0, 100.0]
    assert np.allclose(out[1], [10.0, 20.0])


def test_integerize_handles_float_noise_both_ways():
    y = np.array([[0.9999999999, 1.0000000001, 1.0]])
    x = integerize(y, np.array([[1, 2, 1]]), np.array([3]))
    assert x.sum() == 3 and (x <= [1, 2, 1]).all()
    x = integerize(np.array([[2.9999999999, 0.0]]), np.array([[3, 3]]), np.array([2]))
    assert x.tolist() == [[2, 0]]
