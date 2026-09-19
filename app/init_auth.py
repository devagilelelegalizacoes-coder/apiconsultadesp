import asyncio
from app.infrastructure.supabase_db import db
from app.security.jwt_handler import get_password_hash
from dotenv import load_dotenv

load_dotenv()

async def init_auth():
    print("[*] Initializing Auth via Supabase...")
    await db.connect()
    
    # In Supabase, usually tables are created via SQL Editor.
    # We will try to insert a test user to see if the table exists, 
    # or just print instructions for the user.
    
    username = "admin"
    password = "change-me-123"
    hashed = get_password_hash(password)
    
    data = {
        "username": username,
        "hashed_password": hashed,
        "role": "admin",
        "is_active": True
    }
    
    try:
        # Check if table exists by doing a select
        db.client.table("users").select("*").limit(1).execute()
        print("[+] 'users' table is present.")
    except Exception:
        print("[!] 'users' table NOT found in Supabase.")
        print("[*] Run the following SQL in Supabase SQL Editor:")
        print("""
        CREATE TABLE users (
            id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
        );
        """)
        return

    # Try to create default admin
    try:
        db.client.table("users").insert(data).execute()
        print(f"[+] Default admin user created: {username} / {password}")
    except Exception as e:
        if "already exists" in str(e) or "duplicate" in str(e):
            print("[*] Admin user already exists.")
        else:
            print(f"[!] Error creating admin: {e}")

    await db.close()

if __name__ == "__main__":
    asyncio.run(init_auth())
