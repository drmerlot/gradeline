import argparse


def main() -> None:
    p = argparse.ArgumentParser(prog="gradeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("boundary", help="Fetch WBD boundary and write a preview map")
    b.add_argument("watershed")
    args = p.parse_args()

    if args.cmd == "boundary":
        from .boundary import build_boundary
        from .maps import boundary_map

        hucs, aoi = build_boundary(args.watershed)
        print(hucs[["huc12", "name", "areasqkm", "tohuc"]].to_string(index=False))
        print(f"total area: {hucs.areasqkm.sum():.1f} km2")
        print(f"map: {boundary_map(args.watershed, hucs, aoi)}")
