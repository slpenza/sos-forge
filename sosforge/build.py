"""Build orchestration: recipe -> layers (prepared once, cached) -> checks -> frames in parallel -> JPG/PNG or MP4
-> SOS deployment folder.  Workers are spawned processes that rebuild the recipe and memory-map each layer's cache."""
from __future__ import annotations

import collections
import contextlib
import logging
import multiprocessing as mp
import os
import string
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .checks import Check, log_checks
from .context import BuildContext
from .encode import ffmpeg_command, find_ffmpeg, probe_video, validate_video
from .grid import linear_to_srgb8, luminance, seam_ratio
from .hud import HudRenderer, polar_safe, render_template, sample_indices
from .package import build_package
from .recipe import Recipe, RecipeError, load_recipe
from .registry import LAYERS
from .timeline import Timeline

LOG = logging.getLogger("sosforge.build")
PREVIEW_WIDTH = 1024
LICENSE_NOTES = {
    "CC-BY-SA-4.0": "Contains CC BY-SA 4.0 data: share this dataset under CC BY-SA 4.0 and keep the credits.",
    "ODbL-1.0": "Contains OpenStreetMap data (ODbL 1.0): this image is a Produced Work; keep '© OpenStreetMap contributors'.",
}


class BuildError(RuntimeError):
    """Raised when strict verification fails or the encoder errors. Tested by test_build.py::test_strict_failure_raises."""


