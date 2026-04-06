import asyncio
import sys
import os
from dotenv import load_dotenv

# Path adjust for imports
sys.path.append(os.getcwd())

from app.core.job_manager import JobManager
from app.core.budget_coordinator import BudgetCoordinator
from app.infrastructure.supabase_db import db as database

# Ensure win32 loop policy
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def run_worker():
    print("[*] [Worker] Starting background worker...")
    await database.connect()
    manager = JobManager()
    coordinator = BudgetCoordinator()
    
    while True:
        try:
            # 1. Seek pending jobs
            job = await manager.claim_next_job()
            
            if not job:
                # No pending jobs, wait a bit
                await asyncio.sleep(5)
                continue
                
            job_id = job["id"]
            placa = job["placa"]
            query_type = job["query_type"]
            
            print(f"[+] [Worker] Processing Job: {job_id} | Type: {query_type} | Placa: {placa}")
            
            try:
                # 2. Execute query workflow
                if query_type == "orcamento":
                    # Workflow specific for budget
                    result = await coordinator.run_budget_query(placa, user="worker")
                    
                if result.get("status") == "success":
                    await manager.update_job_result(job_id, "completed", result=result.get("data"))
                    print(f"[+] [Worker] Job {job_id} completed successfully.")
                else:
                    await manager.update_job_result(job_id, "failed", error=result.get("message"))
                    print(f"[-] [Worker] Job {job_id} failed: {result.get('message')}")
                    
            except Exception as inner_e:
                error_msg = str(inner_e)
                await manager.update_job_result(job_id, "failed", error=error_msg)
                print(f"[!] [Worker] Fatal error on job {job_id}: {error_msg}")
                
            # Brief delay between jobs to stay healthy
            await asyncio.sleep(2)
            
        except Exception as e:
            print(f"[!] [Worker] Main loop encountered an error: {str(e)}")
            await asyncio.sleep(10) # Wait more on main errors

if __name__ == "__main__":
    asyncio.run(run_worker())
