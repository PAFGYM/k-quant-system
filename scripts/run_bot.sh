#!/bin/bash
# K-Quant Bot launcher for launchd
cd /Users/botddol/k-quant-system
export PYTHONPATH="/Users/botddol/k-quant-system/src"
exec /usr/bin/python3 -m kstock.app
