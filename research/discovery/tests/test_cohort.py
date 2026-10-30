import pandas as pd
import pytest

from wbdisc.cohort import (
    UNSPECIFIED_SECTOR, approval_fy, coverage_swaps, dominant_major_sector, fy_band, largest_remainder_allocation,
    share_table, split_project_ids, systematic_positions, volume_quartile,
)


def test_largest_remainder_allocation_is_exact_and_proportional():
    sizes = {"a": 634, "b": 601, "c": 16, "d": 1, "e": 1868}
    alloc = largest_remainder_allocation(sizes, 2000)
    assert sum(alloc.values()) == 2000
    assert all(abs(alloc[k] - 2000 * n / 3120) < 1 for k, n in sizes.items())


def test_largest_remainder_allocation_ties_are_deterministic():
    assert largest_remainder_allocation({"x": 1, "y": 1, "z": 1}, 2) == {"x": 1, "y": 1, "z": 0}


def test_largest_remainder_allocation_rejects_oversized_total():
    with pytest.raises(ValueError):
        largest_remainder_allocation({"a": 3}, 4)


@pytest.mark.parametrize("n_units,n_select,start", [(10, 3, 0.0), (10, 3, 0.99), (7, 7, 0.5), (1, 1, 0.3), (5, 0, 0.1)])
def test_systematic_positions_distinct_in_range(n_units, n_select, start):
    pos = systematic_positions(n_units, n_select, start)
    assert len(pos) == len(set(pos)) == n_select
    assert all(0 <= p < n_units for p in pos)


def test_systematic_positions_spread_over_ordered_units():
    assert systematic_positions(9, 3, 0.0) == [0, 3, 6]


def test_approval_fy_prefers_published_value_then_derives_wb_fiscal_year():
    assert approval_fy(["2008"], "2007-07-26T00:00:00Z") == (2008, "v3.fiscalyear")
    assert approval_fy("0", "2019-07-01T00:00:00Z") == (2020, "derived.boardapprovaldate")
    assert approval_fy(None, "2019-06-30T00:00:00Z") == (2019, "derived.boardapprovaldate")
    assert approval_fy(None, None) == (None, "missing")


def test_fy_band_edges():
    assert [fy_band(y) for y in (2014, 2015, 2020, 2021, 2024, None)] == [
        "FY<=2014", "FY2015-2017", "FY2018-2020", "FY2021-2023", "FY2024+", "Unknown"]


def test_dominant_major_sector_uses_largest_summed_percent():
    ms = [{"major_sector": {"major_sector_name": "FY17 - Health", "sectors": [{"sector_percent": "30"}]}},
          {"major_sector": {"major_sector_name": "FY17 - Education",
                            "sectors": [{"sector_percent": "25"}, {"sector_percent": "45"}]}}]
    assert dominant_major_sector(ms) == "Education"
    assert dominant_major_sector(None) == UNSPECIFIED_SECTOR


def test_volume_quartile_equal_frequency_with_ties():
    q = volume_quartile(pd.Series([1, 1, 1, 1, 5, 5, 9, 9]))
    assert q.value_counts().to_dict() == {"Q1": 2, "Q2": 2, "Q3": 2, "Q4": 2}


def test_split_project_ids_drops_non_project_tokens():
    assert split_project_ids("P003606,P046829") == ["P003606", "P046829"]
    assert split_project_ids(["P123456", "IFC-00043487"]) == ["P123456"]
    assert split_project_ids(None) == []


def test_coverage_swaps_stay_within_stratum_and_keep_size():
    pool = pd.DataFrame({
        "project_id": ["P1", "P2", "P3", "P4", "P5", "P6"],
        "country_code": ["AA", "AA", "AA", "BB", "BB", "CC"],
        "stratum_id": ["s1", "s1", "s2", "s1", "s2", "s2"],
        "random_key": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
    })
    selected = {"P1", "P2", "P3"}
    log = coverage_swaps(pool, selected, fraction=0.5)
    assert len(selected) == 3
    assert {s["group"] for s in log} == {"BB"}  # CC has a single eligible unit: not forced in
    swap = log[0]
    assert pool.set_index("project_id").loc[swap["swapped_in"], "stratum_id"] == swap["stratum_id"]
    assert pool.set_index("project_id").loc[swap["swapped_out"], "stratum_id"] == swap["stratum_id"]


def test_share_table_reports_absolute_deviation():
    pool = pd.DataFrame({"r": ["a"] * 6 + ["b"] * 4})
    cohort = pd.DataFrame({"r": ["a"] * 3 + ["b"] * 2})
    t = share_table(pool, cohort, "r")
    assert t.abs_diff_pp.max() == 0
