"""`sos` command line: new, list, build, still, check."""
from __future__ import annotations

import argparse
import functools
import http.server
import logging
import os
import sys
import tomllib
from pathlib import Path

from .build import BuildError, BuildOptions, build, check, default_workers, still
from .envfile import load_env_file
from .recipe import RecipeError, default_search_paths, slugify

LOG = logging.getLogger("sosforge")

TEMPLATE = '''# sos-forge recipe. Preview:  sos build {slug} --preview     Full size:  sos build {slug}
# Schema: docs/recipe-reference.md

[dataset]
name = "{title}"
description = "One or two sentences that presenters will see in the SOS playlist."
output = "texture"            # "texture" = one still image, "movie" = animation ([animation] below)
width = 4096
credits = ["Replace with your data source"]

# [animation]                 # uncomment with output = "movie"
# frames = 600
# fps = 30
# loop = false
# start = "2008-01-01"        # dates let points appear over time (points: time = "<column>")
# end = "today"

[[layers]]
type = "basemap"
name = "black_marble"         # black_marble | blue_marble | blue_marble_relief, or path = "my_map.jpg", or color = "#000000"
brightness = 0.5

[[layers]]
type = "points"
source = {{ type = "csv", path = "data/places.csv" }}   # columns: lat, lon (+ anything else)
weight = "visitors"           # optional numeric column for brightness
weight_scale = "sqrt"
color = "#ffb000"
radius_km = 40
halo_km = 250

[hud]
title = "{title}"
subtitle = "{{count:,}} places"
'''

SAMPLE_CSV = """name,lat,lon,visitors
London,51.5072,-0.1276,100
New York,40.7128,-74.0060,90
Tokyo,35.6762,139.6503,120
Sao Paulo,-23.5558,-46.6396,60
Nairobi,-1.2921,36.8219,30
Sydney,-33.8688,151.2093,50
Mumbai,19.0760,72.8777,80
Reykjavik,64.1466,-21.9426,10
"""


def cmd_new(args) -> int:
    """Scaffold recipes/<name>/ with a commented recipe.toml and a sample CSV that builds immediately. Tested by test_cli.py::test_new_list_check_and_resolve."""
    slug = slugify(args.name)
    folder = Path(args.dir) / slug
    if folder.exists():
        raise RecipeError(f"{folder} already exists")
    (folder / "data").mkdir(parents=True)
    (folder / "recipe.toml").write_text(TEMPLATE.format(slug=slug, title=args.name.strip().title()))
    (folder / "data" / "places.csv").write_text(SAMPLE_CSV)
    print(f"created {folder}/recipe.toml and {folder}/data/places.csv\nnext: sos build {folder} --preview")
    return 0


def cmd_list(args) -> int:
    """Print every recipe found in ./recipes and the bundled recipes folder with its output type. Tested by test_cli.py::test_new_list_check_and_resolve."""
    seen = set()
    for root in default_search_paths():
        for toml in sorted(root.glob("*/recipe.toml")):
            if toml.parent.name in seen:
                continue
            seen.add(toml.parent.name)
            try:
                ds = tomllib.loads(toml.read_text()).get("dataset", {})
                print(f"{toml.parent.name:28s} {ds.get('output', 'texture'):8s} {ds.get('name', '')}")
            except tomllib.TOMLDecodeError as exc:
                print(f"{toml.parent.name:28s} INVALID  {exc}")
    return 0


def _opts(args) -> BuildOptions:
    """Translate parsed arguments into BuildOptions. Tested by test_cli.py::test_new_list_check_and_resolve."""
    return BuildOptions(preview=args.preview, width=args.width, frames=getattr(args, "frames", None),
                        seconds=getattr(args, "seconds", None), hud=getattr(args, "hud", None),
                        fps=getattr(args, "fps", None), workers=args.workers, codecs=getattr(args, "codec", None),
                        crf=getattr(args, "crf", None), preset=getattr(args, "preset", None), out_dir=Path(args.out),
                        build_dir=Path(args.build_dir), refresh=args.refresh, strict=not args.no_strict,
                        frames_dir=Path(args.frames_dir) if getattr(args, "frames_dir", None) else None)


def cmd_build(args) -> int:
    """Build a recipe into an SOS deployment folder and print where it went. Tested by test_build.py through build(); the CLI wrapper is exercised manually."""
    summary = build(args.recipe, _opts(args))
    print(f"\n{summary['files']['data']}\n{summary['files']['playlist']}\n{summary['files']['readme']}")
    return 0


def cmd_still(args) -> int:
    """Render one frame to PNG for visual checks. Tested by test_build.py through still(); the CLI wrapper is exercised manually."""
    print(still(args.recipe, _opts(args), args.frame, Path(args.output) if args.output else None))
    return 0


def cmd_check(args) -> int:
    """Fetch data, prepare layers and run checks without rendering, exiting non-zero on any failure. Tested by test_cli.py::test_new_list_check_and_resolve."""
    return 0 if all(c.passed for c in check(args.recipe, _opts(args))) else 1


def cmd_bundle(args) -> int:
    """Zip a slug's built dataset folders, and a browser preview of each other version, for a GitHub Release upload. Tested by test_site.py::test_bundle_and_folder_dataset and test_version_previews through bundle(); the CLI wrapper is exercised manually."""
    from .site import bundle
    for z in bundle(args.slug, Path(args.dist)):
        print(z)
    return 0


