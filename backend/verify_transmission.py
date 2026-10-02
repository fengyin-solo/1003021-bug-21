"""传输设备统一取数验证：逐条核对误码账的五个症状与四条验收口径。

运行：.venv/bin/pip install httpx && .venv/bin/python verify_transmission.py
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
failures = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# 0. 路由顺序：/export、/alarms、/stats、/threshold 不被 /{entry_id} 抢占
r = client.get("/api/transmission/export")
check("导出路由可达（不被 {entry_id} 抢占）", r.status_code == 200, f"status={r.status_code}")
check("导出是文件（attachment）", "attachment" in r.headers.get("content-disposition", ""), r.headers.get("content-disposition", ""))

# 1. 误码率过线不许直接记成正常，先留下误码
r = client.post("/api/transmission", json={"values": {"设备编号": "TRAN-9001", "传输类型": "OTN", "带宽容量": "10GE", "误码率": "5E-4"}})
entry = r.json()["entry"]
check("登记时误码过线 → 判定误码超标", entry["status"] == "误码超标", f"status={entry['status']}")
check("误码率统一格式化留存", entry["误码率"] == "5.0E-4", entry["误码率"])

r = client.post("/api/transmission", json={"values": {"设备编号": "TRAN-9002", "传输类型": "OTN", "带宽容量": "10GE", "误码率": "1E-6"}})
check("登记时误码未过线 → 正常", r.json()["entry"]["status"] == "正常")

# 2. 告警名单条数与设备列表对得上（同一判定口径）
r_all = client.get("/api/transmission", params={"size": 200})
all_items = r_all.json()["items"]
alarm_in_list = [i for i in all_items if i["status"] in ("光口告警", "误码超标")]
r_alarms = client.get("/api/transmission/alarms", params={"size": 200})
check("告警名单条数 == 列表中告警状态条数", r_alarms.json()["total"] == len(alarm_in_list),
      f"alarms={r_alarms.json()['total']} list={len(alarm_in_list)}")
stats = {s["label"]: s["value"] for s in client.get("/api/transmission/stats").json()}
check("统计卡与列表同口径", stats["告警传输"] + stats["误码超标传输"] == len(alarm_in_list)
      and stats["正常传输"] == sum(1 for i in all_items if i["status"] == "正常"), str(stats))

# 3. 修完的设备从告警名单拿掉
target = alarm_in_list[0]
r = client.post(f"/api/transmission/{target['id']}/actions", json={"values": {"action": "安排修复"}})
check("安排修复后判定已修复", r.json()["entry"]["status"] == "已修复")
alarm_ids = {i["id"] for i in client.get("/api/transmission/alarms", params={"size": 200}).json()["items"]}
check("已修复设备不在告警名单", target["id"] not in alarm_ids)

# 4. 光口告警和误码超标不来回切：误码过线的设备记录告警后仍是误码超标
ber_entry = next(i for i in client.get("/api/transmission", params={"size": 200}).json()["items"] if i["status"] == "误码超标")
r = client.post(f"/api/transmission/{ber_entry['id']}/actions", json={"values": {"action": "记录告警"}})
check("误码超标设备记录告警后不降级", r.json()["entry"]["status"] == "误码超标", r.json()["entry"]["status"])
r = client.post(f"/api/transmission/{ber_entry['id']}/actions", json={"values": {"action": "记录误码", "误码率": "abc"}})
check("记录误码缺合法数值被拦下", r.json()["ok"] is False)

# 5. 翻页不丢记录：逐页取完，合并等于全量
seen, page = [], 1
while True:
    r = client.get("/api/transmission", params={"page": page, "size": 2})
    payload = r.json()
    seen.extend(payload["items"])
    if len(seen) >= payload["total"]:
        break
    page += 1
ids = [i["id"] for i in seen]
check("翻页合并 == 全量，不丢不重", len(ids) == len(set(ids)) == r.json()["total"], f"collected={len(ids)} total={r.json()['total']}")

# 6. 导出按当前条件生成一份文件，行数与页面一致
for params in ({"status": "误码超标"}, {"keyword": "TRAN"}, {"alarm_only": "true"}, {}):
    r_list = client.get("/api/transmission", params=params)
    r_csv = client.get("/api/transmission/export", params=params)
    csv_rows = len(r_csv.text.strip().splitlines()) - 1  # 减去表头
    check(f"导出行数 == 页面总数（{params or '无条件'}）", csv_rows == r_list.json()["total"],
          f"csv={csv_rows} total={r_list.json()['total']}")

# 7. 阈值改过之后：存量设备按新阈值重判，历史结论仍照当时那一版
t0 = client.get("/api/transmission/threshold").json()
check("初始阈值第 1 版", t0["version"] == 1 and t0["value"] == 1e-4, str(t0))
# 先在第 1 版阈值下留一条判定：2.0E-6 < 1E-4 → 当时结论是正常
client.post("/api/transmission/1/actions", json={"values": {"action": "记录误码", "误码率": "2E-6"}})
hist_before = client.get("/api/transmission/1/history").json()
r = client.put("/api/transmission/threshold", json={"value": 1e-6})
body = r.json()
check("阈值调整成功且版本 +1", r.status_code == 200 and body["threshold"]["version"] == 2, str(body.get("threshold")))
check("存量设备全部重判", body["rejudged"] == client.get("/api/transmission", params={"size": 1}).json()["total"])
item1 = next(i for i in client.get("/api/transmission", params={"size": 200}).json()["items"] if i["id"] == 1)
check("存量设备按新阈值重判（2.0E-6 ≥ 1E-6 → 误码超标）", item1["status"] == "误码超标", item1["status"])
hist_after = client.get("/api/transmission/1/history").json()
check("历史记录只增不改", len(hist_after) > len(hist_before)
      and all(h in hist_after for h in hist_before), f"before={len(hist_before)} after={len(hist_after)}")
check("历史结论仍照当时那一版",
      hist_before[-1]["conclusion"] == "正常" and hist_before[-1]["threshold_version"] == 1
      and hist_after[-1]["conclusion"] == "误码超标" and hist_after[-1]["threshold_version"] == 2,
      f"v1={hist_before[-1]['conclusion']}@v{hist_before[-1]['threshold_version']} v2={hist_after[-1]['conclusion']}@v{hist_after[-1]['threshold_version']}")
r = client.put("/api/transmission/threshold", json={"value": -1})
check("非法阈值被拒绝", r.status_code == 400)

print()
print("FAILED:", failures if failures else "无")
raise SystemExit(1 if failures else 0)
