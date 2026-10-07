import re

PATH_RE = re.compile(r'"([^"]*)"')


def extract_paths(category_names_value):
    """Return the raw path strings from a category_names cell, e.g.
    ['Kitchen > Utensils > Knives', 'Sale > Cutlery > Kitchen Knives']."""
    if not category_names_value:
        return []
    return PATH_RE.findall(category_names_value)


def split_levels(path):
    """Split a raw path string into its individual, stripped levels."""
    return [level.strip() for level in path.split(">")]


def build_level_and_path_columns(all_paths_per_row):
    """Given a list of rows, each a list of raw path strings, return
    (max_levels, max_paths, level_column_names, path_column_names)."""
    levels_per_row = [
        [split_levels(path) for path in paths] for paths in all_paths_per_row
    ]

    max_levels = max(
        (len(levels) for row_levels in levels_per_row for levels in row_levels),
        default=0,
    )
    max_paths = max((len(paths) for paths in all_paths_per_row), default=0)

    level_columns = [f"category_l{i}" for i in range(1, max_levels + 1)]
    path_columns = [f"category_path{i}" for i in range(1, max_paths + 1)]

    return max_levels, max_paths, level_columns, path_columns, levels_per_row


def build_level_values(levels_for_row, max_levels):
    """For one row's parsed levels (list of level-lists), build the
    category_l1..N values: each is a comma-joined list of the values every
    path in the row has at that level, skipping paths that don't reach it."""
    values = []
    for level_index in range(max_levels):
        level_values = [
            levels[level_index]
            for levels in levels_for_row
            if level_index < len(levels)
        ]
        values.append(",".join(level_values))
    return values


def build_path_values(raw_paths, max_paths):
    """For one row's raw path strings, build the category_path1..M values,
    blank for any index beyond how many paths this row has."""
    return [
        raw_paths[i] if i < len(raw_paths) else "" for i in range(max_paths)
    ]
