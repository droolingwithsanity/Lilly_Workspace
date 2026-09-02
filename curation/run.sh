#!/bin/bash
cd ~/curation
rm -f training.log status.json
nohup python3 -u pipeline.py > training.log 2>&1 &
PID=$!
echo $PID > pipeline.pid
echo "Pipeline started with PID $PID"
echo "Monitor: tail -f ~/curation/training.log"
echo "Stop:    kill $(cat ~/curation/pipeline.pid)"
