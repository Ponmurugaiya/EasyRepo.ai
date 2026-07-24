"""Debug script: print retrieval ranks for Q3."""
import os, sys

with open(r'P:\EasyRepo\.env') as f:
    for line in f:
        line = line.strip()
        if line and '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

sys.path.insert(0, '.')

from src.retrieval import search
from src.storage.db import get_session

DB_URL = 'postgresql://postgres:postgres@127.0.0.1:5435/easyrepo'
REPO_ID = 'sample-repo'
Q = 'Is there any function in this codebase that has no dependencies on other code?'

with get_session(DB_URL) as session:
    results = search(query=Q, repo_id=REPO_ID, top_k=25, db_session=session)
    for r in results:
        marker = ' <-- FORMATTING' if 'formatting' in r.entity.file_path else ''
        print(f'rank={r.rank} score={r.score:.3f}  {r.entity.id}  file={r.entity.file_path}{marker}')
