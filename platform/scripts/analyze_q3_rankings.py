"""Script to analyze vector search rankings for Q3 query."""
import os
import sys

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

ISOLATED_IDS = {
    'py.utils.formatting.format_user_record',
    'py.utils.formatting.format_audit_log',
    'py.utils.formatting.truncate_text',
    'py.utils.formatting',
    'py.models.base.BaseModel.validate',
    'py.models.base.BaseModel.get_metadata',
    'py.models.base.BaseModel.to_dict',
    'py.interfaces.repository.Repository.save',
    'py.interfaces.repository.Repository.find_by_id',
    'py.interfaces.repository.Repository.delete',
}

with get_session(DB_URL) as session:
    results = search(query=Q, repo_id=REPO_ID, top_k=62, db_session=session)
    print(f'=== VECTOR SEARCH RANKING FOR Q3: "{Q}" ===\n')
    for r in results:
        is_iso = r.entity.id in ISOLATED_IDS
        tag = ' [ISOLATED ENTITY]' if is_iso else ''
        print(f'Rank {r.rank:2d} | Score: {r.score:.4f} | {r.entity.id} ({r.entity.type}){tag}')
