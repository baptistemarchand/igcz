#!/bin/sh
# Install the igcz command line tool from the latest GitHub release.
#
#   curl -fsSL https://raw.githubusercontent.com/baptistemarchand/igcz/main/install.sh | sh
#
# IGCZ_VERSION=v0.1.0 pins a version; IGCZ_INSTALL_DIR changes the target (default ~/.local/bin).
set -eu

repo="baptistemarchand/igcz"
dir="${IGCZ_INSTALL_DIR:-$HOME/.local/bin}"

fail() { echo "igcz install: $*" >&2; exit 1; }

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) fail "unsupported OS $(uname -s), download a build from https://github.com/$repo/releases" ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch=x86_64 ;;
  arm64 | aarch64) arch=aarch64 ;;
  *) fail "unsupported CPU $(uname -m)" ;;
esac

version="${IGCZ_VERSION:-}"
if [ -z "$version" ]; then
  # The /latest redirect avoids the rate-limited API.
  url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/$repo/releases/latest") ||
    fail "cannot reach GitHub"
  version="${url##*/}"
fi

name="igcz-$version-$arch-$os"
base="https://github.com/$repo/releases/download/$version"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "Downloading $name"
curl -fsSL "$base/$name.tar.gz" -o "$tmp/$name.tar.gz" || fail "no build for $version ($arch-$os)"
curl -fsSL "$base/SHA256SUMS" -o "$tmp/SHA256SUMS" || fail "cannot download checksums"

expected=$(grep " $name.tar.gz\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)
[ -n "$expected" ] || fail "no checksum for $name.tar.gz"
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$tmp/$name.tar.gz" | cut -d' ' -f1)
else
  actual=$(shasum -a 256 "$tmp/$name.tar.gz" | cut -d' ' -f1)
fi
[ "$expected" = "$actual" ] || fail "checksum mismatch"

tar -xzf "$tmp/$name.tar.gz" -C "$tmp"
mkdir -p "$dir"
install -m 755 "$tmp/$name/bin/igcz" "$dir/igcz"

echo "Installed igcz $version to $dir/igcz"
case ":$PATH:" in
  *":$dir:"*) ;;
  *) echo "Add $dir to your PATH to run it." ;;
esac
