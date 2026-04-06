import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from app.infrastructure.supabase_db import db as database

class JobManager:
    """
    Manages the query queue in Supabase.
    """
    
    async def create_job(self, placa: str = None, renavam: str = None, cpf: str = None, query_type: str = "orcamento") -> str:
        """Adds a new query to the pending queue."""
        if not database.client:
            await database.connect()
            
        data = {
            "placa": placa,
            "renavam": renavam,
            "cpf": cpf,
            "query_type": query_type,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }
        
        response = database.client.table("query_queue").insert(data).execute()
        if response.data:
            return response.data[0]["id"]
        raise Exception("Failed to insert job into queue")

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Retrieves the status and result of a job."""
        if not database.client:
            await database.connect()
            
        response = database.client.table("query_queue").select("*").eq("id", job_id).execute()
        if response.data:
            return response.data[0]
        return {"status": "not_found"}

    async def claim_next_job(self) -> Optional[Dict[str, Any]]:
        """Claims the next pending job from the queue (atomically)."""
        if not database.client:
            await database.connect()
            
        # Select one pending job
        response = database.client.table("query_queue").select("*").eq("status", "pending").order("created_at").limit(1).execute()
        
        if not response.data:
            return None
            
        job = response.data[0]
        job_id = job["id"]
        
        # Update status to processing (Atomic lock)
        update_res = database.client.table("query_queue").update({
            "status": "processing",
            "updated_at": datetime.now().isoformat()
        }).eq("id", job_id).eq("status", "pending").execute() # Status check for basic race condition prevention
        
        if update_res.data:
            return update_res.data[0]
        return None

    async def update_job_result(self, job_id: str, status: str, result: Any = None, error: str = None):
        """Finalizes the job with a result or error."""
        if not database.client:
            await database.connect()
            
        update_data = {
            "status": status,
            "result": result,
            "error_message": error,
            "updated_at": datetime.now().isoformat()
        }
        
        database.client.table("query_queue").update(update_data).eq("id", job_id).execute()
