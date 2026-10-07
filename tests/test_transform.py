"""Tests for src.transform.

Uses synthetic CSVs written to pytest ``tmp_path``. No real client files.
Covers: single category, multiple categories, missing parent category, JSON
custom-field extraction, missing product group, the pure breadcrumb walk, exact
output column order, and an exact-string regression lock on the full CSV bytes.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from src.transform import (
    OUTPUT_COLUMNS,
    build_breadcrumb,
    transform,
)

PRODUCT_OUTPUT_HEADER = [
    "external_id",
    "family_id",
    "type",
    "name",
    "brand",
    "description",
    "image_url",
    "url",
    "custom_fields",
]

# Full set of custom-field keys the legacy source may provide.
FULL_CUSTOM_FIELDS = {
    "External_id": "P1",
    "Product_ID": "PID1",
    "Height": "10",
    "Length": "20",
    "Width": "5",
    "Weight": "2",
    "Bullets": "b1|b2",
    "Sku_ID": "SKU1",
    "Eco_Friendly": "Yes",
    "BPA_Free": "No",
    "Made_In_The_USA": "Yes",
}


def _write_csv(path: Path, rows: list[list[str]], header: list[str] | None = None) -> None:
    """Write ``rows`` to ``path`` as RFC-4180 CSV (correctly escaping inputs)."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if header is not None:
            writer.writerow(header)
        writer.writerows(rows)


def _make_sources(
    tmp_path: Path,
    *,
    product_rows: list[list[str]],
    group_rows: list[list[str]],
    category_link_rows: list[list[str]],
    category_tree_rows: list[list[str]],
) -> dict[str, Path]:
    """Create the four source CSVs plus an output path; return the path map."""
    paths = {
        "product_output": tmp_path / "product_output.csv",
        "product_groups": tmp_path / "product_groups.csv",
        "product_category": tmp_path / "product_category.csv",
        "category_output": tmp_path / "category_output.csv",
        "output": tmp_path / "enrich_ai.csv",
    }
    _write_csv(paths["product_output"], product_rows, header=PRODUCT_OUTPUT_HEADER)
    _write_csv(paths["product_groups"], group_rows)
    _write_csv(paths["product_category"], category_link_rows)
    _write_csv(paths["category_output"], category_tree_rows)
    return paths


def _product_row(external_id: str, custom_fields: dict[str, str]) -> list[str]:
    return [
        external_id,
        "F1",
        "product",
        "Cool Shoes",
        "Acme",
        "A nice shoe",
        "http://img/1.jpg",
        "http://site/1",
        json.dumps(custom_fields),
    ]


# Storage > Closet > Shoe Storage  and  Kitchen > Containers
CATEGORY_TREE = [
    ["Storage", "1", ""],
    ["Closet", "2", "1"],
    ["Shoe Storage", "3", "2"],
    ["Kitchen", "4", ""],
    ["Containers", "5", "4"],
]


def _run(tmp_path: Path, **kwargs) -> pd.DataFrame:
    """Run transform and read the output back preserving blanks/strings."""
    paths = _make_sources(tmp_path, **kwargs)
    transform(
        product_output_path=paths["product_output"],
        product_groups_path=paths["product_groups"],
        product_category_path=paths["product_category"],
        category_output_path=paths["category_output"],
        output_path=paths["output"],
    )
    return pd.read_csv(paths["output"], dtype=str, keep_default_na=False)


# --------------------------------------------------------------------------- #
# Pure breadcrumb walk
# --------------------------------------------------------------------------- #
def test_build_breadcrumb_walks_to_root() -> None:
    name_by_id = {"1": "Storage", "2": "Closet", "3": "Shoe Storage"}
    parent_by_id = {"1": "", "2": "1", "3": "2"}
    assert build_breadcrumb("3", name_by_id, parent_by_id) == "Storage > Closet > Shoe Storage"


def test_build_breadcrumb_missing_parent_stops_at_highest_resolvable() -> None:
    # Category 3's parent (99) does not exist -> chain stops at 3.
    name_by_id = {"3": "Shoe Storage"}
    parent_by_id = {"3": "99"}
    assert build_breadcrumb("3", name_by_id, parent_by_id) == "Shoe Storage"


# --------------------------------------------------------------------------- #
# Category concatenation and breadcrumbs
# --------------------------------------------------------------------------- #
def test_single_category(tmp_path: Path) -> None:
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"]],
        category_tree_rows=CATEGORY_TREE,
    )
    assert df.loc[0, "category_ids"] == "3"
    assert df.loc[0, "category_names"] == '"Storage > Closet > Shoe Storage"'


