"""machine.toml loader. Values not yet measured on the machine are the string "TODO"."""

import tomllib

TODO = "TODO"

# Keys that must be measured before a drawing can be converted.
REQUIRED_FOR_CONVERT = [
    "drawing.svg_width_mm",
    "atc.slot0_deg",
    "atc.park_x",
    "atc.dock_x",
    "atc.release_x",
    "atc.slide_in_mm",
    "atc.slide_index_mm",
    "atc.slide_out_mm",
]


class MeasurementNeeded(Exception):
    """A config value is still TODO; never fall back to 0 on a real axis."""


def load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def get(cfg, key, default=None):
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def require(cfg, key):
    value = get(cfg, key)
    if value is None or value == TODO:
        raise MeasurementNeeded(f"{key}: not measured yet, fill it in machine.toml")
    return value


def missing(cfg, keys=REQUIRED_FOR_CONVERT):
    return [k for k in keys if get(cfg, k) in (None, TODO)]
