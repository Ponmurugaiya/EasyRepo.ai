import os, sys

with open(r'P:\EasyRepo\.env') as f:
    for line in f:
        line = line.strip()
        if line and '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

sys.path.insert(0, 'platform')

import argparse
from pathlib import Path

sys.argv = ['validate_retrieval.py', '--manifest', r'P:\EasyRepo\sample-repo\test-manifest.json']

# Redirect stdout to file
original_stdout = sys.stdout
with open(r'P:\EasyRepo\validate_final.txt', 'w', encoding='utf-8', newline='\n') as f:
    sys.stdout = f
    try:
        exec(open(r'P:\EasyRepo\platform\scripts\validate_retrieval.py').read())
    finally:
        sys.stdout = original_stdout

print("Output written to validate_final.txt")
