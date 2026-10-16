"""docs/QA.md is made from the code: the register and the thresholds in it must be the ones the checker has."""
from reel_intel.quality import docs
from reel_intel.quality.base import REGISTRY


def test_the_generated_tables_of_the_document_are_up_to_date():
    assert docs.main(["--check"]) == 0


def test_every_registered_code_is_in_the_document():
    text = docs.path().read_text(encoding="utf-8")
    assert [c for c in REGISTRY if f"`{c}`" not in text] == []


def test_a_missing_block_is_an_error_not_a_silent_skip():
    import pytest
    with pytest.raises(ValueError):
        docs.render("# no blocks here")


def test_the_register_table_has_one_row_per_code_and_the_thresholds_table_is_not_empty():
    rows = docs.register_table().splitlines()[2:]
    assert len(rows) == len(REGISTRY)
    assert len(docs.thresholds_table().splitlines()) > 40
