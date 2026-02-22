#!/bin/bash
while true; do
  claude --print "$(cat PROMPT.md)"
  if grep -q "COMPLETE" output.log 2>/dev/null; then
    echo "작업 완료!"
    break
  fi
  sleep 2
done
