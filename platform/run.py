"""Entry point for running the EasyRepo API on Windows.

Run with:
    p:\\EasyRepo\\.venv\\Scripts\\python.exe run.py

Sets WindowsSelectorEventLoopPolicy before anything else runs, then
starts uvicorn with loop="none" so uvicorn uses our pre-configured loop.
psycopg v3 async requires SelectorEventLoop — ProactorEventLoop (Windows
default) is incompatible.
"""
import sys
import asyncio

# Must be first — before any uvicorn or asyncio import creates a loop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Also create and set the loop immediately so nothing else creates a
    # ProactorEventLoop before uvicorn starts
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        loop="none",  # Don't let uvicorn override our SelectorEventLoop
    )
