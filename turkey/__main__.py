import argparse

from . import analyze, config, fetch, map as mapping


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m turkey")
    ap.add_argument("command", choices=["fetch", "analyze", "map", "all"])
    ap.add_argument("--force", action="store_true", help="re-download raw data")
    args = ap.parse_args()
    cfg = config.load()
    if args.command in ("fetch", "all"):
        fetch.run(cfg, force=args.force)
    if args.command in ("analyze", "all"):
        analyze.run(cfg)
    if args.command in ("map", "all"):
        mapping.run(cfg)


if __name__ == "__main__":
    main()
