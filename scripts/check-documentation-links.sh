#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_directory}/.." && pwd)"
cd "${repository_root}"

mapfile -t markdown_files < <(
    find README.md CONTRIBUTING.md docs wiki panel platform orchestration \
        -type d \( -name node_modules -o -name vendor -o -name .test-dist \) \
        -prune -o -type f -name '*.md' -print | sort
)

failures=0
while IFS=$'\t' read -r source_file target; do
    target="${target#<}"
    target="${target%>}"
    target="${target%%#*}"
    target="${target%%\?*}"

    case "${target}" in
        ''|'#'*|http://*|https://*|mailto:*|tel:*)
            continue
            ;;
    esac

    resolved="$(dirname -- "${source_file}")/${target}"
    if [[ ! -e "${resolved}" ]]; then
        echo "Broken documentation link: ${source_file} -> ${target}" >&2
        failures=$((failures + 1))
    fi
done < <(
    perl -ne '
        while (/\]\(([^)]+)\)/g) {
            print "$ARGV\t$1\n";
        }
    ' "${markdown_files[@]}"
)

if (( failures > 0 )); then
    echo "Documentation link check failed: ${failures} broken link(s)." >&2
    exit 1
fi

echo "Documentation links: ok (${#markdown_files[@]} Markdown files)."
