#!/usr/bin/env bash
# Publish Daedalus/ as its own git repository.
# Does not modify ArgosDataNexus. Requires an EMPTY GitHub/GitLab remote
# that your account can push to.
#
# Usage:
#   ./scripts/publish-standalone-repo.sh git@gitlab.com:Blaue-Ente/Daedalus.git
#   ./scripts/publish-standalone-repo.sh https://gitlab.com/Blaue-Ente/deadalus-agent-0.git --force
#   ./scripts/publish-standalone-repo.sh https://github.com/YOU/Daedalus.git
#
# --force  replace a GitLab/GitHub "getting started" README (git push -uf).
# Optional: GITLAB_TOKEN (personal api token with write_repository) can
# create a missing GitLab project before push:
#   GITLAB_TOKEN=glpat-... ./scripts/publish-standalone-repo.sh \
#     https://gitlab.com/Blaue-Ente/Daedalus.git
# Do not silently reuse the ADN secret named GitLabTocken — that token is a
# Data-Asset-Hub project bot and 401s on other projects, which would override
# a working SSH/HTTPS remote. Set GITLAB_TOKEN explicitly.
# Optional: GITHUB_TOKEN (or GH_TOKEN) can create a missing GitHub repo:
#   GITHUB_TOKEN=ghp-... ./scripts/publish-standalone-repo.sh \
#     https://github.com/Blaue-Ente/Daedalus.git
set -euo pipefail

# GITLAB_TOKEN must be set explicitly. The ADN secret GitLabTocken is a
# project bot for Data-Asset-Hub and cannot push this product remote.
GITLAB_TOKEN="${GITLAB_TOKEN:-}"
GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"

FORCE=0
REMOTE=""
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) REMOTE="$arg" ;;
  esac
done
if [[ -z "$REMOTE" ]]; then
  echo "usage: $0 <git-remote-url> [--force]" >&2
  exit 2
fi

maybe_create_gitlab_project() {
  local token="${GITLAB_TOKEN:-}"
  [[ -n "$token" ]] || return 0
  local path=""
  if [[ "$REMOTE" =~ gitlab.com[:/]([^/]+)/([^/.]+) ]]; then
    path="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
  else
    return 0
  fi
  local ns="${path%%/*}"
  local name="${path##*/}"
  echo "GITLAB_TOKEN set — ensuring GitLab project $path exists"
  local ns_json
  ns_json="$(curl -fsS --header "PRIVATE-TOKEN: ${token}" \
    "https://gitlab.com/api/v4/namespaces?search=${ns}" || true)"
  local ns_id
  ns_id="$(python3 -c "
import json,sys
ns=json.loads(sys.argv[1] or '[]')
want=sys.argv[2]
for n in ns:
    if n.get('full_path')==want or n.get('path')==want:
        print(n['id']); break
" "$ns_json" "$ns" || true)"
  if [[ -z "$ns_id" ]]; then
    echo "Could not resolve GitLab namespace '$ns' with this token." >&2
    echo "Create https://gitlab.com/${path} empty (no README), then re-run." >&2
    return 0
  fi
  local code
  code="$(curl -sS -o /tmp/daedalus-gl-create.json -w '%{http_code}' \
    --header "PRIVATE-TOKEN: ${token}" \
    --header "Content-Type: application/json" \
    --data "{\"name\":\"${name}\",\"path\":\"${name}\",\"namespace_id\":${ns_id},\"visibility\":\"private\",\"initialize_with_readme\":false}" \
    "https://gitlab.com/api/v4/projects")"
  if [[ "$code" == "201" ]]; then
    echo "Created GitLab project $path"
  elif [[ "$code" == "400" ]] && grep -q "already been taken" /tmp/daedalus-gl-create.json 2>/dev/null; then
    echo "GitLab project $path already exists"
  else
    echo "GitLab create returned HTTP $code: $(cat /tmp/daedalus-gl-create.json 2>/dev/null || true)" >&2
    echo "If the project is missing, create it empty in the GitLab UI and re-run." >&2
  fi
}

