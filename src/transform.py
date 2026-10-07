"""Transform stage: merge the four Legacy Workbench sources into Enrich AI CSV.

The transform is pure with respect to the filesystem inputs: it reads the four
read-only source CSVs, builds a single output DataFrame with an exact, fixed
column order, and writes it as strict (RFC-4180) CSV.

Business rules implemented (see project spec):
  * external_id, name, description, url, image_url, brand, type -> direct copy.
  * groupings          -> first group_id for the product (blank if none).
  * category_ids       -> every category_id for the product concatenated with
                          NO delimiter, in the order found in product_category.
  * category_names     -> one root->leaf breadcrumb per category, each wrapped
                          in double quotes, joined with commas.
  * C:<Key> columns    -> extracted from the custom_fields JSON blob.
  * buckets, last_curation_update -> intentionally blank (TODO).
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Output schema (EXACT order — do not change)
# --------------------------------------------------------------------------- #
OUTPUT_COLUMNS: list[str] = [
    "external_id",
    "C:Product_ID",
    "groupings",
    "name",
    "description",
    "url",
    "image_url",
    "brand",
    "category_ids",
    "category_names",
    "buckets",
    "type",
    "last_curation_update",
    "C:BPA_Free",
    "C:Bullets",
    "C:Eco_Friendly",
    "C:External_id",
    "C:Height",
    "C:Length",
    "C:Made_In_The_USA",
    "C:Sku_ID",
    "C:Weight",
    "C:Width",
]

# Custom-field columns are exactly those output columns prefixed with "C:".
# The source JSON key is the text after the prefix (e.g. "C:Height" -> "Height").
_CUSTOM_PREFIX = "C:"
CUSTOM_FIELD_COLUMNS: list[str] = [c for c in OUTPUT_COLUMNS if c.startswith(_CUSTOM_PREFIX)]

# Direct copies from product_output.csv: output column -> source column.
_DIRECT_COPY: dict[str, str] = {
    "external_id": "external_id",
    "name": "name",
    "description": "description",
    "url": "url",
    "image_url": "image_url",
    "brand": "brand",
    "type": "type",
}

# Columns the client has not yet defined a source for. Left blank on purpose.
# TODO(client): 'buckets' source not yet defined by the client.
# TODO(client): 'last_curation_update' source not yet defined by the client.
_BLANK_COLUMNS: tuple[str, ...] = ("buckets", "last_curation_update")

_BREADCRUMB_SEP = " > "


@dataclass(frozen=True)
class SourceFrames:
    """The four Legacy Workbench source tables, read as strings."""

    product_output: pd.DataFrame
    product_groups: pd.DataFrame
    product_category: pd.DataFrame
    category_output: pd.DataFrame


# --------------------------------------------------------------------------- #
# Reading (read-only)
# --------------------------------------------------------------------------- #
def _read_csv(path: Path, columns: list[str] | None) -> pd.DataFrame:
    """Read a source CSV as all-strings, preserving blanks as empty strings.

    ``columns`` is ``None`` for a file that already has a header, or an explicit
    list for the headerless sources. Files are only ever opened for reading.
    """
    header = 0 if columns is None else None
    frame = pd.read_csv(
        path,
        header=header,
        names=columns,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )
    return frame


def read_sources(
    *,
    product_output_path: Path,
    product_groups_path: Path,
    product_category_path: Path,
    category_output_path: Path,
) -> SourceFrames:
    """Read the four source CSVs into a :class:`SourceFrames` (read-only)."""
    return SourceFrames(
        product_output=_read_csv(product_output_path, None),
        product_groups=_read_csv(product_groups_path, ["external_id", "group_id"]),
        product_category=_read_csv(product_category_path, ["external_id", "category_id"]),
        category_output=_read_csv(
            category_output_path,
            ["category_name", "category_id", "parent_category_id"],
        ),
    )


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def build_breadcrumb(
    category_id: str,
    name_by_id: dict[str, str],
    parent_by_id: dict[str, str],
) -> str:
    """Return the root->leaf breadcrumb for ``category_id``.

    Walks upward via ``parent_by_id`` until a category has no parent, its parent
    is unknown (missing parent category), or a cycle is detected, then joins the
    resolved names root-first with ' > '. An unknown starting id yields "".
    """
    names: list[str] = []
    seen: set[str] = set()
    current = category_id
    while current and current in name_by_id and current not in seen:
        seen.add(current)
        names.append(name_by_id[current])
        current = parent_by_id.get(current, "")
    return _BREADCRUMB_SEP.join(reversed(names))


def _parse_custom_fields(raw: str) -> dict[str, str]:
    """Parse the custom_fields JSON blob into a str->str mapping.

    Returns an empty mapping for blank or invalid JSON so extraction degrades to
    blank columns rather than failing the whole run.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Skipping unparseable custom_fields value: %r", raw)
        return {}
    if not isinstance(data, dict):
        logger.warning("custom_fields JSON was not an object: %r", raw)
        return {}
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


