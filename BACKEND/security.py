"""
security.py
Pilar A: Gobierno de Datos y Seguridad por Roles.

- Autenticación stateless con JWT (PyJWT).
- Los roles vienen embebidos y firmados en el token; el backend jamás
  confía en un rol enviado por el cliente fuera del token.
- El enmascaramiento de datos confidenciales ocurre en los serializers
  de routers.py, condicionado al rol resuelto aquí — NUNCA en el frontend.
"""
import functools
import hashlib
import os
from datetime import datetime, timedelta, timezone

import jwt
from flask import request, jsonify, g

SECRET_KEY = os.environ.get("SST_JWT_SECRET", "dev-secret-cambiar-en-produccion-9f3a")
ALGORITHM = "HS256"
TOKEN_TTL_MIN = 60

ROLE_HRBP = "HRBP"
ROLE_MEDICO = "MEDICO"

# --- Usuarios de prueba (en un sistema real vendrían de una tabla `usuarios`
#     con password hasheado + salt individual; aquí se simplifica para el
#     prototipo pero SIGUE sin guardar contraseñas en texto plano). ---
def _hash(password: str) -> str:
    return hashlib.sha256(f"sst-salt::{password}".encode()).hexdigest()

USERS = {
    "hrbp.lider": {
        "password_hash": _hash("Lider2026*"),
        "role": ROLE_HRBP,
        "nombre": "Lider Prueba (HRBP / Líder de Área)",
    },
    "medico.sst": {
        "password_hash": _hash("Medico2026*"),
        "role": ROLE_MEDICO,
        "nombre": "Dr. Prueba (Médico Ocupacional / Admin SST)",
    },
}


def authenticate(username: str, password: str):
    user = USERS.get(username)
    if not user or user["password_hash"] != _hash(password):
        return None
    return user


def create_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str):
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def require_auth(allowed_roles=None):
    """
    Decorator de Flask: exige un Bearer JWT válido y, si se especifica
    allowed_roles, restringe el endpoint completo a esos roles.
    Deja el usuario autenticado en flask.g.user para uso del handler.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Token de autenticación faltante"}), 401
            token = auth_header.split(" ", 1)[1]
            try:
                payload = decode_token(token)
            except jwt.ExpiredSignatureError:
                return jsonify({"error": "Token expirado, inicia sesión de nuevo"}), 401
            except jwt.InvalidTokenError:
                return jsonify({"error": "Token inválido"}), 401

            if allowed_roles and payload["role"] not in allowed_roles:
                return jsonify({"error": "No tienes permisos para acceder a este recurso"}), 403

            g.user = {"username": payload["sub"], "role": payload["role"]}
            return fn(*args, **kwargs)
        return wrapper
    return decorator
