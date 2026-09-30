#!/bin/sh
# Smoke-tests a built Toolbot image.
#
#   scripts/smoke-test-image.sh toolbot:dev
#
# Used locally, by GitHub Actions and by the GitLab image job.
set -eu
# Stop Git Bash on Windows rewriting container paths such as /work.
export MSYS_NO_PATHCONV=1

image=${1:?usage: $0 <image>}
run() { docker run --rm "$@"; }
step() { printf '\n==> %s\n' "$*"; }

step "toolbot CLI runs"
run "$image" toolbot --version

step "git is available"
run "$image" git --version

step "runs as an unprivileged user"
uid=$(run "$image" id -u)
[ "$uid" != "0" ] || { echo "image runs as root" >&2; exit 1; }
echo "uid=$uid"

step "validates tool definitions from a mounted repository"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/tools/example"
cat > "$work/tools/example/tool.yaml" <<'EOF'
name: example
version: "1.0.0"
tag: v{version}
source: {provider: github, repository: acme/example}
assets:
  linux: {name: example-linux.tar.gz, destination: example.tar.gz}
EOF
chmod -R a+rX "$work"
run -v "$work:/work:ro" -w /work "$image" toolbot validate
run -v "$work:/work:ro" -w /work "$image" toolbot list

printf '\nsmoke test passed: %s\n' "$image"
