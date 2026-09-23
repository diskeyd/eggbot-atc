import argparse
import sys
from pathlib import Path

from . import config
from .atc import insert_tool_changes
from .grbl_settings import settings
from .svg2gcode import convert


def main(argv=None):
    p = argparse.ArgumentParser(prog="eggbot-atc")
    p.add_argument("--config", default="machine.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="list config values still marked TODO")
    c = sub.add_parser("convert", help="SVG or G-code -> G-code with ATC macros")
    c.add_argument("input")
    c.add_argument("-o", "--output", required=True)
    sub.add_parser("settings", help="print grbl $ settings")
    s = sub.add_parser("send", help="stream a G-code file over serial (debug only)")
    s.add_argument("gcode")
    s.add_argument("--port", required=True)
    s.add_argument("--baud", type=int, default=115200)
    args = p.parse_args(argv)
    cfg = config.load(args.config)

    if args.cmd == "check":
        todo = config.missing(cfg)
        for k in todo:
            print(f"TODO  {k}")
        print("all measured" if not todo else f"{len(todo)} value(s) to measure, see README")
        return 2 if todo else 0
    if args.cmd == "convert":
        todo = config.missing(cfg)
        if todo:
            print("cannot convert, measure first:\n  " + "\n  ".join(todo), file=sys.stderr)
            return 2
        src = Path(args.input)
        if src.suffix.lower() == ".svg":
            lines = convert(src, cfg)
        else:
            lines = insert_tool_changes(cfg, src.read_text().splitlines())
        Path(args.output).write_text("\n".join(lines) + "\n")
        print(f"wrote {args.output} ({len(lines)} lines)")
        return 0
    if args.cmd == "settings":
        print("\n".join(settings(cfg)))
        return 0
    if args.cmd == "send":
        from .send import send

        send(args.port, Path(args.gcode).read_text().splitlines(), args.baud)
        return 0


if __name__ == "__main__":
    sys.exit(main())