maybe_create_github_repo() {
  local token="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
  [[ -n "$token" ]] || return 0
  local owner="" name=""
  if [[ "$REMOTE" =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
    owner="${BASH_REMATCH[1]}"
    name="${BASH_REMATCH[2]}"
  else
    return 0
  fi
  echo "GITHUB_TOKEN set — ensuring GitHub repo $owner/$name exists"
  local login=""
  login="$(curl -fsS -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: daedalus-publish" \
    https://api.github.com/user \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('login',''))" || true)"
  local endpoint="https://api.github.com/user/repos"
  if [[ -n "$login" && "${login,,}" != "${owner,,}" ]]; then
    endpoint="https://api.github.com/orgs/${owner}/repos"
  fi
  local code
  code="$(curl -sS -o /tmp/daedalus-gh-create.json -w '%{http_code}' \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: daedalus-publish" \
    -H "Content-Type: application/json" \
    --data "{\"name\":\"${name}\",\"private\":true,\"auto_init\":false,\"description\":\"Standalone Daedalus coding agent\"}" \
    "$endpoint")"
  if [[ "$code" == "201" ]]; then
    echo "Created GitHub repo $owner/$name"
  else
    echo "GitHub create returned HTTP $code: $(cat /tmp/daedalus-gh-create.json 2>/dev/null || true)" >&2
    echo "If the repo is missing, create https://github.com/${owner}/${name} empty in the GitHub UI and re-run." >&2
  fi
}

authenticated_remote() {
  if [[ -n "${GITLAB_TOKEN:-}" && "$REMOTE" =~ gitlab.com[:/]([^/]+)/([^/.]+) ]]; then
    printf 'https://oauth2:%s@gitlab.com/%s/%s.git' "$GITLAB_TOKEN" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  local gh="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
  if [[ -n "$gh" && "$REMOTE" =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
    printf 'https://x-access-token:%s@github.com/%s/%s.git' "$gh" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  printf '%s' "$REMOTE"
}

maybe_create_gitlab_project
maybe_create_github_repo

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/daedalus-export.XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

echo "Exporting $ROOT → $STAGING"
# rsync is not always installed (cloud VMs). tar copies the product tree.
tar -C "$ROOT" -cf - \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.venv' \
  --exclude 'data' \
  --exclude 'playground' \
  --exclude '.env' \
  --exclude '*.sqlite' \
  . | tar -C "$STAGING" -xf -

cd "$STAGING"
test -f Dockerfile
test -d daedalus
test -d app
test -d ui
test -f pyproject.toml

# Do not inherit the ADN checkout's gitlab.com insteadOf rewrite — that
# injects the Data-Asset-Hub project bot and 403s on other projects.
git_clean() {
  env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git "$@"
}

git_clean init -b main
git_clean add -A
git_clean -c user.email="daedalus@local" -c user.name="Daedalus export" \
  commit -m "feat: standalone Daedalus product (exported from ADN monorepo)"
PUSH_URL="$(authenticated_remote)"
git_clean remote add origin "$PUSH_URL"
echo "Pushing to $REMOTE (main${FORCE:+, force})"
if [[ "$FORCE" == "1" ]]; then
  git_clean push -uf origin main
else
  git_clean push -u origin main
fi

VERIFY="$(mktemp -d "${TMPDIR:-/tmp}/daedalus-verify.XXXXXX")"
trap 'rm -rf "$STAGING" "$VERIFY"' EXIT
echo "Verifying remote tree via fresh clone"
git_clean clone --depth 1 "$PUSH_URL" "$VERIFY"
test -f "$VERIFY/Dockerfile"
test -d "$VERIFY/daedalus"
test -d "$VERIFY/app"
test -d "$VERIFY/ui"
test -f "$VERIFY/pyproject.toml"
test ! -d "$VERIFY/argos_data_nexus"
echo "Remote verified: Dockerfile daedalus/ app/ ui/ (no ADN tree). ADN was not modified."
