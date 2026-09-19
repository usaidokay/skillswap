"""匹配引擎：纯函数，不碰数据库，方便单测和以后换算法。

输入是普通 dict：{"id", "city", "mode", "times": [..], "rating", "teach": [..], "learn": [..]}
输出的每个匹配都带 reasons，前端直接展示——推荐必须说得出理由。
"""
from typing import Dict, List


def relate(me: Dict, other: Dict) -> Dict:
    return {
        "get": [s for s in other["teach"] if s in me["learn"]],    # TA 能教我的
        "give": [s for s in other["learn"] if s in me["teach"]],   # 我能教 TA 的
    }


def reachable(me: Dict, other: Dict) -> bool:
    return other["mode"] != "线下" or other["city"] == me["city"]


def score(me: Dict, other: Dict, get: List[str], give: List[str]) -> Dict:
    reasons = []
    if get and give:
        pts = 72 + 6 * (len(get) + len(give) - 2)
        reasons.append("技能双向互补")
    else:
        pts = 40 + 5 * (len(get) + len(give))
        reasons.append("TA 能教你想学的" if get else "TA 想学你会的")
    if reachable(me, other):
        pts += 6
        reasons.append("同城可线下" if other["city"] == me["city"] and other["mode"] != "线上" else "可线上授课")
    overlap = [t for t in other["times"] if t in me["times"]]
    if overlap:
        pts += 3 * len(overlap)
        reasons.append("共同空闲：" + "、".join(overlap))
    pts += round((other.get("rating", 4.4) - 4.4) * 10)
    return {"score": max(0, min(99, pts)), "reasons": reasons, "overlap": overlap}


def match_all(me: Dict, others: List[Dict]) -> Dict:
    mutual, teach_me, want_me = [], [], []
    for o in others:
        if o["id"] == me["id"]:
            continue
        r = relate(me, o)
        if not r["get"] and not r["give"]:
            continue
        m = {"user_id": o["id"], **r, **score(me, o, r["get"], r["give"]), "reachable": reachable(me, o)}
        (mutual if r["get"] and r["give"] else teach_me if r["get"] else want_me).append(m)

    # 三角互换：我教 B，B 教 C，C 教我。解决物物交换的「需求双重巧合」。
    by_id = {o["id"]: o for o in others}
    chains = []
    for b in want_me:
        for c in teach_me:
            if b["user_id"] == c["user_id"]:
                continue
            ub, uc = by_id[b["user_id"]], by_id[c["user_id"]]
            mid = [s for s in ub["teach"] if s in uc["learn"]]
            if mid:
                chains.append({
                    "b": ub["id"], "c": uc["id"],
                    "give": b["give"][0], "mid": mid[0], "get": c["get"][0],
                    "score": min(95, round((b["score"] + c["score"]) / 2) + 22),
                })

    key = lambda m: -m["score"]
    return {
        "mutual": sorted(mutual, key=key),
        "chains": sorted(chains, key=key),
        "teach_me": sorted(teach_me, key=key),
        "want_me": sorted(want_me, key=key),
    }
