import os
from datetime import datetime, timedelta
from typing import Optional, Any
from supabase import create_client, Client
from dotenv import load_dotenv
import json
from app.security.encryption import encrypt_data, decrypt_data

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

class SupabaseDB:
    def __init__(self):
        self.client: Optional[Client] = None
        self._init_client()

    def _init_client(self):
        """Synchronously initializes the Supabase client if credentials are available."""
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        
        if not url or not key:
            print(f"[!] [Supabase] Missing credentials.")
            print(f"[!] [Supabase] URL present: {bool(url)}")
            print(f"[!] [Supabase] Key present: {bool(key)}")
            self.client = None
            return

        try:
            self.client = create_client(url, key)
            print("[*] [Supabase] Client initialized successfully.")
        except Exception as e:
            print(f"[*] [Supabase] Initial connection error: {e}")
            self.client = None

    async def connect(self):
        """Ensures the Supabase client is initialized."""
        if not self.client:
            self._init_client()
        return self.client

    async def get(self, key: str) -> Optional[Any]:
        """Retrieves a cached value from the 'vehicle_cache' table."""
        if not self.client:
            return None
        try:
            # We use synchronous execution for now as supabase-py is primarily sync-based 
            # with some async support in wrappers, but for simpler integration we'll wrap it.
            response = self.client.table("vehicle_cache").select("value, expires_at").eq("key", key).execute()
            if response.data:
                item = response.data[0]
                # Fix for Z at the end of ISO format
                expires_at_str = item["expires_at"].replace("Z", "+00:00")
                expires_at = datetime.fromisoformat(expires_at_str)
                
                # Check expiration (ensure timezone aware comparison)
                now = datetime.now(expires_at.tzinfo)
                if expires_at > now:
                    try:
                        raw_value = item["value"]
                        # Se for string (criptografada) decripta e faz load
                        if isinstance(raw_value, str):
                            decrypted_str = decrypt_data(raw_value)
                            return json.loads(decrypted_str)
                        return raw_value
                    except Exception as dec_err:
                        print(f"Supabase Decrypt Error (Legacy Cache?): {dec_err}")
                        return None
                else:
                    # Cleanup expired item (async-like behavior in background would be better but keeping it simple)
                    self.client.table("vehicle_cache").delete().eq("key", key).execute()
            return None
        except Exception as e:
            print(f"Supabase Get Error: {e}")
            return None

    async def set(self, key: str, value: Any, expire: int = 3600):
        """Stores a value in the 'vehicle_cache' table with an expiration."""
        if not self.client:
            return
        try:
            expires_at = (datetime.now() + timedelta(seconds=expire)).isoformat()
            
            # Converte o valor para string JSON e encripta
            json_str = json.dumps(value)
            encrypted_value = encrypt_data(json_str)

            data = {
                "key": key,
                "value": encrypted_value,
                "expires_at": expires_at,
                "updated_at": datetime.now().isoformat()
            }
            # Upsert into 'vehicle_cache'
            self.client.table("vehicle_cache").upsert(data).execute()
        except Exception as e:
            print(f"Supabase Set Error: {e}")

    async def close(self):
        """Placeholder for closing connections if needed."""
        self.client = None

# Interface identical to the old cache for easy migration
db = SupabaseDB()
# For compatibility with existing code during transition
cache = db
