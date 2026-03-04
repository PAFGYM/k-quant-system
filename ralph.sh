#!/bin/bash
# ralph.sh → kbot 포워딩 래퍼 (하위 호환)
exec "$(dirname "$0")/kbot" "$@"
