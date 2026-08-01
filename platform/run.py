"""Entry point for running the EasyRepo API on Windows.

Must be run with: p:\\EasyRepo\\.venv\\Scripts\\python.exe run.py

Sets WindowsSelectorEventLoopPolicy BEFORE uvicorn creates its event loop,
then passes loop="none" so uvicorn doesn't override it with ProactorEventLoop.
psycopg v3 async requires SelectorEventLoop on Windows.
"""
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        loop="none",  # Don't let uvicorn override our SelectorEventLoop policy
    )
