"""传输设备业务规则：状态判定、阈值版本与筛选口径都收在这里。

列表、统计、告警名单、导出全部从同一条 query() 取数，页面上看到的条数、
告警名单的条数、导出文件的行数自然是同一本账；状态不再由动作随手覆盖，
统一由 judge 口径判定：误码率过线先记误码超标，光口告警次之，已修复为终态。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "transmission"
REQUIRED_FIELDS = ["设备编号", "传输类型", "带宽容量"]
FIELD_FILTERS = ["传输类型", "带宽容量"]

STATUS_NORMAL = "正常"
STATUS_OPTICAL_ALARM = "光口告警"
STATUS_BER_EXCEEDED = "误码超标"
STATUS_REPAIRED = "已修复"
STATUS_ORDER = [STATUS_NORMAL, STATUS_OPTICAL_ALARM, STATUS_BER_EXCEEDED, STATUS_REPAIRED]
ALERT_STATUSES = [STATUS_OPTICAL_ALARM, STATUS_BER_EXCEEDED]

ACTIONS = ["记录告警", "记录误码", "安排修复"]

DEFAULT_BER_THRESHOLD = 1e-6


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_ber(value: Any) -> float | None:
    """把误码率入参解析成数值；空值或非数值返回 None，由调用方决定怎么提示。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class TransmissionService:
    """传输设备的统一口径：取数、判定、阈值版本。"""

    def __init__(self) -> None:
        self._threshold_versions: list[dict[str, Any]] = [
            {"版本": 1, "误码率阈值": DEFAULT_BER_THRESHOLD, "生效时间": "2026-09-01T00:00:00"}
        ]

    # ---------- 统一取数 ----------

    def query(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        field_filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """唯一的取数入口：列表、统计、告警名单、导出都用它，口径不会再分叉。"""
        rows = list(store.rows(MODULE))
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设备编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        for field, want in (field_filters or {}).items():
            if field in FIELD_FILTERS and want:
                rows = [row for row in rows if want in str(row.get(field, ""))]
        return rows

    def list_entries(self, *, page: int = 1, size: int = 20, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        rows = self.query(**filters)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def summary(self, **filters: Any) -> dict[str, Any]:
        """统计与列表同条件取数，告警合计就是告警名单的条数。"""
        rows = self.query(**filters)
        by_status = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            name = str(row.get("status", STATUS_NORMAL))
            by_status[name] = by_status.get(name, 0) + 1
        return {
            "total": len(rows),
            "by_status": by_status,
            "alerts": sum(by_status[status] for status in ALERT_STATUSES),
            "threshold": self.current_threshold(),
        }

    def list_alerts(self, **filters: Any) -> list[dict[str, Any]]:
        """告警名单：同一口径下处于告警状态的设备；已修复的不再出现。"""
        return [row for row in self.query(**filters) if row.get("status") in ALERT_STATUSES]

    def export_rows(self, **filters: Any) -> list[dict[str, Any]]:
        """导出与页面同条件、不分页，行数与页面总数一致。"""
        return self.query(**filters)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ---------- 统一判定 ----------

    def _judge(self, entry: dict[str, Any]) -> str:
        """判定口径：误码率过线一律误码超标，不许直接记正常；光口告警次之。"""
        ber = _parse_ber(entry.get("误码率"))
        threshold = float(self.current_threshold()["误码率阈值"])
        if ber is not None and ber >= threshold:
            return STATUS_BER_EXCEEDED
        if str(entry.get("光口状态", "")).strip() == "告警":
            return STATUS_OPTICAL_ALARM
        return STATUS_NORMAL

    def _apply_judgment(self, entry: dict[str, Any], *, reason: str) -> None:
        """按当前阈值重判一台设备并留下判定历史；历史结论永远保留当时那一版。"""
        version = self.current_threshold()
        status = self._judge(entry)
        entry["status"] = status
        entry["设备状态"] = status
        entry["pending"] = True
        entry["abnormal"] = status in ALERT_STATUSES
        entry.setdefault("判定历史", []).append({
            "版本": version["版本"],
            "阈值": version["误码率阈值"],
            "误码率": _parse_ber(entry.get("误码率")),
            "结论": status,
            "事由": reason,
            "时间": _now(),
        })

    # ---------- 阈值版本 ----------

    def current_threshold(self) -> dict[str, Any]:
        return self._threshold_versions[-1]

    def threshold_versions(self) -> list[dict[str, Any]]:
        return list(self._threshold_versions)

    def update_threshold(self, value: float) -> tuple[dict[str, Any], int, int]:
        """登记新版阈值，存量设备按新阈值重判（已修复是人工闭环的终态，不再翻动）。

        返回 (新版本, 重判台数, 状态变化台数)；每台设备的判定历史追加新记录，
        历史结论仍照当时那一版保留，不回改。
        """
        version = {"版本": len(self._threshold_versions) + 1, "误码率阈值": value, "生效时间": _now()}
        self._threshold_versions.append(version)
        judged = 0
        changed = 0
        for entry in store.rows(MODULE):
            if entry.get("status") == STATUS_REPAIRED:
                continue
            judged += 1
            before = entry.get("status")
            self._apply_judgment(entry, reason="阈值重判")
            if entry.get("status") != before:
                changed += 1
        return version, judged, changed

    # ---------- 登记与动作 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ["设备编号", "传输类型", "带宽容量", "所属站点", "电口状态"]:
            entry[field] = values.get(field) or ""
        entry["光口状态"] = values.get("光口状态") or "正常"
        entry["误码率"] = _parse_ber(values.get("误码率"))
        # 登记即判定：误码率过线直接记误码超标，不会落成正常
        self._apply_judgment(entry, reason="登记")
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"传输设备 {entry_id} 不存在或已归档"
        if action not in ACTIONS:
            return None, f"动作「{action}」不属于传输设备可执行范围"
        if action == "记录误码":
            ber = _parse_ber(values.get("误码率"))
            if ber is None:
                return None, "记录误码需要提交数值形式的误码率，例如 0.000008 或 8e-6"
            entry["误码率"] = ber
            self._apply_judgment(entry, reason="记录误码")
            threshold = float(self.current_threshold()["误码率阈值"])
            if entry["status"] == STATUS_BER_EXCEEDED:
                return entry, f"误码率 {ber:g} 达到阈值 {threshold:g}，已记为误码超标"
            return entry, f"误码率 {ber:g} 未过阈值 {threshold:g}，当前状态{entry['status']}"
        if action == "记录告警":
            entry["光口状态"] = "告警"
            self._apply_judgment(entry, reason="记录告警")
            if entry["status"] == STATUS_BER_EXCEEDED:
                return entry, "光口告警已登记；误码率仍超阈值，按口径保持误码超标"
            return entry, "传输设备已记录告警"
        # 安排修复：人工闭环，从告警名单移除
        if entry.get("status") == STATUS_REPAIRED:
            return None, "设备已修复，无需重复安排"
        entry["status"] = STATUS_REPAIRED
        entry["设备状态"] = STATUS_REPAIRED
        entry["光口状态"] = "正常"
        entry["pending"] = False
        entry["abnormal"] = False
        return entry, "传输设备已安排修复，从告警名单移除"
