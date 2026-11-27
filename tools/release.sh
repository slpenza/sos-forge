#!/usr/bin/env bash
# Release the commit at HEAD (design/v0/v0.1.0.md). The same script runs by hand and in CI, so both
# release the same thing. It refuses a dirty tree, a HEAD that is not exactly at a tag, a tag without
# its scope doc, and a tag that disagrees with pyproject's version; then it tests, builds the sdist and
# wheel, and publishes a GitHub Release whose notes are the scope doc.
#
# Needs: python3 with the dev extras and `build` installed, and gh signed in (GH_TOKEN in CI).
# It deploys nothing. sos.stevenpenza.com is built from a pinned sos-forge tag and deployed by its own private repo,
# sos-site, which also uploads Help's docs to Ask & Answer (design/v0/v0.9.0.md).
# Datasets (MP4/JPG) are not built here: bundle them and attach them to a data-YYYY-MM release on this repo, where
# the site's Download links point (design/v0/v0.3.0.md).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

fail() { printf '\nBLOCKED: release\n\n  %s\n\n' "$1" >&2; exit 1; }

[ -z "$(git status --porcelain)" ] || fail "the working tree is dirty; release only a committed, tagged tree"
tag=$(git describe --exact-match --tags HEAD 2>/dev/null) || fail "HEAD is not exactly at a tag (git tag -a vX.Y.Z -m 'vX.Y.Z: ...')"
python3 tools/scope.py --release "$tag"
version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
[ "$tag" = "v$version" ] || fail "$tag does not match the version in pyproject.toml ($version)"

python3 -m pytest -q
rm -rf dist-pkg
python3 -m build --outdir dist-pkg

major=${tag#v}; major=${major%%.*}
if gh release view "$tag" >/dev/null 2>&1; then
  echo "release $tag already exists; keeping it (a rerun)"
else
  gh release create "$tag" dist-pkg/* --verify-tag --title "$tag" --notes-file "design/v$major/$tag.md"
fi

echo "released $tag"
