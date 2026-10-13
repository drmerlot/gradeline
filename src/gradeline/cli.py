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
