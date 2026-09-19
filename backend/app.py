"""换技 SkillSwap API。

运行：  uvicorn app:app --reload --port 8787      文档：  http://localhost:8787/docs
"""
import os
import random
import secrets
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import db
import matching
import seed as seed_mod

ORIGINS = os.environ.get("SKILLSWAP_ORIGINS", "https://usaidokay.github.io,http://localhost:8000,http://127.0.0.1:8000").split(",")
DEMO_REPLIES = [
    "好呀，我这周末白天有空，你呢？",
    "可以的，要不先来个 15 分钟试课，互相感受一下节奏？",
    "收到，我把练习资料整理一下发你。",
    "没问题，那就按每周一次来，谁也别鸽谁哈。",
]

app = FastAPI(title="换技 SkillSwap API", version="0.1.0",
              description="技能互换平台后端：两张清单 → 可解释匹配 → 学习契约 → 打卡履约 → 互评与时间币。")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    conn = db.connect()
    db.init(conn)
    seed_mod.seed(conn)
    conn.close()


def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def current_user(authorization: Optional[str] = Header(None), conn=Depends(get_db)):
    token = (authorization or "").replace("Bearer ", "").strip()
    row = token and conn.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(401, "需要登录：先调用 POST /api/auth/guest 获取 token")
    return row


# ---------- 序列化 ----------

def skills_of(conn, uid):
    rows = conn.execute("SELECT kind, name, category, level, detail FROM skills WHERE user_id = ? ORDER BY id", (uid,)).fetchall()
    return {
        "teach": [dict(name=r["name"], category=r["category"], level=r["level"], detail=r["detail"]) for r in rows if r["kind"] == "teach"],
        "learn": [r["name"] for r in rows if r["kind"] == "learn"],
    }


def user_public(conn, row):
    return {
        "id": row["id"], "name": row["name"], "city": row["city"], "mode": row["mode"],
        "times": [t for t in row["times"].split(",") if t], "bio": row["bio"],
        "verified": bool(row["verified"]), "rating": round(row["rating"], 1), "swaps_done": row["swaps_done"],
        **skills_of(conn, row["id"]),
    }


def match_profile(conn, row):
    u = user_public(conn, row)
    u["teach"] = [t["name"] for t in u["teach"]]
    return u


def swap_view(conn, swap_id):
    s = conn.execute("SELECT * FROM swaps WHERE id = ?", (swap_id,)).fetchone()
    if not s:
        raise HTTPException(404, "交换不存在")
    out = dict(s)
    out["trial"] = bool(out["trial"])
    out["legs"] = [dict(r) for r in conn.execute("SELECT id, teacher_id, learner_id, skill, done FROM swap_legs WHERE swap_id = ?", (swap_id,))]
    out["parties"] = [dict(user_id=r["user_id"], name=r["name"], accepted=bool(r["accepted"])) for r in conn.execute(
        "SELECT p.user_id, p.accepted, u.name FROM swap_parties p JOIN users u ON u.id = p.user_id WHERE swap_id = ?", (swap_id,))]
    return out


# ---------- 账号 ----------

class GuestIn(BaseModel):
    name: str = Field("我", max_length=12)
    city: str = "上海"
    teach: List[str] = []
    learn: List[str] = []


@app.post("/api/auth/guest", tags=["账号"], summary="游客注册，返回 token，附赠 3 枚时间币")
def guest(body: GuestIn, conn=Depends(get_db)):
    token = secrets.token_urlsafe(24)
    with db.tx(conn):
        uid = conn.execute("INSERT INTO users (token, name, city, times) VALUES (?,?,?,?)",
                           (token, body.name.strip() or "我", body.city, "工作日晚,周末白天")).lastrowid
        db.add_coins(conn, uid, 3, "新人礼")
        _replace_skills(conn, uid, "teach", [SkillIn(name=s) for s in body.teach])
        _replace_skills(conn, uid, "learn", [SkillIn(name=s) for s in body.learn])
    return {"token": token, "user": me_view(conn, uid)}


def me_view(conn, uid):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return {**user_public(conn, row), "coins": row["coins"]}


