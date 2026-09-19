import asyncio
import sys
import os
from app.infrastructure.supabase_db import db
from app.security.jwt_handler import get_password_hash
from dotenv import load_dotenv

load_dotenv()

async def create_user(username, password, role="user"):
    print(f"[*] Creating user '{username}' with role '{role}'...")
    await db.connect()
    
    hashed = get_password_hash(password)
    
    data = {
        "username": username,
        "hashed_password": hashed,
        "role": role,
        "is_active": True
    }
    
    try:
        db.client.table("users").insert(data).execute()
        print(f"[+] User '{username}' created successfully.")
    except Exception as e:
        if "already exists" in str(e) or "duplicate" in str(e):
            print(f"[!] User '{username}' already exists.")
        else:
            print(f"[!] Error creating user: {e}")

    await db.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python create_user_cli.py <username> <password> [role]")
        sys.exit(1)
        
    user = sys.argv[1]
    pwd = sys.argv[2]
    role = sys.argv[3] if len(sys.argv) > 3 else "user"
    
    asyncio.run(create_user(user, pwd, role))
