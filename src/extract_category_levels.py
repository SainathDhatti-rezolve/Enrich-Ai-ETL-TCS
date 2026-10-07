"""Category-level expansion (post-transform, pre-upload).

Reads the Enrich AI CSV produced by the transform stage and appends:
  * ``category_l1..N``   — for each level, the values every path in the row has
                           at that level, comma-joined (paths that don't reach
                           the level are skipped).
  * ``category_path1..M`` — each raw breadcrumb path for the row.

N (max levels) and M (max paths per row) are derived from the whole file so the
column set is consistent across all rows.

Exposes :func:`expand_category_levels` for use by the pipeline, and a small CLI
(``python -m src.extract_category_levels <input.csv>``) for standalone use.
"""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import datetime
from pathlib import Path

try:  # Works both as part of the package and when run as a standalone script.
    from src.category_parsing import (
        build_level_and_path_columns,
        build_level_values,
        build_path_values,
        extract_paths,
    )
except ModuleNotFoundError:  # pragma: no cover - fallback for `python extract_category_levels.py`
    from category_parsing import (
        build_level_and_path_columns,
        build_level_values,
        build_path_values,
        extract_paths,
    )

logger = logging.getLogger(__name__)


def expand_category_levels(input_path: Path, output_path: Path) -> Path:
    """Append category level/path columns to the CSV at ``input_path``.

    Reads ``input_path``, derives the ``category_l*``/``category_path*`` columns
    from each row's ``category_names`` cell, and writes the result to
    ``output_path``. Returns ``output_path``. The input file is not modified.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    all_paths_per_row = [extract_paths(row.get("category_names", "")) for row in rows]
    max_levels, max_paths, level_columns, path_columns, levels_per_row = (
        build_level_and_path_columns(all_paths_per_row)
    )
    header = fieldnames + level_columns + path_columns

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row, raw_paths, levels_for_row in zip(rows, all_paths_per_row, levels_per_row):
            out_row = [row[col] for col in fieldnames]
            out_row.extend(build_level_values(levels_for_row, max_levels))
            out_row.extend(build_path_values(raw_paths, max_paths))
            writer.writerow(out_row)

    logger.info(
        "Category expansion: %d row(s), %d level column(s), %d path column(s) -> %s",
        len(rows),
        max_levels,
        max_paths,
        output_path,
    )
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add category_l*/category_path* columns to a CSV file."
    )
    parser.add_argument("input_file", help="Path to the input CSV file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_file)
    timestamp = datetime.now().strftime("%d%m%Y%H%M%S")
    output_path = input_path.with_name(f"{input_path.stem}{timestamp}.csv")
    expand_category_levels(input_path, output_path)
    print(f"Output written to: {output_path}")


if __name__ == "__main__":
    main()
