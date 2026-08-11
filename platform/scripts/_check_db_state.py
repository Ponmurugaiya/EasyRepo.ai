# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r'P:\EasyRepo\platform')
with open(r'P:\EasyRepo\.env', encoding='utf-8') as f:
    for l in f:
        l=l.strip()
        if l and '=' in l and not l.startswith('#'):
            k,v=l.split('=',1); os.environ.setdefault(k.strip(),v.strip())
from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    repos = c.execute(text('SELECT id, name, status, indexed_at FROM repositories ORDER BY indexed_at DESC NULLS LAST LIMIT 5')).fetchall()
    print("=== REPOSITORIES ===")
    for r in repos:
        print(r)
    if repos:
        for repo in repos:
            rid = repo[0]
            ec = c.execute(text('SELECT COUNT(*) FROM entities WHERE repo_id=:r'), {'r':rid}).scalar()
            nc = c.execute(text('SELECT COUNT(*) FROM entities WHERE repo_id=:r AND embedding IS NULL'), {'r':rid}).scalar()
            rc2 = c.execute(text('SELECT COUNT(*) FROM relationships WHERE repo_id=:r'), {'r':rid}).scalar()
            print(f'  repo_id={rid}  status={repo[2]}  entities={ec}  null_embeddings={nc}  relationships={rc2}')
    else:
        print("No repositories found.")
