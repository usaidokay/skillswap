"""端到端：游客注册 → 匹配 → 发起交换 → 打卡到完成 → 互评 → 时间币结算 → 消息。"""
import os
import sys
import tempfile
import unittest

os.environ["SKILLSWAP_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import app as app_mod  # noqa: E402
import matching  # noqa: E402


class ApiFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cm = TestClient(app_mod.app)
        cls.c = cls._cm.__enter__()
        r = cls.c.post("/api/auth/guest", json={"name": "测试", "teach": ["Python", "英语口语"], "learn": ["吉他", "摄影"]})
        assert r.status_code == 200, r.text
        cls.me = r.json()["user"]
        cls.h = {"Authorization": "Bearer " + r.json()["token"]}

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)

    def test_01_auth_and_profile(self):
        self.assertEqual(self.c.get("/api/me").status_code, 401)
        me = self.c.get("/api/me", headers=self.h).json()
        self.assertEqual(me["coins"], 3)
        self.assertEqual(me["learn"], ["吉他", "摄影"])
        r = self.c.patch("/api/me", headers=self.h, json={"city": "上海", "times": ["工作日晚", "周末白天"]})
        self.assertEqual(r.json()["times"], ["工作日晚", "周末白天"])

    def test_02_discover(self):
        rows = self.c.get("/api/skills", params={"kind": "teach", "q": "吉他"}).json()
        self.assertTrue(rows and all("吉他" in r["name"] + r["detail"] for r in rows))
        self.assertEqual(len(self.c.get("/api/skills", params={"category": "音乐"}).json()), 4)  # 林晚×2、周予、Nana

    def test_03_matches(self):
        m = self.c.get("/api/matches", headers=self.h).json()
        names = lambda k: [m["users"][str(x["user_id"])]["name"] for x in m[k]]
        self.assertEqual(set(names("mutual")), {"林晚", "陈柯"})
        self.assertIn("周予", names("teach_me"))
        self.assertIn("Kiki", names("want_me"))
        self.assertTrue(m["chains"], "应能组出 我→Mia→周予→我 的三角")
        self.assertTrue(all(x["reasons"] for x in m["mutual"]))

    def test_04_mutual_swap_to_done_and_review(self):
        m = self.c.get("/api/matches", headers=self.h).json()
        top = m["mutual"][0]
        me, ta = self.me["id"], top["user_id"]
        body = {"type": "mutual", "total": 2, "message": "你好～",
                "legs": [{"teacher_id": me, "learner_id": ta, "skill": top["give"][0]},
                         {"teacher_id": ta, "learner_id": me, "skill": top["get"][0]}]}
        s = self.c.post("/api/swaps", headers=self.h, json=body)
        self.assertEqual(s.status_code, 200, s.text)
        s = s.json()
        self.assertEqual(s["status"], "active")  # 示例用户自动确认
        for leg in s["legs"]:
            for _ in range(2):
                s2 = self.c.post("/api/swaps/%d/legs/%d/checkin" % (s["id"], leg["id"]), headers=self.h).json()
        self.assertEqual(s2["status"], "done")
        self.assertEqual(self.c.post("/api/swaps/%d/legs/%d/checkin" % (s["id"], s["legs"][0]["id"]), headers=self.h).status_code, 409)
        r = self.c.post("/api/swaps/%d/review" % s["id"], headers=self.h, json={"to_id": ta, "stars": 5, "text": "很耐心"})
        self.assertEqual(r.json()["coins"], 4)  # 3 + 互评奖励
        self.assertEqual(self.c.post("/api/swaps/%d/review" % s["id"], headers=self.h, json={"to_id": ta, "stars": 5}).status_code, 409)
        self.assertEqual(self.c.get("/api/users/%d" % ta).json()["reviews"][0]["text"], "很耐心")

    def test_05_open_loop_rejected(self):
        me = self.me["id"]
        bad = {"type": "mutual", "legs": [{"teacher_id": me, "learner_id": 1, "skill": "Python"},
                                          {"teacher_id": me, "learner_id": 1, "skill": "英语口语"}]}
        self.assertEqual(self.c.post("/api/swaps", headers=self.h, json=bad).status_code, 422)

    def test_06_coin_swap_settles(self):
        m = self.c.get("/api/matches", headers=self.h).json()
        t = m["teach_me"][0]
        before = self.c.get("/api/me", headers=self.h).json()["coins"]
        s = self.c.post("/api/swaps", headers=self.h, json={"type": "coin", "total": 2, "legs": [
            {"teacher_id": t["user_id"], "learner_id": self.me["id"], "skill": t["get"][0]}]}).json()
        self.c.post("/api/swaps/%d/legs/%d/checkin" % (s["id"], s["legs"][0]["id"]), headers=self.h)
        led = self.c.get("/api/ledger", headers=self.h).json()
        self.assertEqual(led["coins"], before - 1)
        self.assertEqual(led["items"][0]["delta"], -1)

    def test_07_messages(self):
        msgs = self.c.post("/api/threads/4/messages", headers=self.h, json={"text": "在吗"}).json()
        self.assertEqual([x["from_id"] for x in msgs[-2:]], [self.me["id"], 4])
        th = self.c.get("/api/threads", headers=self.h).json()
        self.assertIn(4, [t["peer"]["id"] for t in th])


class MatchingUnit(unittest.TestCase):
    def test_score_is_explainable_and_bounded(self):
        me = {"id": 0, "city": "上海", "mode": "均可", "times": ["周末白天"], "teach": ["A"], "learn": ["B"]}
        o = {"id": 1, "city": "北京", "mode": "线下", "times": [], "rating": 4.4, "teach": ["B"], "learn": ["A"]}
        r = matching.match_all(me, [o])
        self.assertEqual(len(r["mutual"]), 1)
        self.assertFalse(r["mutual"][0]["reachable"])
        self.assertLessEqual(r["mutual"][0]["score"], 99)


if __name__ == "__main__":
    unittest.main()
