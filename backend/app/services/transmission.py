"""传输设备业务规则：状态判定、阈值版本与筛选口径都收在这里。

全模块只有 judge() 一处判定口径：列表、告警名单、统计、导出都从它取数，
任何写操作（登记、记录误码、记录告警、安排修复、调整阈值）都先改数据再重判，
并把判定结论连同当时的阈值版本追加进判定历史——历史只增不改。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.store import store

MODULE = "transmission"
REQUIRED_FIELDS = ["设备编号", "传输类型", "带宽容量"]
OPTIONAL_FIELDS = ["所属站点", "光口状态", "电口状态", "误码率", "设备状态"]
STATUS_ORDER = ["正常", "光口告警", "误码超标", "已修复"]
ALARM_STATUSES = ["光口告警", "误码超标"]
ACTIONS = ["记录告警", "记录误码", "安排修复"]

# 误码率阈值：默认 1E-4，每次调整版本 +1，存量设备按新版本重判。
DEFAULT_BER_THRESHOLD = 1e-4


def parse_ber(value: Any) -> float | None:
    """把误码率解析成浮点数；接受 3E-4、0.0003 这类写法，解析不了返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def format_ber(value: float) -> str:
    """误码率统一落成科学计数字符串（如 5.0E-4），避免同一数值多种写法。"""
    mantissa, exponent = f"{value:.1E}".split("E")
    return f"{mantissa}E{int(exponent)}"


def judge(entry: dict[str, Any], threshold: float) -> str:
    """唯一判定口径：已修复 > 误码超标 > 光口告警 > 正常。

    误码率过线优先于光口告警，避免两个状态之间来回切。
    """
    if entry.get("repaired"):
        return "已修复"
    ber = parse_ber(entry.get("误码率"))
    if ber is not None and ber >= threshold:
        return "误码超标"
    if str(entry.get("光口状态") or "").strip() == "告警":
        return "光口告警"
    return "正常"


class TransmissionService:
    # ---- 阈值：带版本，改一次版本 +1 ----

    def current_threshold(self) -> dict[str, Any]:
        config = store.config(MODULE)
        if "current" not in config:
            config["current"] = {"version": 1, "value": DEFAULT_BER_THRESHOLD}
            config.setdefault("versions", []).append(dict(config["current"]))
        return config["current"]

    def update_threshold(self, value: float) -> tuple[dict[str, Any], int]:
        """调整阈值：版本 +1，存量设备全部按新阈值重判，历史仍保留当时那一版。"""
        if value <= 0:
            raise ValueError("误码率阈值必须大于 0")
        config = store.config(MODULE)
        current = self.current_threshold()
        current = {"version": int(current["version"]) + 1, "value": value}
        config["current"] = current
        config.setdefault("versions", []).append(dict(current))
        judged = 0
        for row in store.rows(MODULE):
            self._apply_judgment(row, reason="阈值调整")
            judged += 1
        return current, judged

    # ---- 查询：列表、告警名单、统计、导出共用同一判定 ----

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        alarm_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._view(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设备编号", ""))]
        if alarm_only:
            rows = [row for row in rows if row["status"] in ALARM_STATUSES]
        if status:
            rows = [row for row in rows if row["status"] == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._view(row) if row is not None else None

    def stats(self) -> list[dict[str, Any]]:
        """统计卡与列表同口径：同一批行、同一个 judge()，条数自然对得上。"""
        rows = [self._view(row) for row in store.rows(MODULE)]
        return [
            {"label": "正常传输", "value": sum(1 for row in rows if row["status"] == "正常")},
            {"label": "告警传输", "value": sum(1 for row in rows if row["status"] == "光口告警")},
            {"label": "误码超标传输", "value": sum(1 for row in rows if row["status"] == "误码超标")},
        ]

    def history_of(self, entry_id: int) -> list[dict[str, Any]] | None:
        if store.find(MODULE, entry_id) is None:
            return None
        return [item for item in store.history(MODULE) if int(item.get("entry_id", 0)) == entry_id]

    # ---- 写入：先改数据，再统一重判 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip() != "":
                entry[field] = value
        ber = parse_ber(entry.get("误码率"))
        if ber is not None:
            entry["误码率"] = format_ber(ber)
        entry["repaired"] = False
        rows.append(entry)
        # 误码率过线不许直接记成正常：登记时即按当前阈值判定，先留下误码。
        self._apply_judgment(entry, reason="登记")
        return self._view(entry), []

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"传输设备 {entry_id} 不存在或已归档"
        if action not in ACTIONS:
            return None, f"动作「{action}」不属于传输设备可执行范围"
        values = values or {}
        if action == "记录误码":
            ber = parse_ber(values.get("误码率"))
            if ber is None:
                return None, "记录误码需要给出可解析的误码率（如 3E-4）"
            entry["误码率"] = format_ber(ber)
            entry["repaired"] = False
        elif action == "记录告警":
            entry["光口状态"] = "告警"
            entry["repaired"] = False
        elif action == "安排修复":
            # 修完的设备从告警名单拿掉：判定口径里 repaired 优先于一切告警。
            entry["repaired"] = True
        self._apply_judgment(entry, reason=action)
        return self._view(entry), f"传输设备已{action}，当前判定：{entry['status']}"

    # ---- 内部：判定、标志同步、历史留痕 ----

    def _apply_judgment(self, entry: dict[str, Any], *, reason: str) -> None:
        threshold = self.current_threshold()
        entry["status"] = judge(entry, float(threshold["value"]))
        entry["pending"] = entry["status"] != "已修复"
        entry["abnormal"] = entry["status"] in ALARM_STATUSES
        store.history(MODULE).append({
            "entry_id": int(entry.get("id", 0)),
            "设备编号": entry.get("设备编号"),
            "conclusion": entry["status"],
            "误码率": entry.get("误码率"),
            "threshold": threshold["value"],
            "threshold_version": threshold["version"],
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        """读取视图：按当前阈值重算状态并同步标志，保证任何入口看到的口径一致。"""
        threshold = self.current_threshold()
        row["status"] = judge(row, float(threshold["value"]))
        row["pending"] = row["status"] != "已修复"
        row["abnormal"] = row["status"] in ALARM_STATUSES
        return dict(row)