def cmd_site(args) -> int:
    """Build the sos.stevenpenza.com site into a folder, optionally serving it locally for a look. Tested by test_site.py::test_build_site_end_to_end through build_site(); serving is exercised manually."""
    from .site import build_site
    out = Path(args.out)
    manifest = build_site(Path(args.config), out, dist=Path(args.dist) if args.dist else None,
                          ask_base="" if args.no_ask else (args.ask_base or os.environ.get("SOSFORGE_ASK_BASE")),
                          check_downloads=not args.no_check_downloads)
    print(f"{out}  ({len(manifest['datasets'])} datasets)")
    if args.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
        print(f"serving http://localhost:{args.serve}/  (Ctrl-C to stop)")
        http.server.ThreadingHTTPServer(("127.0.0.1", args.serve), handler).serve_forever()
    return 0


def cmd_upload_help(args) -> int:
    """Upload the help docs to Ask & Answer with the pinned corpus.py and the key in $ASK_UPLOAD_KEY, as a release does. Tested by test_site.py::test_upload_help_uses_the_pinned_corpus through upload_help(); the CLI wrapper runs in tools/release.sh."""
    from .site import load_config, upload_help
    upload_help(args.ask_base or os.environ.get("SOSFORGE_ASK_BASE") or load_config(Path(args.config))["ask_base"])
    return 0


def parser() -> argparse.ArgumentParser:
    """Build the argparse tree for all subcommands. Tested by test_cli.py::test_new_list_check_and_resolve."""
    ap = argparse.ArgumentParser(prog="sos", description="Build NOAA Science On a Sphere datasets from recipes.")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="scaffold a new recipe")
    p.add_argument("name")
    p.add_argument("--dir", default="recipes")
    p.set_defaults(fn=cmd_new)
    sub.add_parser("list", help="list available recipes").set_defaults(fn=cmd_list)
    for name, fn, hlp in (("build", cmd_build, "render and package a recipe"), ("still", cmd_still, "render one frame"),
                          ("check", cmd_check, "fetch data and run checks only")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("recipe", help="recipe name, folder or recipe.toml")
        if name == "still":
            p.add_argument("frame", type=int)
            p.add_argument("--output", "-o")
        p.add_argument("--preview", action="store_true", help="1024x512 quick render")
        p.add_argument("--width", type=int)
        p.add_argument("--workers", type=int, default=default_workers())
        p.add_argument("--out", default="dist")
        p.add_argument("--build-dir", default="build")
        p.add_argument("--refresh", action="store_true", help="re-download sources and rebuild caches")
        p.add_argument("--no-strict", action="store_true", help="warn instead of failing on checks")
        if name == "build":
            p.add_argument("--frames", type=int)
            p.add_argument("--seconds", type=int,
                           help="movie length in whole seconds at the recipe's fps; a length other than the recipe's "
                                "tags the output folders and files _<N>s so lengths install side by side")
            p.add_argument("--hud", choices=["panels", "labels"],
                           help="show a movie's text painted into the picture (panels) or as SOS labels; a style other "
                                "than the recipe's tags the outputs _panels or _labels")
            p.add_argument("--fps", type=int)
            p.add_argument("--codec", action="append", choices=["mpeg4", "h264"],
                           help="encode only these codecs (repeatable; first is primary)")
            p.add_argument("--crf", type=int)
            p.add_argument("--preset")
            p.add_argument("--frames-dir", help="also save every frame as PNG")
        p.set_defaults(fn=fn)
    p = sub.add_parser("bundle", help="zip a dataset's built folders and version previews for a GitHub Release")
    p.add_argument("slug")
    p.add_argument("--dist", default="dist")
    p.set_defaults(fn=cmd_bundle)
    p = sub.add_parser("site", help="build the sos.stevenpenza.com site (gallery, simulator, help, Ask)")
    p.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "site" / "site.toml"))
    p.add_argument("--out", default="build/site")
    p.add_argument("--dist", help="use built dataset folders from here instead of release downloads")
    p.add_argument("--ask-base", help="Ask & Answer API base (default $SOSFORGE_ASK_BASE, then site.toml's ask_base); the "
                                      "help pages and the ask box are built from the release ask.lock.json pins, fetched from it")
    p.add_argument("--no-ask", action="store_true", help="build without Ask & Answer: no help pages or ask box, and Help "
                                                         "links point at the docs on GitHub")
    p.add_argument("--no-check-downloads", action="store_true",
                   help="skip checking that every download (and length) in site.toml is on its release")
    p.add_argument("--serve", type=int, nargs="?", const=8040, help="serve the result locally on this port")
    p.set_defaults(fn=cmd_site)
    p = sub.add_parser("upload-help", help="upload the help docs to Ask & Answer with the key in $ASK_UPLOAD_KEY "
                                           "(a release does this)")
    p.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "site" / "site.toml"))
    p.add_argument("--ask-base", help="Ask & Answer API base (default $SOSFORGE_ASK_BASE, then site.toml's ask_base)")
    p.set_defaults(fn=cmd_upload_help)
    return ap


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `sos` console script that loads ./.env.local and reports recipe and build problems clearly. Tested by test_cli.py::test_new_list_check_and_resolve and test_env_local_loaded_without_override."""
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    loaded = load_env_file(Path.cwd() / ".env.local")
    if loaded:
        LOG.info("loaded %s from .env.local", ", ".join(loaded))
    try:
        return args.fn(args)
    except (RecipeError, BuildError, KeyError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
