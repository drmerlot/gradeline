import argparse


def main() -> None:
    p = argparse.ArgumentParser(prog="gradeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("boundary", help="Fetch WBD boundary and write a preview map")
    b.add_argument("watershed")
    f = sub.add_parser("fetch", help="Download raw layers into data/raw/<watershed>/")
    f.add_argument("watershed")
    f.add_argument("layers", nargs="*", help="dem10 dem1 nlcd soils fema buildings (default: all)")
    f.add_argument("--force", action="store_true")
    t = sub.add_parser("terrain", help="Build terrain products from a downloaded DEM")
    t.add_argument("watershed")
    t.add_argument("--res", type=int, default=10)
    for name, hlp in [("features", "Per-building features"), ("score", "Score buildings"), ("validate", "Check routing vs USGS gauges")]:
        sub.add_parser(name, help=hlp).add_argument("watershed")
    args = p.parse_args()

    if args.cmd == "boundary":
        from .boundary import build_boundary
        from .maps import boundary_map

        hucs, aoi = build_boundary(args.watershed)
        print(hucs[["huc12", "name", "areasqkm", "tohuc"]].to_string(index=False))
        print(f"total area: {hucs.areasqkm.sum():.1f} km2")
        print(f"map: {boundary_map(args.watershed, hucs, aoi)}")

    elif args.cmd == "fetch":
        from .fetch import FETCHERS

        for layer in args.layers or list(FETCHERS):
            print(f"[{layer}] ...", flush=True)
            print(f"[{layer}] -> {FETCHERS[layer](args.watershed, args.force)}", flush=True)

    elif args.cmd == "terrain":
        from .terrain import build_terrain

        print(f"terrain -> {build_terrain(args.watershed, args.res)}")

    elif args.cmd == "features":
        from .features import build_features

        f = build_features(args.watershed)
        print(f"{len(f)} buildings; terrain_res counts: {f.terrain_res.value_counts().to_dict()}")
    elif args.cmd == "validate":
        import pandas as pd

        from .validate import gauge_drainage_check

        print(pd.DataFrame(gauge_drainage_check(args.watershed)).to_string(index=False))
    elif args.cmd == "score":
        from .score import build_scores

        s = build_scores(args.watershed)
        print(s.score.describe().round(1).to_string())
        print(f"outside FEMA but risky: {int(s.outside_fema_but_risky.sum())}")
