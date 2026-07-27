#!/bin/bash
# The correct syntax uses the 'vllm' CLI command directly
vllm serve Qwen/Qwen2.5-VL-7B-Instruct --host 0.0.0.0 --port 8000
