"""Tests for the `sos` command line."""
from __future__ import annotations

import pytest

from conftest import REPO
from sosforge import cli
from sosforge.recipe import RecipeError, find_recipe, load_recipe


def test_new_list_check_and_resolve(tmp_path, monkeypatch, capsys):
    """Checks `sos new` scaffolds a loadable recipe, `sos list` shows bundled and local recipes, `sos check` passes offline, and names resolve. Covers cmd_new, cmd_list, cmd_check, parser, main, _opts, find_recipe and default_search_paths; build/still wrappers are exercised manually."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["new", "Coffee Shops"]) == 0
    r = load_recipe("coffee_shops")
    assert r.dataset["name"] == "Coffee Shops" and r.layers[1]["source"]["path"] == "data/places.csv"
    r.layers[0] = {"type": "basemap", "color": "#000000"}
    toml = (tmp_path / "recipes" / "coffee_shops" / "recipe.toml")
    toml.write_text(toml.read_text().replace('name = "black_marble"', 'color = "#101010"'))
    assert cli.main(["new", "Coffee Shops"]) == 2
    capsys.readouterr()
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "coffee_shops" in out and "lunar_phases" in out and "where_the_world_plays" in out
    assert cli.main(["check", "coffee_shops", "--width", "256", "--workers", "1"]) == 0
    assert find_recipe("lunar_phases") == REPO / "recipes" / "lunar_phases" / "recipe.toml"
    with pytest.raises(RecipeError, match="not found"):
        find_recipe("nope")
    assert cli.main(["check", "nope"]) == 2


def test_env_local_loaded_without_override(tmp_path, monkeypatch):
    """Checks .env.local values load, quotes and export prefixes are handled, blanks are skipped, and real environment variables win. Covers load_env_file and its call in main; this test is not itself tested."""
    from sosforge.envfile import load_env_file
    f = tmp_path / ".env.local"
    f.write_text('# comment\nexport A_TOKEN="abc"\nB_TOKEN=\nC_TOKEN=keep-me\nD_URL=\'http://x\'\nnot a pair\n')
    env = {"C_TOKEN": "from-shell"}
    assert load_env_file(f, env) == ["A_TOKEN", "D_URL"]
    assert env == {"A_TOKEN": "abc", "C_TOKEN": "from-shell", "D_URL": "http://x"}
    assert load_env_file(tmp_path / "missing", env) == []
    import os
    monkeypatch.chdir(tmp_path)
    for k in ("A_TOKEN", "D_URL"):
        os.environ.pop(k, None)
    try:
        assert cli.main(["list"]) == 0
        assert os.environ.get("A_TOKEN") == "abc"
    finally:
        for k in ("A_TOKEN", "D_URL"):
            os.environ.pop(k, None)


def test_codec_flag_parses_to_list():
    """Checks repeated --codec flags become an ordered codec list and no flag leaves the recipe default. Covers parser and _opts for codecs; this test is not itself tested."""
    args = cli.parser().parse_args(["build", "x", "--codec", "h264", "--codec", "mpeg4"])
    assert cli._opts(args).codecs == ["h264", "mpeg4"]
    assert cli._opts(cli.parser().parse_args(["build", "x"])).codecs is None


def test_site_and_bundle_commands(tmp_path, monkeypatch, capsys):
    """Checks sos site passes the Ask & Answer base from the flag or $SOSFORGE_ASK_BASE (leaving site.toml's to build_site), an empty one for --no-ask, and checks downloads unless told not to, and a bundle runs from the CLI. Covers cmd_site's wiring and cmd_bundle; build_site itself is tested in test_site.py and serving is exercised manually."""
    from test_site import make_dataset
    from sosforge import site
    seen = []
    monkeypatch.setattr(site, "build_site", lambda cfg, out, **kw: (seen.append(kw), {"datasets": []})[1])
    monkeypatch.setenv("SOSFORGE_ASK_BASE", "https://env.example")
    assert cli.main(["site", "--out", str(tmp_path / "s")]) == 0
    assert cli.main(["site", "--out", str(tmp_path / "s"), "--ask-base", "https://flag.example", "--no-check-downloads"]) == 0
    assert cli.main(["site", "--out", str(tmp_path / "s"), "--no-ask"]) == 0
    assert [(k["ask_base"], k["check_downloads"]) for k in seen] == [
        ("https://env.example", True), ("https://flag.example", False), ("", True)]
    make_dataset(tmp_path / "dist" / "tiny")
    assert cli.main(["bundle", "tiny", "--dist", str(tmp_path / "dist")]) == 0
    assert capsys.readouterr().out.strip().endswith("tiny_256x128.zip")
    assert cli.main(["bundle", "nothing", "--dist", str(tmp_path / "dist")]) == 2
