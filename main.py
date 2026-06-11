import os
import json
import hashlib
from contextlib import asynccontextmanager
from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles


_client = None
_lock = Lock()
_cache: dict = {}


def build_client():
    from instagrapi import Client

    username = os.environ.get("IG_USERNAME", "")
    password = os.environ.get("IG_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("Configure IG_USERNAME e IG_PASSWORD nas variaveis de ambiente.")

    cl = Client()
    cl.delay_range = [1, 3]

    session_str = os.environ.get("IG_SESSION", "")
    if session_str:
        try:
            cl.set_settings(json.loads(session_str))
        except Exception:
            pass

    cl.login(username, password)
    return cl


def get_client():
    global _client
    with _lock:
        if _client is None:
            _client = build_client()
    return _client


def reset_client():
    global _client
    with _lock:
        _client = None
        _client = build_client()
    return _client


@asynccontextmanager
async def lifespan(app):
    try:
        get_client()
        print("Instagram login OK")
    except Exception as e:
        print(f"Instagram login error: {e}")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"ok": True, "logged_in": _client is not None}


@app.get("/api/comments")
def comments(url: str = Query(...)):
    from instagrapi.exceptions import LoginRequired, MediaNotFound, ClientError

    cache_key = hashlib.md5(url.encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    last_error: Optional[str] = None

    for attempt in range(2):
        try:
            cl = get_client() if attempt == 0 else reset_client()
            media_pk = cl.media_pk_from_url(url)
            raw = cl.media_comments(media_pk, amount=2000)

            result = []
            for c in raw:
                result.append({
                    "username": c.user.username,
                    "name": c.user.full_name or c.user.username,
                    "avatar": str(c.user.profile_pic_url) if c.user.profile_pic_url else None,
                    "text": c.text,
                    "id": str(c.pk),
                })

            payload = {"ok": True, "comments": result, "total": len(result)}
            _cache[cache_key] = payload
            return payload

        except MediaNotFound:
            raise HTTPException(404, detail="Post nao encontrado ou e privado.")
        except LoginRequired:
            last_error = "Sessao expirada"
            if attempt == 1:
                raise HTTPException(401, detail="Sessao expirada. Reinicie o servidor.")
        except ClientError as e:
            raise HTTPException(400, detail=f"Erro Instagram: {e}")
        except Exception as e:
            last_error = str(e)
            if attempt == 1:
                raise HTTPException(500, detail=f"Erro interno: {last_error}")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
