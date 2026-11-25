#!/usr/bin/env bash
# Release the commit at HEAD (design/v0/v0.1.0.md). The same script runs by hand and in CI, so both
# release the same thing. It refuses a dirty tree, a HEAD that is not exactly at a tag, a tag without
# its scope doc, and a tag that disagrees with pyproject's version; then it tests, builds the sdist and
# wheel, and publishes a GitHub Release whose notes are the scope doc.
#
# Needs: python3 with the dev extras and `build` installed, gh signed in (GH_TOKEN in CI), node 24, AWS access to
# sosforge-deploy-role, and Ask & Answer's API (site/site.toml's ask_base), from which the build fetches the release
# ask.lock.json pins, checked file by file (design/v0/v0.4.2.md). $ASK_UPLOAD_KEY is sos's upload key: the help docs
# reach Ask & Answer only through this release's upload (design/v0/v0.4.10.md).
# Datasets (MP4/JPG) are not built here: bundle them and attach them to a data-YYYY-MM release before pushing the
# version tag whose site/site.toml lists them (design/v0/v0.3.0.md).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

fail() { printf '\nBLOCKED: release\n\n  %s\n\n' "$1" >&2; exit 1; }

[ -z "$(git status --porcelain)" ] || fail "the working tree is dirty; release only a committed, tagged tree"
tag=$(git describe --exact-match --tags HEAD 2>/dev/null) || fail "HEAD is not exactly at a tag (git tag -a vX.Y.Z -m 'vX.Y.Z: ...')"
python3 tools/scope.py --release "$tag"
version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
[ "$tag" = "v$version" ] || fail "$tag does not match the version in pyproject.toml ($version)"
# Checked before anything is published, so a release never deploys a site whose Help answers from older docs.
[ ! -d infra ] || [ -n "${ASK_UPLOAD_KEY:-}" ] || fail "ASK_UPLOAD_KEY is not set: the help docs reach Ask & Answer only through the release's upload"

python3 -m pytest -q
rm -rf dist-pkg
python3 -m build --outdir dist-pkg

major=${tag#v}; major=${major%%.*}
if gh release view "$tag" >/dev/null 2>&1; then
  echo "release $tag already exists; keeping it (a rerun after a failed site deploy)"
else
  gh release create "$tag" dist-pkg/* --verify-tag --title "$tag" --notes-file "design/v$major/$tag.md"
fi

# The site, sos.stevenpenza.com (design/v0/v0.2.0.md): built from the datasets attached to releases and the pinned
# Ask & Answer release, then deployed by CDK through sosforge-deploy-role (OIDC in CI, a profile by hand). The stack's
# CSP allows the same API the build used: site.toml's ask_base unless $SOSFORGE_ASK_BASE overrides it.
if [ -d infra ]; then
  export SOSFORGE_ASK_BASE="${SOSFORGE_ASK_BASE:-$(python3 -c 'import tomllib; print(tomllib.load(open("site/site.toml", "rb"))["site"]["ask_base"])')}"
  python3 -m sosforge site --out build/site
  [ -n "${CI:-}" ] || export AWS_PROFILE="${AWS_PROFILE:-sosforge-deploy-role}"
  (cd infra && npm ci && SOSFORGE_SITE_DIR="$PWD/../build/site" npx cdk deploy SosForgeSite --require-approval never)
  # The docs Help answers from, as this tag committed them, with the corpus.py ask.lock.json pins.
  python3 -m sosforge upload-help
  # The architecture chart for stevenpenza.com's owner page (its architecture standard; design/v0/v0.8.3.md): what CDK
  # just synthesized plus infra/architecture.hand.json, by the shared generator copied byte for byte. It goes beside
  # status/sos.json in the private docs bucket, named by $SOSFORGE_STATUS_BUCKET, and is never committed. By hand
  # without that bucket it is drawn and checked, not uploaded.
  node tools/architecture.mjs --project sos --hand infra/architecture.hand.json --commit "$(git rev-parse HEAD)" \
    infra/cdk.out > build/architecture.json
  if [ -n "${SOSFORGE_STATUS_BUCKET:-}" ]; then
    aws s3 cp build/architecture.json "s3://$SOSFORGE_STATUS_BUCKET/architecture/sos.json" \
      --content-type application/json --only-show-errors
    echo "architecture chart uploaded: architecture/sos.json"
  else
    echo "SOSFORGE_STATUS_BUCKET is not set: the architecture chart was drawn, not uploaded"
  fi
fi
echo "released $tag"
