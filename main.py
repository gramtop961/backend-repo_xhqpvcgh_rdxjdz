import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents

app = FastAPI(title="Telegram 2.0 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------
# Helpers & Auth (very simple mock token)
# ------------------------

def now_utc():
    return datetime.now(timezone.utc)


def collection(name: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    return db[name]


def require_user(user_id: Optional[str]):
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")


# ------------------------
# Schemas for requests
# ------------------------

class OTPRequest(BaseModel):
    phone: str

class OTPVerify(BaseModel):
    phone: str
    code: str

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    status: Optional[str] = None
    privacy_mode: Optional[bool] = None
    creator_mode: Optional[bool] = None
    notifications_enabled: Optional[bool] = None

class ChatCreate(BaseModel):
    type: str = Field(..., pattern="^(personal|group|channel)$")
    title: str
    participants: List[str] = []

class MessageCreate(BaseModel):
    text: str = ""
    attachments: List[Dict[str, Any]] = []
    thread_root_id: Optional[str] = None

class ChannelCreate(BaseModel):
    title: str
    description: str = ""
    tags: List[str] = []

class PostCreate(BaseModel):
    content_text: str = ""
    media: List[Dict[str, Any]] = []
    scheduled_at: Optional[datetime] = None

class AnalyticsEventIn(BaseModel):
    event: str
    meta: Dict[str, Any] = {}

class AutomationFlowIn(BaseModel):
    name: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]

class AutomationExecuteIn(BaseModel):
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    payload: Dict[str, Any] = {}

# ------------------------
# Basic routes
# ------------------------

@app.get("/")
def read_root():
    return {"message": "Telegram 2.0 Backend Running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected"
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()
    except Exception as e:
        response["database"] = f"⚠️ Error: {str(e)[:80]}"
    return response

# ------------------------
# Schema endpoint for tooling
# ------------------------

@app.get("/schema")
def get_schema():
    try:
        import schemas as s
        models = [
            {"name": name, "fields": list(getattr(s, name).model_fields.keys())}
            for name in dir(s)
            if hasattr(getattr(s, name), "model_fields")
        ]
        return {"models": models}
    except Exception as e:
        return {"error": str(e)}

# ------------------------
# Auth: OTP mock flow
# ------------------------

@app.post("/api/auth/request-otp")
def request_otp(payload: OTPRequest):
    code = "123456"  # Mocked code
    sessions = collection("session")
    sessions.insert_one({
        "phone": payload.phone,
        "otp_code": code,
        "verified": False,
        "created_at": now_utc(),
        "updated_at": now_utc()
    })
    return {"ok": True, "code": code}

@app.post("/api/auth/verify-otp")
def verify_otp(payload: OTPVerify):
    sess = collection("session").find_one({"phone": payload.phone}, sort=[("created_at", -1)])
    if not sess or sess.get("otp_code") != payload.code:
        raise HTTPException(status_code=400, detail="Invalid code")
    # Find or create user
    user = collection("user").find_one({"phone": payload.phone})
    if not user:
        uid = create_document("user", {"phone": payload.phone, "name": "", "privacy_mode": False,
                                         "creator_mode": False, "notifications_enabled": True})
        user = collection("user").find_one({"_id": collection("user").find_one({"_id": collection("user").find_one})})
        user = collection("user").find_one({"phone": payload.phone})
    token = user["_id"] if user else None
    collection("session").update_many({"phone": payload.phone}, {"$set": {"verified": True, "updated_at": now_utc()}})
    return {"ok": True, "user_id": str(token)}

@app.get("/api/users/me")
def get_me(x_user_id: Optional[str] = None):
    # fastapi won't map headers automatically with hyphen in signature; use dependency via request.headers is verbose
    from fastapi import Request
    def responder(request: Request):
        user_id = request.headers.get("X-User-Id")
        require_user(user_id)
        u = collection("user").find_one({"_id": collection("user").database.client.get_default_database()["user"].find_one})
        # Fallback by _id string
        from bson import ObjectId
        try:
            u = collection("user").find_one({"_id": ObjectId(user_id)})
        except Exception:
            u = None
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u["_id"] = str(u["_id"])
        return u
    return responder

@app.put("/api/users/me")
async def update_me(request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    body = await request.json()
    from bson import ObjectId
    try:
        collection("user").update_one({"_id": ObjectId(user_id)}, {"$set": {**body, "updated_at": now_utc()}})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id")
    return {"ok": True}

# ------------------------
# Chats & messages
# ------------------------

@app.get("/api/chats")
def list_chats(request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    chats = list(collection("chat").find({"participants": user_id}).sort("last_message_at", -1))
    for c in chats:
        c["_id"] = str(c["_id"])
    return chats

@app.post("/api/chats")
def create_chat(payload: ChatCreate, request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    participants = list(set(payload.participants + [user_id]))
    cid = create_document("chat", {
        "type": payload.type,
        "title": payload.title,
        "participants": participants,
        "last_message_at": now_utc(),
        "pinned": False
    })
    return {"ok": True, "chat_id": cid}

@app.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: str, limit: int = 50):
    msgs = list(collection("message").find({"chat_id": chat_id}).sort("created_at", -1).limit(limit))
    for m in msgs:
        m["_id"] = str(m["_id"])
    return list(reversed(msgs))

@app.post("/api/chats/{chat_id}/messages")
def send_message(chat_id: str, payload: MessageCreate, request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    mid = create_document("message", {
        "chat_id": chat_id,
        "sender_id": user_id,
        "text": payload.text,
        "attachments": payload.attachments,
        "thread_root_id": payload.thread_root_id,
        "reactions": {},
    })
    collection("chat").update_one({"_id": {"$exists": True}, "_id": collection("chat").find_one}, {"$set": {"last_message_at": now_utc()}})
    # Notify via websockets
    await_broadcast(chat_id, {
        "type": "message",
        "message_id": mid,
        "text": payload.text,
        "sender_id": user_id,
        "created_at": now_utc().isoformat()
    })
    return {"ok": True, "message_id": mid}

# ------------------------
# WebSocket manager per chat
# ------------------------

class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, List[WebSocket]] = {}

    async def connect(self, chat_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active.setdefault(chat_id, []).append(websocket)

    def disconnect(self, chat_id: str, websocket: WebSocket):
        if chat_id in self.active and websocket in self.active[chat_id]:
            self.active[chat_id].remove(websocket)

    async def broadcast(self, chat_id: str, message: Dict[str, Any]):
        for ws in list(self.active.get(chat_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(chat_id, ws)

manager = ConnectionManager()

async def await_broadcast(chat_id: str, message: Dict[str, Any]):
    await manager.broadcast(chat_id, message)

@app.websocket("/ws/chats/{chat_id}")
async def chat_ws(websocket: WebSocket, chat_id: str):
    await manager.connect(chat_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(chat_id, {"type": "ping", "echo": data})
    except WebSocketDisconnect:
        manager.disconnect(chat_id, websocket)

# ------------------------
# Channels & posts
# ------------------------

@app.get("/api/channels")
def list_channels(tag: Optional[str] = None):
    q = {"tags": tag} if tag else {}
    chans = list(collection("channel").find(q).sort("trending_score", -1).limit(50))
    for ch in chans:
        ch["_id"] = str(ch["_id"])
    return chans

@app.post("/api/channels")
def create_channel(payload: ChannelCreate, request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    cid = create_document("channel", {
        "title": payload.title,
        "description": payload.description,
        "owner_id": user_id,
        "members": [user_id],
        "tags": payload.tags,
        "trending_score": 0.0,
    })
    return {"ok": True, "channel_id": cid}

@app.get("/api/channels/{channel_id}")
def get_channel(channel_id: str):
    from bson import ObjectId
    ch = collection("channel").find_one({"_id": ObjectId(channel_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    ch["_id"] = str(ch["_id"])
    return ch

@app.get("/api/channels/{channel_id}/posts")
def list_posts(channel_id: str):
    posts = list(collection("post").find({"channel_id": channel_id}).sort("created_at", -1))
    for p in posts:
        p["_id"] = str(p["_id"])
    return posts

@app.post("/api/channels/{channel_id}/posts")
def create_post(channel_id: str, payload: PostCreate, request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    pid = create_document("post", {
        "channel_id": channel_id,
        "author_id": user_id,
        "content_text": payload.content_text,
        "media": payload.media,
        "scheduled_at": payload.scheduled_at,
    })
    return {"ok": True, "post_id": pid}

# ------------------------
# Analytics
# ------------------------

@app.post("/api/analytics/events")
def analytics_event(payload: AnalyticsEventIn, request):
    user_id = request.headers.get("X-User-Id")
    create_document("analyticsevent", {
        "user_id": user_id or "anon",
        "event": payload.event,
        "meta": payload.meta,
    })
    return {"ok": True}

# ------------------------
# Automation engine simulation
# ------------------------

@app.get("/api/automation/flows")
def list_flows(request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    flows = list(collection("automationflow").find({"owner_id": user_id}).sort("created_at", -1))
    for f in flows:
        f["_id"] = str(f["_id"])
    return flows

@app.post("/api/automation/flows")
def save_flow(payload: AutomationFlowIn, request):
    user_id = request.headers.get("X-User-Id")
    require_user(user_id)
    fid = create_document("automationflow", {
        "owner_id": user_id,
        "name": payload.name,
        "nodes": payload.nodes,
        "edges": payload.edges,
    })
    return {"ok": True, "flow_id": fid}

@app.post("/api/automation/execute")
def execute_flow(payload: AutomationExecuteIn):
    logs: List[str] = []
    ctx = payload.payload.copy()

    node_map = {n.get("id"): n for n in payload.nodes}
    # naive: start with first node with type == 'trigger'
    start_nodes = [n for n in payload.nodes if n.get("type") == "trigger"] or payload.nodes[:1]

    def log(msg: str):
        ts = now_utc().isoformat()
        logs.append(f"[{ts}] {msg}")

    for start in start_nodes:
        current_id = start.get("id")
        visited = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            node = node_map.get(current_id)
            if not node:
                break
            ntype = node.get("type")
            cfg = node.get("config", {})
            if ntype == "trigger":
                log(f"Trigger: {cfg.get('name', 'start')}")
            elif ntype == "condition":
                cond = cfg.get("expr", "True")
                try:
                    ok = bool(eval(cond, {}, {"ctx": ctx}))
                except Exception:
                    ok = False
                log(f"Condition '{cond}' -> {ok}")
                if not ok:
                    break
            elif ntype == "action":
                act = cfg.get("name", "noop")
                if act == "send_message":
                    log(f"Action: send_message -> {cfg.get('text', '')}")
                elif act == "delay":
                    log(f"Action: delay {cfg.get('ms', 0)}ms (simulated)")
                else:
                    log(f"Action: {act}")
            # move to next via first matching edge
            next_edges = [e for e in payload.edges if e.get("from") == current_id]
            current_id = next_edges[0]["to"] if next_edges else None

    return {"ok": True, "logs": logs, "context": ctx}

# ------------------------
# Mini-apps (static examples)
# ------------------------

MINIAPPS = [
    {"id": "vault", "name": "Vault", "icon": "lock", "pages": ["Home", "Cards", "Payments"]},
    {"id": "tasks", "name": "Tasks", "icon": "check", "pages": ["Today", "Upcoming"]},
]

@app.get("/api/miniapps")
def miniapps():
    return MINIAPPS

@app.get("/api/miniapps/{app_id}")
def miniapp_detail(app_id: str):
    for m in MINIAPPS:
        if m["id"] == app_id:
            return m
    raise HTTPException(status_code=404, detail="Mini-app not found")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
