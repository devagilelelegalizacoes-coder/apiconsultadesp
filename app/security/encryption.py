import os
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv

load_dotenv()

# AES-256 key must be 32 bytes
SECRET_KEY = os.getenv("ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef")
if isinstance(SECRET_KEY, str):
    SECRET_KEY = SECRET_KEY.encode()

def encrypt_data(data: str) -> str:
    """Encrypts a string using AES-256-CBC."""
    if not data:
        return ""
    
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(data.encode()) + padder.finalize()
    
    cipher = Cipher(algorithms.AES(SECRET_KEY), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
    
    return base64.b64encode(iv + encrypted_data).decode('utf-8')

def decrypt_data(encrypted_str: str) -> str:
    """Decrypts an AES-256-CBC encrypted string."""
    if not encrypted_str:
        return ""
    
    raw_data = base64.b64decode(encrypted_str)
    iv = raw_data[:16]
    encrypted_payload = raw_data[16:]
    
    cipher = Cipher(algorithms.AES(SECRET_KEY), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted_padded_data = decryptor.update(encrypted_payload) + decryptor.finalize()
    
    unpadder = padding.PKCS7(128).unpadder()
    data = unpadder.update(decrypted_padded_data) + unpadder.finalize()
    
    return data.decode('utf-8')
