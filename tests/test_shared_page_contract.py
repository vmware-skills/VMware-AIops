from vmware_aiops.ops._paging import paginated_window


def test_legacy_last_page_and_aliases_are_preserved_with_a_stop_signal():
    result = paginated_window([{"n": 4}], total=5, limit=2, offset=4, owner="test")
    assert result["next_offset"] is None
    assert result["truncated"] is False
    assert result["hint"] is None
    assert result["owner"] == "test"
    assert result["offset"] == 4


def test_first_page_unlimited_and_empty_pages_terminate_correctly():
    assert paginated_window([{}, {}], total=5, limit=2, offset=0)["next_offset"] == 2
    assert paginated_window([{}, {}], total=2, limit=0, offset=0)["next_offset"] is None
    assert paginated_window([], total=5, limit=2, offset=9)["next_offset"] is None