@app.get("/api/me", tags=["账号"])
def get_me(me=Depends(current_user), conn=Depends(get_db)):
    return me_view(conn, me["id"])


class MePatch(BaseModel):
    name: Optional[str] = Field(None, max_length=12)
    city: Optional[str] = None
    mode: Optional[str] = None
    times: Optional[List[str]] = None
    bio: Optional[str] = Field(None, max_length=200)


@app.patch("/api/me", tags=["账号"])
def patch_me(body: MePatch, me=Depends(current_user), conn=Depends(get_db)):
    data = body.model_dump(exclude_none=True)
    if "times" in data:
        data["times"] = ",".join(data["times"])
    if data:
        with db.tx(conn):
            conn.execute("UPDATE users SET %s WHERE id = ?" % ", ".join(k + " = ?" for k in data), (*data.values(), me["id"]))
    return me_view(conn, me["id"])


class SkillIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=12)
    level: str = ""
    detail: str = ""


class SkillsIn(BaseModel):
    teach: Optional[List[SkillIn]] = None
    learn: Optional[List[SkillIn]] = None


def _replace_skills(conn, uid, kind, items):
    conn.execute("DELETE FROM skills WHERE user_id = ? AND kind = ?", (uid, kind))
    for s in items:
        conn.execute("INSERT OR IGNORE INTO skills (user_id, kind, name, category, level, detail) VALUES (?,?,?,?,?,?)",
                     (uid, kind, s.name.strip(), seed_mod.cat_of(s.name.strip()), s.level, s.detail))


@app.put("/api/me/skills", tags=["账号"], summary="整体替换我的两张清单")
def put_skills(body: SkillsIn, me=Depends(current_user), conn=Depends(get_db)):
    with db.tx(conn):
        if body.teach is not None:
            _replace_skills(conn, me["id"], "teach", body.teach)
        if body.learn is not None:
            _replace_skills(conn, me["id"], "learn", body.learn)
    return me_view(conn, me["id"])


@app.get("/api/ledger", tags=["账号"], summary="时间币账本")
def ledger(me=Depends(current_user), conn=Depends(get_db)):
    rows = conn.execute("SELECT delta, reason, swap_id, created_at FROM ledger WHERE user_id = ? ORDER BY id DESC LIMIT 50", (me["id"],))
    return {"coins": conn.execute("SELECT coins FROM users WHERE id = ?", (me["id"],)).fetchone()[0], "items": [dict(r) for r in rows]}


# ---------- 发现 ----------

@app.get("/api/categories", tags=["发现"])
def categories():
    return seed_mod.CATS


@app.get("/api/skills", tags=["发现"], summary="技能广场")
def list_skills(kind: str = Query("teach", pattern="^(teach|learn)$"), category: Optional[str] = None,
                q: Optional[str] = None, conn=Depends(get_db)):
    sql = ("SELECT s.name, s.category, s.level, s.detail, u.id AS user_id, u.name AS user_name, u.city, u.mode, u.rating, u.swaps_done, u.verified "
           "FROM skills s JOIN users u ON u.id = s.user_id WHERE s.kind = ?")
    args = [kind]
    if category:
        sql += " AND s.category = ?"
        args.append(category)
    if q:
        sql += " AND (s.name LIKE ? OR u.name LIKE ? OR u.city LIKE ? OR s.detail LIKE ?)"
        args += ["%" + q + "%"] * 4
    return [dict(r) for r in conn.execute(sql + " ORDER BY u.rating DESC LIMIT 200", args)]