def default_workers() -> int:
    """Return the default render process count, leaving half the cores for the encoder. Not tested; it only reads os.cpu_count()."""
    return max(1, (os.cpu_count() or 2) // 2)


@dataclass
class BuildOptions:
    """Command-line overrides for one build. Tested indirectly by test_cli.py and test_build.py."""

    preview: bool = False
    width: int | None = None
    frames: int | None = None
    seconds: int | None = None
    hud: str | None = None
    fps: int | None = None
    workers: int = field(default_factory=default_workers)
    codecs: list[str] | None = None
    crf: int | None = None
    preset: str | None = None
    out_dir: Path = Path("dist")
    build_dir: Path = Path("build")
    refresh: bool = False
    strict: bool = True
    frames_dir: Path | None = None


def size_label(width: int) -> str:
    """Return the conventional size tag used in data file names, such as 4k or 1k. Tested by test_package.py::test_playlist_and_readme."""
    return {8192: "8k", 4096: "4k", 2048: "2k", 1024: "1k"}.get(width, f"{width}x{width // 2}")


def apply_text_style(recipe: Recipe, opts: BuildOptions) -> str | None:
    """Switch the recipe's text between panels painted into the frames and SOS labels for --hud, returning the style to tag outputs with, or None when it is the recipe's own. Tested by test_build.py::test_length_variant_build."""
    if opts.hud is None:
        return None
    if opts.hud not in ("panels", "labels"):
        raise RecipeError("--hud must be panels (text painted into the picture) or labels (SOS draws labels.txt)")
    if not recipe.is_movie or not recipe.hud["enabled"]:
        raise RecipeError("--hud chooses how a movie's text is shown; this recipe has no movie text")
    if opts.hud == recipe.hud["mode"]:
        return None
    recipe.hud["mode"] = opts.hud
    return opts.hud


def length_tag(recipe: Recipe, ctx: BuildContext, opts: BuildOptions) -> int | None:
    """Return the seconds a --seconds build tags its files with, or None when it is the recipe's own length. Tested by test_build.py::test_length_variant_build."""
    if not opts.seconds or ctx.timeline.frames == int(recipe.animation["frames"]):
        return None
    return int(opts.seconds)


def make_context(recipe: Recipe, opts: BuildOptions) -> BuildContext:
    """Resolve resolution, frame count and folders for a build from the recipe and overrides. Tested by test_build.py::test_texture_build_end_to_end."""
    width = PREVIEW_WIDTH if opts.preview else int(opts.width or recipe.dataset["width"])
    if width % 4 or width < 64:
        raise RecipeError("width must be a multiple of 4 and >= 64")
    a = recipe.animation
    fps = int(opts.fps or a["fps"])
    if opts.seconds is not None:
        if not recipe.is_movie:
            raise RecipeError("--seconds sets a movie's length; this recipe makes a still image")
        if opts.frames:
            raise RecipeError("give --frames or --seconds, not both")
        if int(opts.seconds) < 1:
            raise RecipeError("--seconds must be a whole number of seconds, 1 or more")
    frames = (int(opts.seconds) * fps if opts.seconds else int(opts.frames or a["frames"])) if recipe.is_movie else 1
    timeline = Timeline(frames, fps, bool(a["loop"]) if recipe.is_movie else False, a["start"], a["end"])
    return BuildContext(recipe, width, timeline, Path(opts.build_dir) / recipe.slug / f"{width}x{width // 2}",
                        max(1, opts.workers), opts.refresh)


def context_from_args(args: dict) -> BuildContext:
    """Rebuild a BuildContext inside a worker from BuildContext.to_args(), carrying over a --hud text style the main process applied. Tested by test_build.py::test_movie_build_end_to_end and test_length_variant_build."""
    recipe = load_recipe(args["recipe"])
    recipe.hud["mode"] = args.get("hud_mode", recipe.hud["mode"])
    a = recipe.animation
    timeline = Timeline(args["frames"], args["fps"], bool(a["loop"]) if recipe.is_movie else False, a["start"], a["end"])
    return BuildContext(recipe, args["width"], timeline, Path(args["build_dir"]), args["workers"], False)


def make_layers(ctx: BuildContext) -> list:
    """Instantiate every recipe layer with validated options. Tested indirectly by test_build.py."""
    return [LAYERS[spec["type"]](spec, ctx, i) for i, spec in enumerate(ctx.recipe.layers)]


def prepare_layers(ctx: BuildContext, layers: list) -> None:
    """Bind each layer to its cache, run prepare() only when the cache is missing or refresh is set, then attach. Tested by test_build.py::test_cache_reused_and_invalidated."""
    for layer in layers:
        layer.bind()
        if ctx.refresh or not layer.store.complete:
            t0 = time.monotonic()
            LOG.info("preparing layer %d (%s) at %dx%d", layer.index + 1, layer.TYPE, ctx.width, ctx.height)
            layer.prepare()
            layer.store.mark_complete()
            LOG.info("  layer %d ready in %.1fs", layer.index + 1, time.monotonic() - t0)
        else:
            LOG.info("reusing cached layer %d (%s): %s", layer.index + 1, layer.TYPE, layer.store.root.name)
        layer.attach()


def dataset_name(template: str, values: dict) -> str:
    """Return a dataset's name with any {value} it names filled from a frame's values, as HUD templates are (such as a forecast's date), or the name as written when it names none. Tested by test_build.py::test_name_takes_build_values."""
    if "{" not in template:
        return template
    try:
        return string.Formatter().vformat(template, (), {k: v for k, v in values.items() if k != "name"})
    except (KeyError, AttributeError, IndexError, ValueError) as exc:
        raise RecipeError(f"[dataset] name {template!r} can't be filled ({type(exc).__name__}: {exc}); available "
                          f"values are: {', '.join(sorted(k for k in values if k != 'name'))}") from None


class FrameRenderer:
    """Composites all layers and the HUD for one frame index. Tested indirectly by test_build.py."""

    def __init__(self, ctx: BuildContext, layers: list):
        """Keep the layers and build the HUD with fonts fitted to sampled frame values. Tested indirectly by test_build.py::test_texture_build_end_to_end."""
        self.ctx, self.layers = ctx, layers
        spec = ctx.recipe.hud
        self.hud = None
        if spec["enabled"] and spec["mode"] == "panels":
            if not polar_safe(float(spec["lat"]), float(spec["height_deg"])):
                raise RecipeError("[hud] lat +/- height_deg/2 must stay within 80 degrees of the equator")
            idx = sample_indices(ctx.timeline.frames, int(spec["fit_samples"]))
            self.hud = HudRenderer(ctx.width, ctx.height, spec, [self.values(ctx.timeline.at(i)) for i in idx])

    def values(self, t) -> dict:
        """Merge timeline, layer and recipe-hook values for HUD templates, passing a three-argument hook the values so far, then fill the dataset's name from them. Tested by test_recipe.py::test_hook_registers_layer_and_values, test_v3_recipes.py::test_hook_sees_layer_values and test_build.py::test_name_takes_build_values."""
        v = {"name": self.ctx.recipe.dataset["name"], **t.values()}
        for layer in self.layers:
            v.update(layer.values(t))
        hook = self.ctx.recipe.hook("values")
        if hook:
            import inspect
            wants_values = len(inspect.signature(hook).parameters) >= 3
            v.update((hook(t, self.ctx, dict(v)) if wants_values else hook(t, self.ctx)) or {})
        v["name"] = dataset_name(self.ctx.recipe.dataset["name"], v)   # last, so it can use the hook's values too
        return v

    def label(self, index: int) -> str:
        """Return one frame's HUD text as a single SOS label line: the title, then the subtitle after a middle dot. Tested by test_build.py::test_movie_build_end_to_end and test_labels_mode_skips_panels."""
        spec = self.ctx.recipe.hud
        v = self.values(self.ctx.timeline.at(index))
        parts = [render_template(spec["title"], v)] + ([render_template(spec["subtitle"], v)] if spec["subtitle"] else [])
        return "  \u00b7  ".join(" ".join(p.split()) for p in parts if p.strip())

    def frame(self, index: int) -> np.ndarray:
        """Render one uint8 sRGB frame: layers in linear light, encode, flat overlays, then the HUD. Tested indirectly by test_build.py."""
        t = self.ctx.timeline.at(index)
        canvas = np.zeros((self.ctx.height, self.ctx.width, 3), np.float32)
        for layer in self.layers:
            layer.render(canvas, t)
        out = np.empty(canvas.shape, np.uint8)
        for y0 in range(0, self.ctx.height, 256):
            out[y0:y0 + 256] = linear_to_srgb8(canvas[y0:y0 + 256])
        for layer in self.layers:
            layer.overlay(out, t)
        if self.hud:
            self.hud.composite(out, self.values(t))
        return out


_WORKER: dict = {}


def _init_worker(ctx_args: dict, store_paths: list[str], frames_dir: str | None) -> None:
    """Pool initializer that rebuilds the recipe, binds each layer to the main process's cache and builds a renderer. Tested indirectly by test_build.py::test_movie_build_end_to_end."""
    import cv2
    cv2.setNumThreads(1)
    ctx = context_from_args(ctx_args)
    layers = make_layers(ctx)
    for layer, path in zip(layers, store_paths):
        layer.bind(Path(path))
        layer.attach()
    _WORKER.update(renderer=FrameRenderer(ctx, layers), frames_dir=frames_dir)


def _render_task(index: int) -> bytes:
    """Pool task returning one frame as packed RGB24 bytes, optionally also saving it as PNG. Tested indirectly by test_build.py::test_movie_build_end_to_end."""
    img = _WORKER["renderer"].frame(index)
    if _WORKER["frames_dir"]:
        import cv2
        cv2.imwrite(str(Path(_WORKER["frames_dir"]) / f"frame_{index:05d}.png"), img[..., ::-1])
    return img.tobytes()


def encode_movie(ctx: BuildContext, layers: list, opts: BuildOptions, outputs: dict[str, Path]) -> dict:
    """Render every frame once in a spawned pool with a bounded in-flight window and pipe it to one ffmpeg per codec, returning timing and commands. Tested by test_build.py::test_movie_build_end_to_end with both codecs."""
    enc = ctx.recipe.encode
    ffmpeg = find_ffmpeg()
    cmds = {c: ffmpeg_command(ffmpeg, ctx.width, ctx.height, ctx.timeline.fps, path, c, int(opts.crf or enc["crf"]),
                              opts.preset or enc["preset"], enc["bitrate"]) for c, path in outputs.items()}
    if opts.frames_dir:
        Path(opts.frames_dir).mkdir(parents=True, exist_ok=True)
    n, nbytes = ctx.timeline.frames, ctx.width * ctx.height * 3
    LOG.info("encoding %d frames %dx%d @ %d fps with %d workers -> %s", n, ctx.width, ctx.height, ctx.timeline.fps,
             ctx.workers, ", ".join(f"{c}: {p.name}" for c, p in outputs.items()))
    t0 = time.monotonic()
    logs = {c: p.with_suffix(".ffmpeg.log") for c, p in outputs.items()}
    procs, rcs = {}, {}
    with contextlib.ExitStack() as stack:
        for c, path in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = stack.enter_context(logs[c].open("wb"))
            procs[c] = subprocess.Popen(cmds[c], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log_fh)
        try:
            initargs = (ctx.to_args(), [str(l.store.root) for l in layers],
                        str(opts.frames_dir) if opts.frames_dir else None)
            with mp.get_context("spawn").Pool(ctx.workers, _init_worker, initargs) as pool:
                pending: collections.deque = collections.deque()
                nxt, done, step = 0, 0, max(1, n // 20)
                while nxt < min(n, ctx.workers * 2):
                    pending.append(pool.apply_async(_render_task, (nxt,)))
                    nxt += 1
                while pending:
                    buf = pending.popleft().get()
                    if len(buf) != nbytes:
                        raise BuildError(f"frame {done} has {len(buf)} bytes, expected {nbytes}")
                    for proc in procs.values():
                        proc.stdin.write(buf)
                    done += 1
                    if nxt < n:
                        pending.append(pool.apply_async(_render_task, (nxt,)))
                        nxt += 1
                    if done % step == 0 or done == n:
                        el = time.monotonic() - t0
                        LOG.info("  frame %5d/%d  %5.1f fps  ETA %4.0fs", done, n, done / el, (n - done) * el / done)
            for proc in procs.values():
                proc.stdin.close()
        except BrokenPipeError as exc:
            raise BuildError(f"ffmpeg exited early; see {', '.join(str(p) for p in logs.values())}") from exc
        finally:
            for c, proc in procs.items():
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                rcs[c] = proc.wait()
    for c, rc in rcs.items():
        if rc != 0:
            tail = "\n".join(logs[c].read_text(errors="replace").splitlines()[-15:])
            raise BuildError(f"ffmpeg ({c}) failed ({rc}):\n{tail}")
    return {"encode_s": round(time.monotonic() - t0, 1), "ffmpeg_cmds": {c: cmd[1:] for c, cmd in cmds.items()},
            "ffmpeg": ffmpeg}


def collect_checks(ctx: BuildContext, layers: list, first: np.ndarray) -> list[Check]:
    """Gather generic output checks, every layer's checks and the recipe hook's checks. Tested by test_build.py::test_texture_build_end_to_end."""
    ratio = seam_ratio(luminance(first.astype(np.float32)))
    checks = [Check("output is 2:1 equirectangular", first.shape[1] == 2 * first.shape[0], f"{first.shape[1]}x{first.shape[0]}"),
              Check("frame 0 seamless at +/-180", ratio < 2.5, f"edge/interior column difference {ratio:.2f}")]
    for layer in layers:
        checks += layer.checks()
    hook = ctx.recipe.hook("checks")
    if hook:
        checks += list(hook(ctx, layers) or [])
    return checks


def gather_credits(recipe: Recipe, layers: list) -> tuple[list[str], list[str]]:
    """Return de-duplicated credit lines and licence notes from the recipe and its layers. Tested by test_build.py::test_texture_build_end_to_end."""
    credits = list(dict.fromkeys(list(recipe.dataset["credits"]) + [c for l in layers for c in l.credits()]))
    ids = {getattr(l, "meta", {}).get("license") for l in layers if hasattr(l, "meta")}
    notes = [recipe.dataset["license"]] if recipe.dataset["license"] else []
    notes += [LICENSE_NOTES[i] for i in sorted(i for i in ids if i in LICENSE_NOTES)]
    return credits, notes


def build(ref, opts: BuildOptions | None = None) -> dict:
    """Build a recipe into dist/<slug>[_preview]/, plus dist/<slug>_<codec>[_preview]/ for each extra codec, and return a summary. Tested by test_build.py::test_texture_build_end_to_end and test_movie_build_end_to_end."""
    opts = opts or BuildOptions()
    t_start = time.monotonic()
    recipe = load_recipe(ref)
    for w in recipe.warnings:
        LOG.warning(w)
    style = apply_text_style(recipe, opts)
    ctx = make_context(recipe, opts)
    layers = make_layers(ctx)
    prepare_layers(ctx, layers)
    renderer = FrameRenderer(ctx, layers)
    # A name may take build values, such as a forecast's date: the playlist and README get it filled from frame one.
    recipe.dataset["name"] = renderer.values(ctx.timeline.at(0))["name"]
    first = renderer.frame(0)
    checks = collect_checks(ctx, layers, first)
    log_checks(checks, LOG)
    if opts.strict and not all(c.passed for c in checks):
        raise BuildError("verification failed (use --no-strict to build anyway)")
    length = length_tag(recipe, ctx, opts)
    tag = (f"_{length}s" if length else "") + (f"_{style}" if style else "")
    stem = f"{recipe.dataset['stem']}{tag}_{size_label(ctx.width)}"
    summary = {"recipe": str(recipe.path), "width": ctx.width, "height": ctx.height,
               "settings": {"width": ctx.width, "frames": ctx.timeline.frames, "fps": ctx.timeline.fps,
                            "layers": recipe.layers, "hud": recipe.hud, "encode": recipe.encode}}
    codecs = list(opts.codecs or recipe.encode["codecs"])
    outputs: dict[str, Path] = {}
    if recipe.is_movie:
        summary["settings"]["encode"] = {**recipe.encode, "codecs": codecs}
        outputs = {c: ctx.build_dir / (f"{stem}.mp4" if i == 0 else f"{stem}_{c}.mp4") for i, c in enumerate(codecs)}
        summary.update(encode_movie(ctx, layers, opts, outputs))
        videos, vchecks = {}, []
        for c, path in outputs.items():
            videos[c] = probe_video(path, summary["ffmpeg"])
            vchecks += validate_video(videos[c], ctx.width, ctx.height, ctx.timeline.frames, ctx.timeline.fps, c)
        log_checks(vchecks, LOG)
        checks += vchecks
        summary.update(videos=videos, video=videos[codecs[0]])
        if recipe.hud["enabled"]:
            labels = ctx.build_dir / "labels.txt"
            labels.write_text("\n".join(renderer.label(i) for i in range(ctx.timeline.frames)) + "\n")
        if opts.strict and not all(c.passed for c in vchecks):
            raise BuildError("video validation failed")
        out = outputs[codecs[0]]
    else:
        import cv2
        fmt = recipe.dataset["image_format"]
        out = ctx.build_dir / f"{stem}.{fmt}"
        out.parent.mkdir(parents=True, exist_ok=True)
        params = [cv2.IMWRITE_JPEG_QUALITY, 95] if fmt == "jpg" else [cv2.IMWRITE_PNG_COMPRESSION, 6]
        cv2.imwrite(str(out), first[..., ::-1], params)
        summary["image"] = {"path": str(out), "bytes": out.stat().st_size, "width": ctx.width, "height": ctx.height}
    summary["checks"] = [asdict(c) for c in checks]
    credits, licenses = gather_credits(recipe, layers)
    suffix = "_preview" if opts.preview else ""
    dist = Path(opts.out_dir) / (recipe.slug + tag + suffix)
    labels = ctx.build_dir / "labels.txt" if recipe.is_movie and recipe.hud["enabled"] else None
    files = build_package(recipe, out, dist, summary, credits, licenses, labels=labels, length=length, style=style)
    variants = {}
    for c in list(outputs)[1:]:
        vdir = Path(opts.out_dir) / f"{recipe.slug}{tag}_{c}{suffix}"
        vfiles = build_package(recipe, outputs[c], vdir, {**summary, "video": summary["videos"][c]}, credits,
                               licenses, variant=c, primary=(codecs[0], dist.name), labels=labels, length=length,
                               style=style)
        variants[c] = {k: str(v) for k, v in vfiles.items()}
    summary.update(files={k: str(v) for k, v in files.items()}, variants=variants, credits=credits,
                   licenses=licenses, total_s=round(time.monotonic() - t_start, 1))
    LOG.info("built %s in %.1fs -> %s", recipe.slug, summary["total_s"],
             ", ".join([str(dist)] + [str(Path(opts.out_dir) / f"{recipe.slug}{tag}_{c}{suffix}") for c in variants]))
    return summary


def still(ref, opts: BuildOptions, index: int, out_png: Path | None = None) -> Path:
    """Render one frame of a recipe to PNG for quick visual checks without encoding. Tested by test_build.py::test_movie_build_end_to_end."""
    import cv2
    recipe = load_recipe(ref)
    ctx = make_context(recipe, opts)
    layers = make_layers(ctx)
    prepare_layers(ctx, layers)
    if not 0 <= index < ctx.timeline.frames:
        raise RecipeError(f"frame must be between 0 and {ctx.timeline.frames - 1}")
    img = FrameRenderer(ctx, layers).frame(index)
    out_png = Path(out_png or ctx.build_dir / f"still_{index:05d}.png")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), img[..., ::-1])
    LOG.info("still frame %d -> %s", index, out_png)
    return out_png


def check(ref, opts: BuildOptions) -> list[Check]:
    """Prepare all layers (fetching data) and run every check without rendering a dataset. Tested by test_cli.py::test_new_list_check_and_resolve."""
    recipe = load_recipe(ref)
    ctx = make_context(recipe, opts)
    layers = make_layers(ctx)
    prepare_layers(ctx, layers)
    first = FrameRenderer(ctx, layers).frame(0)
    checks = collect_checks(ctx, layers, first)
    log_checks(checks, LOG)
    return checks


__all__ = ["BuildOptions", "BuildError", "build", "still", "check"]
