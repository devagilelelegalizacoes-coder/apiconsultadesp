import uvicorn
import asyncio
import sys

if __name__ == "__main__":
    if sys.platform == 'win32':
        # Force the loop that supports subprocesses on Windows
        # This MUST be set before the event loop is created or used
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    print("Starting API with ProactorEventLoop policy for Windows...")
    
    # Run uvicorn with reload=True
    # Running from this script ensures the event loop policy is set correctly 
    # before uvicorn starts the server, which is critical for Playwright on Windows.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, loop="asyncio")