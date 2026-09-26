#!/bin/bash
# Double-click in Finder: pick a wedding folder, pick a look, get Lightroom edits and/or finished JPEGs.
cd "$(dirname "$0")/.." || exit 1
if [ ! -f .venv/bin/activate ]; then
  osascript -e 'display alert "vivaah is not installed yet" message "Open Terminal in the vivaah folder and run ./setup_mac.sh first."'
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

FOLDER="$(osascript -e 'POSIX path of (choose folder with prompt "Choose the wedding folder to grade")' 2>/dev/null)" || exit 0
[ -z "$FOLDER" ] && exit 0

LOOK_LIST="$(vivaah looks | awk '{printf "%s\"%s\"", (NR>1?",":""), $1}')"
LOOK="$(osascript -e "choose from list {${LOOK_LIST}} with prompt \"Choose the look\" default items {\"signature\"}" 2>/dev/null)"
[ -z "$LOOK" ] || [ "$LOOK" = "false" ] && exit 0

MODE="$(osascript -e 'button returned of (display dialog "What should vivaah create?" buttons {"Finished JPEGs", "Both", "Lightroom edits"} default button "Lightroom edits" with title "vivaah")' 2>/dev/null)" || exit 0

OUT="${FOLDER%/}/vivaah-graded"
ARGS=(grade "$FOLDER" --look "$LOOK")
case "$MODE" in
  "Lightroom edits") ARGS+=(--xmp) ;;
  "Finished JPEGs") ARGS+=(--export "$OUT") ;;
  *) ARGS+=(--xmp --export "$OUT") ;;
esac

echo "vivaah ${ARGS[*]}"
vivaah "${ARGS[@]}"
STATUS=$?
if [ $STATUS -eq 0 ]; then
  osascript -e 'display notification "Grading finished" with title "vivaah"'
  if [ -d "$OUT" ]; then open "$OUT"; else open "${FOLDER%/}/vivaah_report"; fi
fi
echo
read -r -p "Done. Press Enter to close this window."
