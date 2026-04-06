import asyncio
import sys
import os

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    print("Starting Query Worker Policy for Windows...")
    
    # Simple execution of the worker module
    import runpy
    runpy.run_module("app.core.worker", run_name="__main__")