@app.get("/api/users/{uid}", tags=["发现"], summary="用户主页（含收到的评价）")
def get_user(uid: int, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    reviews = conn.execute("SELECT r.stars, r.text, r.created_at, u.name AS from_name FROM reviews r JOIN users u ON u.id = r.from_id "
                           "WHERE r.to_id = ? ORDER BY r.id DESC LIMIT 20", (uid,))
    return {**user_public(conn, row), "reviews": [dict(r) for r in reviews]}


# ---------- 匹配 ----------

@app.get("/api/matches", tags=["匹配"], summary="四种成交路径：双向互补 / 三角互换 / 时间币约课 / 想向我学的人")
def matches(me=Depends(current_user), conn=Depends(get_db)):
    others = [match_profile(conn, r) for r in conn.execute("SELECT * FROM users WHERE id != ?", (me["id"],))]
    result = matching.match_all(match_profile(conn, me), others)
    users = {o["id"]: o for o in others}
    used = {m["user_id"] for k in ("mutual", "teach_me", "want_me") for m in result[k]} | {x for c in result["chains"] for x in (c["b"], c["c"])}
    return {**result, "users": {i: users[i] for i in used}}


# ---------- 交换契约 ----------

class LegIn(BaseModel):
    teacher_id: int
    learner_id: int
    skill: str


class SwapIn(BaseModel):
    type: str = Field(..., pattern="^(mutual|chain|coin|earn)$")
    legs: List[LegIn] = Field(..., min_length=1, max_length=3)
    total: int = Field(4, ge=1, le=12)
    pace: str = "每周 1 次"
    mode: str = "线上视频"
    trial: bool = True
    message: str = Field("", max_length=300)


LEG_COUNT = {"mutual": 2, "chain": 3, "coin": 1, "earn": 1}


@app.post("/api/swaps", tags=["交换"], summary="发起一份学习契约")
def create_swap(body: SwapIn, me=Depends(current_user), conn=Depends(get_db)):
    if len(body.legs) != LEG_COUNT[body.type]:
        raise HTTPException(422, "%s 需要 %d 条授课关系" % (body.type, LEG_COUNT[body.type]))
    party_ids = {x for l in body.legs for x in (l.teacher_id, l.learner_id)}
    if me["id"] not in party_ids:
        raise HTTPException(422, "发起人必须是契约的一方")
    # 闭环校验：除时间币形态外，每个人都必须既教又学，谁也不白拿
    if body.type in ("mutual", "chain"):
        if {l.teacher_id for l in body.legs} != party_ids or {l.learner_id for l in body.legs} != party_ids:
            raise HTTPException(422, "互换必须闭环：每一方都既教又学")
    for l in body.legs:
        ok = conn.execute("SELECT 1 FROM skills WHERE user_id = ? AND kind = 'teach' AND name = ?", (l.teacher_id, l.skill)).fetchone()
        if not ok:
            raise HTTPException(422, "用户 %d 没有发布「%s」" % (l.teacher_id, l.skill))
    if body.type == "coin" and me["coins"] < 1:
        raise HTTPException(402, "时间币不足，先去教一节吧")

    with db.tx(conn):
        sid = conn.execute("INSERT INTO swaps (type, total, pace, mode, trial, created_by) VALUES (?,?,?,?,?,?)",
                           (body.type, body.total, body.pace, body.mode, int(body.trial), me["id"])).lastrowid
        for l in body.legs:
            conn.execute("INSERT INTO swap_legs (swap_id, teacher_id, learner_id, skill) VALUES (?,?,?,?)",
                         (sid, l.teacher_id, l.learner_id, l.skill))
        for pid in party_ids:
            demo = conn.execute("SELECT is_demo FROM users WHERE id = ?", (pid,)).fetchone()["is_demo"]
            conn.execute("INSERT INTO swap_parties (swap_id, user_id, accepted) VALUES (?,?,?)",
                         (sid, pid, 1 if pid == me["id"] or demo else 0))
            if pid != me["id"]:
                if body.message:
                    conn.execute("INSERT INTO messages (from_id, to_id, text) VALUES (?,?,?)", (me["id"], pid, body.message))
                if demo:  # 示例用户自动确认，保证雏形可以走完整条流程
                    conn.execute("INSERT INTO messages (from_id, to_id, text) VALUES (?,?,?)", (pid, me["id"], "邀请我确认啦！" + DEMO_REPLIES[1]))
        _activate_if_ready(conn, sid)
    return swap_view(conn, sid)


def _activate_if_ready(conn, sid):
    waiting = conn.execute("SELECT COUNT(*) FROM swap_parties WHERE swap_id = ? AND accepted = 0", (sid,)).fetchone()[0]
    if not waiting:
        conn.execute("UPDATE swaps SET status = 'active' WHERE id = ? AND status = 'pending'", (sid,))


def _my_swap(conn, sid, uid):
    if not conn.execute("SELECT 1 FROM swap_parties WHERE swap_id = ? AND user_id = ?", (sid, uid)).fetchone():
        raise HTTPException(404, "交换不存在")
    return conn.execute("SELECT * FROM swaps WHERE id = ?", (sid,)).fetchone()


@app.get("/api/swaps", tags=["交换"])
def list_swaps(me=Depends(current_user), conn=Depends(get_db)):
    ids = [r[0] for r in conn.execute("SELECT swap_id FROM swap_parties WHERE user_id = ? ORDER BY swap_id DESC", (me["id"],))]
    return [swap_view(conn, i) for i in ids]


@app.post("/api/swaps/{sid}/accept", tags=["交换"], summary="确认邀请；所有人确认后契约生效")
def accept_swap(sid: int, me=Depends(current_user), conn=Depends(get_db)):
    _my_swap(conn, sid, me["id"])
    with db.tx(conn):
        conn.execute("UPDATE swap_parties SET accepted = 1 WHERE swap_id = ? AND user_id = ?", (sid, me["id"]))
        _activate_if_ready(conn, sid)
    return swap_view(conn, sid)


@app.post("/api/swaps/{sid}/cancel", tags=["交换"], summary="试课不合适或中途退出")
def cancel_swap(sid: int, me=Depends(current_user), conn=Depends(get_db)):
    s = _my_swap(conn, sid, me["id"])
    if s["status"] not in ("pending", "active"):
        raise HTTPException(409, "当前状态不能取消")
    with db.tx(conn):
        conn.execute("UPDATE swaps SET status = 'cancelled' WHERE id = ?", (sid,))
    return swap_view(conn, sid)


@app.post("/api/swaps/{sid}/legs/{leg_id}/checkin", tags=["交换"], summary="完成一节并打卡；时间币形态在此结算")
def checkin(sid: int, leg_id: int, me=Depends(current_user), conn=Depends(get_db)):
    s = _my_swap(conn, sid, me["id"])
    if s["status"] != "active":
        raise HTTPException(409, "契约未生效或已结束")
    leg = conn.execute("SELECT * FROM swap_legs WHERE id = ? AND swap_id = ?", (leg_id, sid)).fetchone()
    if not leg or me["id"] not in (leg["teacher_id"], leg["learner_id"]):
        raise HTTPException(404, "这节课与你无关")
    if leg["done"] >= s["total"]:
        raise HTTPException(409, "这门课已经上完了")
    with db.tx(conn):
        if s["type"] in ("coin", "earn"):
            bal = conn.execute("SELECT coins FROM users WHERE id = ?", (leg["learner_id"],)).fetchone()[0]
            if bal < 1:
                raise HTTPException(402, "学习方时间币不足")
            db.add_coins(conn, leg["learner_id"], -1, "学习「%s」1 小时" % leg["skill"], sid)
            db.add_coins(conn, leg["teacher_id"], 1, "教授「%s」1 小时" % leg["skill"], sid)
        conn.execute("UPDATE swap_legs SET done = done + 1 WHERE id = ?", (leg_id,))
        left = conn.execute("SELECT COUNT(*) FROM swap_legs WHERE swap_id = ? AND done < ?", (sid, s["total"])).fetchone()[0]
        if not left:
            conn.execute("UPDATE swaps SET status = 'done' WHERE id = ?", (sid,))
            conn.execute("UPDATE users SET swaps_done = swaps_done + 1 WHERE id IN (SELECT user_id FROM swap_parties WHERE swap_id = ?)", (sid,))
    return swap_view(conn, sid)


class ReviewIn(BaseModel):
    to_id: int
    stars: int = Field(..., ge=1, le=5)
    text: str = Field("", max_length=300)


@app.post("/api/swaps/{sid}/review", tags=["交换"], summary="互评；首次评价奖励 1 枚时间币，评分计入对方信用")
def review(sid: int, body: ReviewIn, me=Depends(current_user), conn=Depends(get_db)):
    s = _my_swap(conn, sid, me["id"])
    if s["status"] != "done":
        raise HTTPException(409, "完成全部课程后才能评价")
    if body.to_id == me["id"] or not conn.execute("SELECT 1 FROM swap_parties WHERE swap_id = ? AND user_id = ?", (sid, body.to_id)).fetchone():
        raise HTTPException(422, "只能评价这次交换里的其他人")
    if conn.execute("SELECT 1 FROM reviews WHERE swap_id = ? AND from_id = ? AND to_id = ?", (sid, me["id"], body.to_id)).fetchone():
        raise HTTPException(409, "已经评价过了")
    with db.tx(conn):
        first = not conn.execute("SELECT 1 FROM reviews WHERE swap_id = ? AND from_id = ?", (sid, me["id"])).fetchone()
        conn.execute("INSERT INTO reviews (swap_id, from_id, to_id, stars, text) VALUES (?,?,?,?,?)", (sid, me["id"], body.to_id, body.stars, body.text))
        t = conn.execute("SELECT rating, swaps_done FROM users WHERE id = ?", (body.to_id,)).fetchone()
        n = max(t["swaps_done"], 1)
        conn.execute("UPDATE users SET rating = ? WHERE id = ?", ((t["rating"] * (n - 1) + body.stars) / n, body.to_id))
        if first:
            db.add_coins(conn, me["id"], 1, "完成互评奖励", sid)
    return {"ok": True, "coins": conn.execute("SELECT coins FROM users WHERE id = ?", (me["id"],)).fetchone()[0]}


# ---------- 消息 ----------

@app.get("/api/threads", tags=["消息"], summary="会话列表（最后一条 + 未读数）")
def threads(me=Depends(current_user), conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT CASE WHEN from_id = :me THEN to_id ELSE from_id END AS peer, MAX(id) AS last_id, "
        "SUM(CASE WHEN to_id = :me AND read = 0 THEN 1 ELSE 0 END) AS unread "
        "FROM messages WHERE from_id = :me OR to_id = :me GROUP BY peer ORDER BY last_id DESC", {"me": me["id"]}).fetchall()
    out = []
    for r in rows:
        last = conn.execute("SELECT text, created_at FROM messages WHERE id = ?", (r["last_id"],)).fetchone()
        peer = conn.execute("SELECT id, name FROM users WHERE id = ?", (r["peer"],)).fetchone()
        out.append({"peer": dict(peer), "last": dict(last), "unread": r["unread"]})
    return out


@app.get("/api/threads/{peer}/messages", tags=["消息"], summary="拉取并标记已读")
def get_messages(peer: int, me=Depends(current_user), conn=Depends(get_db)):
    with db.tx(conn):
        conn.execute("UPDATE messages SET read = 1 WHERE from_id = ? AND to_id = ?", (peer, me["id"]))
    rows = conn.execute("SELECT id, from_id, text, system, created_at FROM messages "
                        "WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?) ORDER BY id", (me["id"], peer, peer, me["id"]))
    return [dict(r) for r in rows]


class MsgIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


@app.post("/api/threads/{peer}/messages", tags=["消息"])
def send_message(peer: int, body: MsgIn, me=Depends(current_user), conn=Depends(get_db)):
    target = conn.execute("SELECT is_demo FROM users WHERE id = ?", (peer,)).fetchone()
    if not target or peer == me["id"]:
        raise HTTPException(404, "用户不存在")
    with db.tx(conn):
        conn.execute("INSERT INTO messages (from_id, to_id, text) VALUES (?,?,?)", (me["id"], peer, body.text))
        if target["is_demo"]:
            conn.execute("INSERT INTO messages (from_id, to_id, text) VALUES (?,?,?)", (peer, me["id"], random.choice(DEMO_REPLIES)))
    return get_messages(peer, me, conn)


@app.get("/api/health", tags=["运维"])
def health(conn=Depends(get_db)):
    return {"ok": True, "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]}
