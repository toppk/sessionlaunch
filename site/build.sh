#!/usr/bin/env bash
# Build the self-contained Horizon site into this repository's _site directory.
set -euo pipefail

site_dir=$(cd "$(dirname "$0")" && pwd)
repo_root=$(dirname "$site_dir")
out="$repo_root/_site"
theme="$site_dir/theme"
canonical=https://toppk.github.io/sessionlaunch
edit=https://github.com/toppk/sessionlaunch/edit/master

# page|title|navigation section|source, in reading order.
pages=(
  'index|Overview|Start|docs/index.md'
  'getting-started|Getting started|Start|docs/getting-started.md'
  'model|Session model|Understand|docs/session-model.md'
)

field() { cut -d'|' -f"$2" <<<"$1"; }

nav_for() {
  local current=$1 group='' html='' page name title next_group
  for page in "${pages[@]}"; do
    name=$(field "$page" 1)
    title=$(field "$page" 2)
    next_group=$(field "$page" 3)
    if [[ "$next_group" != "$group" ]]; then
      [[ -n "$group" ]] && html+='</ul></div>'
      html+="<div class=\"nav-group\"><span class=\"nav-label\">$next_group</span><ul>"
      group=$next_group
    fi
    if [[ "$name" == "$current" ]]; then
      html+="<li><a href=\"$name.html\" aria-current=\"page\">$title</a></li>"
    else
      html+="<li><a href=\"$name.html\">$title</a></li>"
    fi
  done
  printf '%s\n' "$html</ul></div>"
}

command -v pandoc >/dev/null || { echo 'Install pandoc to build the site.' >&2; exit 1; }
rm -rf -- "$out"
mkdir -p "$out/theme" "$out/assets"
cp "$theme/horizon.css" "$theme/horizon.js" "$out/theme/"
cp -r "$theme/fonts" "$out/theme/"
cp "$site_dir/assets/"* "$out/assets/"
cp "$site_dir/llms.txt" "$out/llms.txt"
touch "$out/.nojekyll"
: > "$out/llms-full.txt"

for index in "${!pages[@]}"; do
  page=${pages[$index]}
  name=$(field "$page" 1)
  title=$(field "$page" 2)
  source_relative=$(field "$page" 4)
  source="$repo_root/$source_relative"
  pager=''
  if ((index > 0)); then
    previous=${pages[$((index - 1))]}
    pager+="<a class=\"prev\" href=\"$(field "$previous" 1).html\"><span>Previous</span>$(field "$previous" 2)</a>"
  fi
  if ((index + 1 < ${#pages[@]})); then
    next=${pages[$((index + 1))]}
    pager+="<a class=\"next\" href=\"$(field "$next" 1).html\"><span>Next</span>$(field "$next" 2)</a>"
  fi
  arguments=()
  [[ "$name" != index ]] && arguments+=(--toc --toc-depth=3)
  # The template supplies the page heading. Remove the source's first H1 only.
  awk '!removed && /^# / { removed=1; next } { print }' "$source" | pandoc \
    --from markdown+yaml_metadata_block+fenced_divs+bracketed_spans-tex_math_dollars \
    --to html5 \
    --template "$theme/template.html" \
    --lua-filter "$theme/horizon.lua" \
    --metadata-file "$site_dir/site.yaml" \
    --metadata title="$title" \
    --metadata source="$name.md" \
    --metadata canonical="$canonical/$([[ "$name" == index ]] || printf '%s.html' "$name")" \
    --metadata edit-url="$edit/$source_relative" \
    --variable nav="$(nav_for "$name")" \
    --variable pager="$pager" \
    "${arguments[@]}" \
    --output "$out/$name.html"
  cp "$source" "$out/$name.md"
  { printf '\n\n<!-- %s.md -->\n\n' "$name"; cat "$source"; } >> "$out/llms-full.txt"
done

echo "Built ${#pages[@]} pages in $out"
