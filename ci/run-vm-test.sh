#!/bin/bash
# Runs inside an Arch container on the GitHub runner (privileged, with
# /dev/kvm). Drives the official omarchy-iso acceptance harness against the
# released ISO, with ci/guest as the "omarchy" test tree so the guest runs
# ci/guest/test/acceptance instead of the upstream suite.
set -euo pipefail

ISO_URL="${ISO_URL:-https://iso.omarchy.org/omarchy-4.0.4.iso}"
OUT=/work/ci/out
mkdir -p "$OUT"
# Everything here runs as root inside the container; the runner's upload step
# runs as an ordinary user, so leave the results world-readable no matter how
# the harness exits.
trap 'chmod -R a+rwX "$OUT" 2>/dev/null || true' EXIT

pacman -Syu --noconfirm --needed qemu-full edk2-ovmf socat imagemagick tesseract tesseract-data-eng \
  gum python openssh git jq curl >/dev/null
# The harness installs its own deps through Omarchy's package helper; stand in for it.
printf '#!/bin/bash\nexec pacman -S --needed --noconfirm "$@"\n' >/usr/local/bin/omarchy-pkg-add
chmod +x /usr/local/bin/omarchy-pkg-add
ls -l /dev/kvm

# The harness is fetched at one reviewed commit and checked out detached, so a
# moving branch cannot change what this workflow executes. Bump deliberately.
OMARCHY_ISO_COMMIT="${OMARCHY_ISO_COMMIT:-86c07785cb0f63be78edb1349843d5817b5c0e66}"
[[ $OMARCHY_ISO_COMMIT =~ ^[0-9a-f]{40}$ ]] || { echo "OMARCHY_ISO_COMMIT must be a full 40-character commit sha" >&2; exit 1; }
git init -q /iso
git -C /iso remote add origin https://github.com/omacom/omarchy-iso.git
git -C /iso fetch -q --depth 1 origin "$OMARCHY_ISO_COMMIT"
git -C /iso checkout -q --detach "$OMARCHY_ISO_COMMIT"
echo "omarchy-iso at $(git -C /iso rev-parse HEAD)"
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
  rm -f "$OUT"/*.qcow2 "$OUT"/*.fd "$OUT"/.capture.ppm
fi
# Keep the install-phase console captures too; they explain a broken base image.
for f in test-runs/*/*.png test-runs/*/*.log; do
  [[ -f $f ]] && cp "$f" "$OUT/base-$(basename "$f")"
done
echo "harness exit: $status"
exit $status