def test_multiple_categories_concatenate_ids_without_delimiter(tmp_path: Path) -> None:
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"], ["P1", "5"]],
        category_tree_rows=CATEGORY_TREE,
    )
    # Concatenated in file order, no commas/spaces/delimiters.
    assert df.loc[0, "category_ids"] == "35"
    assert (
        df.loc[0, "category_names"]
        == '"Storage > Closet > Shoe Storage","Kitchen > Containers"'
    )


def test_missing_parent_category(tmp_path: Path) -> None:
    # Category 5 points at a non-existent parent (99).
    tree = [["Orphan", "5", "99"]]
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "5"]],
        category_tree_rows=tree,
    )
    assert df.loc[0, "category_names"] == '"Orphan"'


# --------------------------------------------------------------------------- #
# JSON custom-field extraction
# --------------------------------------------------------------------------- #
def test_json_extraction_populates_c_columns(tmp_path: Path) -> None:
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"]],
        category_tree_rows=CATEGORY_TREE,
    )
    assert df.loc[0, "C:Product_ID"] == "PID1"
    assert df.loc[0, "C:Height"] == "10"
    assert df.loc[0, "C:Weight"] == "2"
    assert df.loc[0, "C:BPA_Free"] == "No"
    assert df.loc[0, "C:Made_In_The_USA"] == "Yes"


def test_json_extraction_missing_key_is_blank(tmp_path: Path) -> None:
    partial = {"Product_ID": "PID1", "Height": "10"}  # most keys absent
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", partial)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"]],
        category_tree_rows=CATEGORY_TREE,
    )
    assert df.loc[0, "C:Product_ID"] == "PID1"
    assert df.loc[0, "C:Weight"] == ""
    assert df.loc[0, "C:Sku_ID"] == ""


# --------------------------------------------------------------------------- #
# Groupings
# --------------------------------------------------------------------------- #
def test_missing_product_group_is_blank(tmp_path: Path) -> None:
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[],  # no group for P1
        category_link_rows=[["P1", "3"]],
        category_tree_rows=CATEGORY_TREE,
    )
    assert df.loc[0, "groupings"] == ""


def test_present_product_group_is_copied(tmp_path: Path) -> None:
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"]],
        category_tree_rows=CATEGORY_TREE,
    )
    assert df.loc[0, "groupings"] == "G100"


# --------------------------------------------------------------------------- #
# Output shape and blank TODO columns
# --------------------------------------------------------------------------- #
def test_output_column_order_is_exact(tmp_path: Path) -> None:
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"]],
        category_tree_rows=CATEGORY_TREE,
    )
    assert list(df.columns) == OUTPUT_COLUMNS


def test_todo_columns_are_blank(tmp_path: Path) -> None:
    df = _run(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"]],
        category_tree_rows=CATEGORY_TREE,
    )
    assert df.loc[0, "buckets"] == ""
    assert df.loc[0, "last_curation_update"] == ""


# --------------------------------------------------------------------------- #
# Regression: exact bytes of the full output CSV
# --------------------------------------------------------------------------- #
def test_regression_exact_output_bytes(tmp_path: Path) -> None:
    paths = _make_sources(
        tmp_path,
        product_rows=[_product_row("P1", FULL_CUSTOM_FIELDS)],
        group_rows=[["P1", "G100"]],
        category_link_rows=[["P1", "3"], ["P1", "5"]],
        category_tree_rows=CATEGORY_TREE,
    )
    transform(
        product_output_path=paths["product_output"],
        product_groups_path=paths["product_groups"],
        product_category_path=paths["product_category"],
        category_output_path=paths["category_output"],
        output_path=paths["output"],
    )
    expected = (
        "external_id,C:Product_ID,groupings,name,description,url,image_url,brand,"
        "category_ids,category_names,buckets,type,last_curation_update,C:BPA_Free,"
        "C:Bullets,C:Eco_Friendly,C:External_id,C:Height,C:Length,C:Made_In_The_USA,"
        "C:Sku_ID,C:Weight,C:Width\n"
        "P1,PID1,G100,Cool Shoes,A nice shoe,http://site/1,http://img/1.jpg,Acme,35,"
        '"""Storage > Closet > Shoe Storage"",""Kitchen > Containers""",'
        ",product,,No,b1|b2,Yes,P1,10,20,Yes,SKU1,2,5\n"
    )
    assert paths["output"].read_text(encoding="utf-8") == expected
