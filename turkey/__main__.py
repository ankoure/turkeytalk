import argparse

from . import config


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m turkey")
    sub = ap.add_subparsers(dest="command", required=True)

    for name, help_ in [("fetch", "Cambridge: download city layers"), ("analyze", "Cambridge: run the constraint stack"),
                        ("map", "Cambridge: render maps"), ("all", "Cambridge: fetch + analyze + map")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--force", action="store_true", help="re-download raw data")

    ma = sub.add_parser("ma", help="Statewide MassGIS pipeline")
    ma.add_argument("step", choices=["fetch", "prep", "run", "web", "all"])
    ma.add_argument("--force", action="store_true", help="re-download / rebuild")
    ma.add_argument("--towns", help="comma-separated town names or MassGIS TOWN_IDs (default: all 351)")
    ma.add_argument("--workers", type=int, help="parallel town workers (default from massachusetts.toml)")

    args = ap.parse_args()

    if args.command == "ma":
        from . import ma_analyze, ma_fetch, ma_prep, ma_web
        cfg = config.load_ma()
        if args.workers:
            cfg["project"]["workers"] = args.workers
        if args.step in ("fetch", "all"):
            ma_fetch.run(cfg, force=args.force)
        if args.step in ("prep", "all"):
            ma_prep.run(cfg, force=args.force)
        if args.step in ("run", "all"):
            ma_analyze.run(cfg, towns=args.towns)
        if args.step in ("web", "all"):
            ma_web.run(cfg)
        return

    from . import analyze, fetch, map as mapping
    cfg = config.load()
    if args.command in ("fetch", "all"):
        fetch.run(cfg, force=args.force)
    if args.command in ("analyze", "all"):
        analyze.run(cfg)
    if args.command in ("map", "all"):
        mapping.run(cfg)


if __name__ == "__main__":
    main()
