# -*- coding: utf-8 -*-
"""Delete all failed/incomplete repository rows so we start clean."""
import os, sys
sys.path.insert(0, r'P:\EasyRepo\platform')
with open(r'P:\EasyRepo\.env', encoding='utf-8') as f:
    for l in f:
        l = l.strip()
        if l and '=' in l and not l.startswith('#'):
            k, v = l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    result = c.execute(text("DELETE FROM repositories"))
    c.execute(text("TRUNCATE conversation_memory"))
    c.execute(text("TRUNCATE conversations CASCADE"))
    c.commit()
    remaining = c.execute(text("SELECT COUNT(*) FROM repositories")).scalar()
    ents = c.execute(text("SELECT COUNT(*) FROM entities")).scalar()
    print(f"Deleted all repos. Remaining: repos={remaining}  entities={ents}")
