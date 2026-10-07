"""Tests for the category-level expansion step (post-transform, pre-upload).

Given the Enrich AI CSV's ``category_names`` cell (quoted breadcrumbs joined by
commas), this step appends ``category_l1..N`` (per-level, comma-joined across a
row's paths) and ``category_path1..M`` (each raw breadcrumb) columns.
"""

from __future__ import annotations

import csv
from pathlib import Path

from src.category_parsing import extract_paths, split_levels
from src.extract_category_levels import expand_category_levels


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def test_extract_paths_and_split_levels() -> None:
    value = '"Kitchen > Utensils > Knives","Sale > Cutlery > Kitchen Knives"'
    assert extract_paths(value) == [
        "Kitchen > Utensils > Knives",
        "Sale > Cutlery > Kitchen Knives",
    ]
    assert split_levels("Kitchen > Utensils > Knives") == ["Kitchen", "Utensils", "Knives"]
    assert extract_paths("") == []


def test_expand_appends_level_and_path_columns(tmp_path: Path) -> None:
    input_path = tmp_path / "in.csv"
    output_path = tmp_path / "out.csv"
    _write_csv(
        input_path,
        ["external_id", "category_names"],
        [
            ["P1", '"Kitchen > Utensils > Knives","Sale > Cutlery > Kitchen Knives"'],
            ["P2", '"Storage > Closet"'],
        ],
    )

    expand_category_levels(input_path, output_path)

    with output_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames
        rows = list(reader)

    # max levels across all paths = 3 (P1); max paths per row = 2 (P1).
    assert header == [
        "external_id",
        "category_names",
        "category_l1",
        "category_l2",
        "category_l3",
        "category_path1",
        "category_path2",
    ]

    p1 = rows[0]
    assert p1["category_l1"] == "Kitchen,Sale"
    assert p1["category_l2"] == "Utensils,Cutlery"
    assert p1["category_l3"] == "Knives,Kitchen Knives"
    assert p1["category_path1"] == "Kitchen > Utensils > Knives"
    assert p1["category_path2"] == "Sale > Cutlery > Kitchen Knives"

    p2 = rows[1]
    assert p2["category_l1"] == "Storage"
    assert p2["category_l2"] == "Closet"
    assert p2["category_l3"] == ""          # P2's single path has no 3rd level
    assert p2["category_path1"] == "Storage > Closet"
    assert p2["category_path2"] == ""       # P2 has only one path


def test_expand_preserves_original_columns_and_values(tmp_path: Path) -> None:
    input_path = tmp_path / "in.csv"
    output_path = tmp_path / "out.csv"
    _write_csv(
        input_path,
        ["external_id", "name", "category_names"],
        [["P1", "Cool Shoes", '"Storage > Closet > Shoe Storage"']],
    )

    expand_category_levels(input_path, output_path)

    with output_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["external_id"] == "P1"
    assert row["name"] == "Cool Shoes"
    assert row["category_names"] == '"Storage > Closet > Shoe Storage"'
    assert row["category_l1"] == "Storage"
    assert row["category_path1"] == "Storage > Closet > Shoe Storage"
