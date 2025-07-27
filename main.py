from fastapi import FastAPI, HTTPException, Depends, Request, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from sqlmodel import SQLModel, Session, create_engine, select
from pydantic import BaseModel
from dotenv import load_dotenv
import os, httpx, time

from auth import (
    hash_password, verify_password,
    create_access_token, decode_access_token,
    create_refresh_token, decode_token
)
from models import User, Conversation, Message

load_dotenv()
app = FastAPI()

# ====== CORS for frontend ======
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Add production frontend URLs as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== SQLite setup ======
engine = create_engine("sqlite:///db.sqlite3", echo=True)

@app.on_event("startup")
def init_db():
    SQLModel.metadata.create_all(engine)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload["sub"]  # Returns user's email

# ----------- MODELS / SCHEMAS -----------
class PromptRequest(BaseModel):
    prompt: str
    persona: str

# ============== API ROUTES ==============

@app.get("/ping")
def ping(token: str = Depends(oauth2_scheme)):
    # Verifies token validity
    return {"msg": "pong"}

# ------------ User Management -----------
@app.get("/users")
def get_users():
    with Session(engine) as session:
        return session.exec(select(User)).all()

@app.post("/users")
def create_user(user: User):
    with Session(engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
        return {"msg": "User added", "user": user}

@app.get("/users/{user_id}")
def get_user(user_id: int):
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            return user
        raise HTTPException(status_code=404, detail="User not found")

@app.put("/users/{user_id}")
def update_user(user_id: int, updated_user: User):
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.name = updated_user.name
        user.email = updated_user.email
        session.add(user)
        session.commit()
        session.refresh(user)
        return {"msg": "User updated", "user": user}

@app.delete("/users/{user_id}")
def delete_user(user_id: int):
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        session.delete(user)
        session.commit()
        return {"msg": "User deleted"}

# ----------- Auth (Signup/Login/Refresh/Logout) -----------
@app.post("/signup")
def signup(user: User):
    with Session(engine) as session:
        existing_user = session.exec(select(User).where(User.email == user.email)).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="User already exists")
        user.password = hash_password(user.password)
        session.add(user)
        session.commit()
        session.refresh(user)
        return {"msg": "User registered!"}

@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == form_data.username)).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if not verify_password(form_data.password, user.password):
            raise HTTPException(status_code=401, detail="Incorrect password")
        access_token = create_access_token(data={"sub": user.email}, expires_minutes=15)
        refresh_token = create_refresh_token(data={"sub": user.email})
        response = JSONResponse(content={"access_token": access_token, "token_type": "bearer"})
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=False,
            samesite="strict",
            max_age=7 * 24 * 60 * 60
        )
        return response

@app.post("/refresh")
def refresh_token(request: Request):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token found")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    new_access_token = create_access_token(data={"sub": payload["sub"]}, expires_minutes=15)
    return {"access_token": new_access_token, "token_type": "bearer"}

@app.get("/logout")
def logout():
    response = JSONResponse(content={"msg": "Logged out"})
    response.delete_cookie("refresh_token")
    return response

@app.get("/profile")
def protected_profile(current_user: str = Depends(get_current_user)):
    return {"msg": f"Hello, {current_user}! This is a protected route."}

# ------------ AI Generate (OpenRouter) -----------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

@app.post("/generate-text")
async def generate_text(request: PromptRequest, current_user: str = Depends(get_current_user)):
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="Missing OpenRouter API key")

    system_prompts = {
        "friendly": "You are a friendly and helpful assistant. Speak casually and warmly.",
        "sarcastic": "You're a sarcastic, witty assistant who never misses a chance to roast.",
        "dev": "You are DevGPT, a skilled AI engineer who explains code precisely.",
        "translator": "You are a multilingual translator. Translate input accurately."
    }
    system_prompt = system_prompts.get(request.persona, system_prompts["friendly"])
    payload = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.prompt}
        ]
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "http://localhost:8000",
        "Content-Type": "application/json"
    }
    try:
        start = time.time()
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
        result = response.json()
        if "choices" in result and result["choices"]:
            return {
                "response": result["choices"][0]["message"]["content"].strip(),
                "duration": round(time.time() - start, 2)
            }
        raise HTTPException(status_code=500, detail="Invalid response from OpenRouter")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Request error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GenAI Error: {str(e)}")

# ------------ Conversations & Messages -----------
@app.post("/save-conversation")
def save_conversation(data: dict = Body(...), user=Depends(get_current_user)):
    with Session(engine) as session:
        convo = Conversation(title=data["title"], user_id=user)
        session.add(convo)
        session.commit()
        session.refresh(convo)
        for msg in data["messages"]:
            m = Message(content=msg["content"], role=msg["role"], conversation_id=convo.id)
            session.add(m)
        session.commit()
    return {"msg": "Conversation saved"}

@app.get("/conversations")
def get_conversations(user=Depends(get_current_user)):
    with Session(engine) as session:
        convos = session.exec(select(Conversation).where(Conversation.user_id == user)).all()
        result = []
        for convo in convos:
            messages = session.exec(select(Message).where(Message.conversation_id == convo.id)).all()
            result.append({
                "id": convo.id,
                "title": convo.title,
                "created_at": convo.created_at,
                "messages": [{"role": m.role, "content": m.content} for m in messages]
            })
        return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

