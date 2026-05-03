#!/usr/bin/env bash
# Build all `main` Go packages in this module into ./bin/ (Unix sibling of
# build-all.ps1 -- same behavior, no PowerShell dependency).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

bin_dir="$script_dir/bin"
mkdir -p "$bin_dir"

echo "Compiling all packages..."
go test ./...

echo "Enumerating packages..."
while IFS= read -r pkg; do
  [ -z "$pkg" ] && continue
  pkg_type="$(go list -f '{{.Name}}' "$pkg")"
  [ "$pkg_type" != "main" ] && continue

  pkg_name="$(basename "$pkg")"
  out="$bin_dir/$pkg_name"
  echo "Building $pkg -> $out"
  go build -o "$out" "$pkg"

  # Mirror the .ps1's behaviour: a package literally named `main` also gets
  # copied to ./main.exe at the backend root.
  if [ "$pkg_name" = "main" ]; then
    rm -f "$script_dir/main.exe"
    cp "$out" "$script_dir/main.exe"
    echo "Copied $out -> $script_dir/main.exe"
  fi
done < <(go list ./...)

echo "Build complete. Binaries are in: $bin_dir"
