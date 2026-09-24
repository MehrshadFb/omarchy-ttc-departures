#!/bin/bash
# Runs inside an Arch container on the GitHub runner (privileged, with
# /dev/kvm). Drives the official omarchy-iso acceptance harness against the
# released ISO, with ci/guest as the "omarchy" test tree so the guest runs
# ci/guest/test/acceptance instead of the upstream suite.
set -euo pipefail

ISO_URL="${ISO_URL:-https://iso.omarchy.org/omarchy-4.0.4.iso}"
OUT=/work/ci/out
mkdir -p "$OUT"

pacman -Syu --noconfirm --needed qemu-full edk2-ovmf socat imagemagick tesseract tesseract-data-eng \
  gum python openssh git jq curl >/dev/null
# The harness installs its own deps through Omarchy's package helper; stand in for it.
printf '#!/bin/bash\nexec pacman -S --needed --noconfirm "$@"\n' >/usr/local/bin/omarchy-pkg-add
chmod +x /usr/local/bin/omarchy-pkg-add
ls -l /dev/kvm

git clone --depth 1 https://github.com/omacom/omarchy-iso.git /iso
mkdir -p /iso/release
echo "Downloading $ISO_URL"
curl -fL --retry 3 --retry-delay 10 -o /iso/release/omarchy.iso "$ISO_URL"
curl -fL --retry 3 -o /iso/release/omarchy.iso.sha256 "$ISO_URL.sha256" || true
if [[ -s /iso/release/omarchy.iso.sha256 ]]; then
  (cd /iso/release && sed 's#  .*#  omarchy.iso#' omarchy.iso.sha256 | sha256sum -c -)
fi

cd /iso
status=0
./bin/omarchy-iso-test release/omarchy.iso --no-preview --timeout 3000 --sync-omarchy /work/ci/guest || status=$?

run_dir=$(ls -d test-runs/*/runs/* 2>/dev/null | tail -1 || true)
if [[ -n $run_dir ]]; then
  cp -r "$run_dir"/. "$OUT"/
  rm -f "$OUT"/*.qcow2 "$OUT"/*.fd
fi
echo "harness exit: $status"
exit $status
