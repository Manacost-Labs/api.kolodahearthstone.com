#!/usr/bin/env bash
set -euo pipefail

wiki_repository="${KHS_GITHUB_REPOSITORY:-Manacost-Labs/api.kolodahearthstone.com}"
script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_directory}/.." && pwd)"
wiki_source="${repository_root}/wiki"

if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI (gh) is required." >&2
    exit 1
fi

if [[ ! -f "${wiki_source}/Home.md" || ! -f "${wiki_source}/_Sidebar.md" ]]; then
    echo "Wiki source is incomplete: expected Home.md and _Sidebar.md." >&2
    exit 1
fi

if [[ "$(gh api "repos/${wiki_repository}" --jq '.has_wiki')" != "true" ]]; then
    echo "GitHub Wiki is disabled or unavailable for ${wiki_repository}." >&2
    echo "Keep using wiki/Home.md, or enable Wikis in repository settings first." >&2
    exit 2
fi

wiki_worktree="$(mktemp -d -t khs-wiki.XXXXXX)"
cleanup() {
    rm -rf -- "${wiki_worktree}"
}
trap cleanup EXIT

if ! git clone --quiet "https://github.com/${wiki_repository}.wiki.git" "${wiki_worktree}/repo"; then
    echo "The Wiki Git repository does not exist yet." >&2
    echo "Create the initial Home page in the GitHub web interface, then retry." >&2
    exit 3
fi

find "${wiki_worktree}/repo" -maxdepth 1 -type f -name '*.md' -delete
cp -- "${wiki_source}"/*.md "${wiki_worktree}/repo/"

# The reviewed source uses repository-relative links so it remains navigable in
# the main repository. The published Wiki is a separate Git repository, so
# convert only Markdown link targets to their public Wiki/repository URLs.
sed -i -E \
    -e "s#\]\(([A-Za-z0-9_-]+)\.md\)#](https://github.com/${wiki_repository}/wiki/\\1)#g" \
    -e "s#\]\(\.\./docs/([^)]+)\)#](https://github.com/${wiki_repository}/blob/main/docs/\\1)#g" \
    -e "s#\]\(\.\./platform/([^)]+)\)#](https://github.com/${wiki_repository}/blob/main/platform/\\1)#g" \
    -e "s#\]\(\.\./DEPLOY\.md\)#](https://github.com/${wiki_repository}/blob/main/DEPLOY.md)#g" \
    "${wiki_worktree}/repo"/*.md

git -C "${wiki_worktree}/repo" add -A -- '*.md'
if git -C "${wiki_worktree}/repo" diff --cached --quiet; then
    echo "GitHub Wiki is already up to date."
    exit 0
fi

git -C "${wiki_worktree}/repo" diff --cached --stat
git -C "${wiki_worktree}/repo" commit -m "docs: update project wiki"
git -C "${wiki_worktree}/repo" push origin HEAD

echo "GitHub Wiki published: https://github.com/${wiki_repository}/wiki"
