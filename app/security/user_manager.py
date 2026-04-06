from app.infrastructure.supabase_db import db
from app.security.jwt_handler import get_password_hash, verify_password
from typing import Optional, Dict

class UserManager:
    @staticmethod
    async def get_user(username: str) -> Optional[Dict]:
        """Fetch user from Supabase 'users' table."""
        try:
            # We assume a 'users' table exists with username and hashed_password columns
            query = db.client.table("users").select("*").eq("username", username).limit(1).execute()
            if query.data:
                return query.data[0]
            return None
        except Exception as e:
            print(f"[!] User retrieval error: {e}")
            return None

    @staticmethod
    async def create_user(username: str, password: str, role: str = "user"):
        """Create a new user in Supabase with hashed password."""
        hashed_password = get_password_hash(password)
        data = {
            "username": username,
            "hashed_password": hashed_password,
            "role": role,
            "is_active": True
        }
        try:
            return db.client.table("users").insert(data).execute()
        except Exception as e:
            print(f"[!] User creation error: {e}")
            return None

user_manager = UserManager()
