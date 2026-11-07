#!/usr/bin/env bash
# Double-click this file in Finder to start StudyBench on a Mac.
cd "$(dirname "$0")" || exit 1
bash ./run.sh
status=$?
echo
echo "StudyBench stopped (exit code $status). You can close this window."
read -r -p "Press Enter to close..." _