# --------------------------------------------------------------------------- #
# Transformation
# --------------------------------------------------------------------------- #
def build_output_dataframe(sources: SourceFrames) -> pd.DataFrame:
    """Build the Enrich AI output DataFrame from the source frames."""
    # Lookups for the category tree.
    name_by_id = dict(
        zip(sources.category_output["category_id"], sources.category_output["category_name"])
    )
    parent_by_id = dict(
        zip(
            sources.category_output["category_id"],
            sources.category_output["parent_category_id"],
        )
    )

    # First group_id per product (order preserved by the source file).
    group_by_external: dict[str, str] = {}
    for external_id, group_id in zip(
        sources.product_groups["external_id"], sources.product_groups["group_id"]
    ):
        group_by_external.setdefault(external_id, group_id)

    # Ordered list of category_ids per product (order preserved by the file).
    categories_by_external: dict[str, list[str]] = {}
    for external_id, category_id in zip(
        sources.product_category["external_id"], sources.product_category["category_id"]
    ):
        categories_by_external.setdefault(external_id, []).append(category_id)

    rows: list[dict[str, str]] = []
    for record in sources.product_output.to_dict(orient="records"):
        external_id = record["external_id"]
        category_ids = categories_by_external.get(external_id, [])
        custom = _parse_custom_fields(record.get("custom_fields", ""))

        row: dict[str, str] = {column: "" for column in OUTPUT_COLUMNS}

        for out_col, src_col in _DIRECT_COPY.items():
            row[out_col] = record.get(src_col, "")

        row["groupings"] = group_by_external.get(external_id, "")

        # Concatenate every category_id with NO delimiter, in source order.
        row["category_ids"] = "".join(category_ids)

        # One quoted breadcrumb per category, joined with commas.
        row["category_names"] = ",".join(
            f'"{build_breadcrumb(cid, name_by_id, parent_by_id)}"' for cid in category_ids
        )

        for column in CUSTOM_FIELD_COLUMNS:
            row[column] = custom.get(column[len(_CUSTOM_PREFIX):], "")

        # Columns whose client source is not yet defined stay blank on purpose.
        for column in _BLANK_COLUMNS:
            row[column] = ""

        rows.append(row)

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS, dtype=str)


def write_output(frame: pd.DataFrame, output_path: Path) -> Path:
    """Write ``frame`` to ``output_path`` as strict CSV and return the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        output_path,
        index=False,
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
        encoding="utf-8",
    )
    return output_path


def transform(
    *,
    product_output_path: Path,
    product_groups_path: Path,
    product_category_path: Path,
    category_output_path: Path,
    output_path: Path,
) -> Path:
    """Read the four sources, transform, and write the Enrich AI CSV.

    Returns the written output path. Source files are never modified.
    """
    logger.info("Transforming sources into %s", output_path)
    sources = read_sources(
        product_output_path=Path(product_output_path),
        product_groups_path=Path(product_groups_path),
        product_category_path=Path(product_category_path),
        category_output_path=Path(category_output_path),
    )
    frame = build_output_dataframe(sources)
    written = write_output(frame, Path(output_path))
    logger.info("Wrote %d product row(s) to %s", len(frame), written)
    return written
