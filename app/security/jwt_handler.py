import os
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from dotenv import load_dotenv

load_dotenv()

# Configuration
SECRET_KEY = os.getenv("JWT_SECRET", "y0uR_Sup3r_S3cr3t_K3y_H3r3_V3ry_L0ng")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password using direct bcrypt."""
    try:
        password_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception as e:
        print(f"Error verifying password: {e}")
        return False

def get_password_hash(password: str) -> str:
    """Hash a password using direct bcrypt."""
    # Generate a salt and hash the password
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    try:
        decoded_token = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # JWT verify_exp is handled by decode itself, but we can be explicit
        exp = decoded_token.get("exp")
        if exp and datetime.utcnow().timestamp() > exp:
            return None
        return decoded_token
    except JWTError:
        return None
