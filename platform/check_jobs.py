"""Quick diagnostic — check procrastinate job queue and repo status."""
import os, sys
sys.path.insert(0, ".")

env_path = "p:/EasyRepo/.env"
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import psycopg2

db_url = os.environ["DATABASE_URL"]
conn = psycopg2.connect(db_url, sslmode="require")
cur = conn.cursor()

# Check procrastinate schema columns first
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'procrastinate_jobs'
    ORDER BY ordinal_position
""")
cols = [r[0] for r in cur.fetchall()]
print("procrastinate_jobs columns:", cols)
print()

print("=== Procrastinate Jobs (last 10) ===")
cur.execute("""
    SELECT id, task_name, status, attempts, scheduled_at,
           SUBSTRING(args::text, 1, 120)
    FROM procrastinate_jobs
    ORDER BY id DESC LIMIT 10
""")
for r in cur.fetchall():
    print(f"  id={r[0]}  task={r[1]}  status={r[2]}  attempts={r[3]}")
    print(f"  scheduled={r[4]}  args={r[5]}")
    print()

print("=== Repositories ===")
cur.execute("""
    SELECT id, name, status, progress_message
    FROM repositories
    ORDER BY id DESC LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {str(r[0])[:14]}  name={r[1]}  status={r[2]}")
    print(f"  progress={r[3]}")
    print()

cur.close()
conn.close()
