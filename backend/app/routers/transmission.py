"""传输设备接口：维护传输设备，覆盖记录告警、记录误码、安排修复等动作。

注意路由顺序：/alarms、/stats、/threshold、/export 必须声明在 /{entry_id} 之前，
否则会被 {entry_id} 抢占，导出等接口实际走不到。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import ActionResult, EntryPayload, PageResult, ThresholdPayload
from app.services.transmission import STATUS_ORDER, TransmissionService, format_ber

router = APIRouter(prefix="/api/transmission", tags=["传输设备"])

service = TransmissionService()

LIST_FIELDS = ["设备编号", "传输类型", "带宽容量", "所属站点", "光口状态", "电口状态", "误码率", "设备状态"]
EXPORT_FIELDS = LIST_FIELDS + ["判定状态"]
STATUSES = STATUS_ORDER


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索"),
    status: str | None = Query(default=None, description="正常、光口告警、误码超标、已修复"),
    alarm_only: bool = Query(default=False, description="只看告警名单（光口告警、误码超标）"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设备编号与状态过滤传输设备列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, alarm_only=alarm_only, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/alarms", response_model=PageResult[dict])
def list_alarms(
    keyword: str | None = Query(default=None, description="按设备编号检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """告警名单：与设备列表同一判定口径，已修复的设备不出现在这里。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, alarm_only=True, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def stats() -> list[dict[str, Any]]:
    """统计卡：与列表同一份数据、同一个判定，条数互相对得上。"""
    return service.stats()


@router.get("/threshold")
def get_threshold() -> dict[str, Any]:
    """当前误码率阈值及版本号。"""
    return service.current_threshold()


@router.put("/threshold")
def update_threshold(payload: ThresholdPayload) -> dict[str, Any]:
    """调整误码率阈值：版本 +1，存量设备按新阈值重判，历史结论仍照当时那一版。"""
    try:
        current, rejudged = service.update_threshold(payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "threshold": current,
        "rejudged": rejudged,
        "message": f"阈值已调整为 {format_ber(current['value'])}（第 {current['version']} 版），已按新阈值重判 {rejudged} 台存量设备",
    }


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索"),
    status: str | None = Query(default=None, description="正常、光口告警、误码超标、已修复"),
    alarm_only: bool = Query(default=False, description="只导出告警名单"),
) -> StreamingResponse:
    """导出传输设备清单：按当前过滤条件生成一份 CSV 文件，行数与页面上的总数一致。"""
    items, total = service.list_entries(keyword=keyword, status=status, alarm_only=alarm_only, page=1, size=10000)
    buffer = io.StringIO()
    buffer.write("﻿")  # BOM，保证 Excel 打开中文不乱码
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_FIELDS)
    for item in items:
        writer.writerow([item.get("status", "") if field == "判定状态" else item.get(field, "") for field in EXPORT_FIELDS])
    filename = f"transmission-{datetime.now():%Y%m%d-%H%M%S}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Total-Count": str(total),
        },
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条传输设备明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"传输设备 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/history")
def get_history(entry_id: int) -> list[dict[str, Any]]:
    """判定历史：每次判定记录当时的阈值版本与结论，历史结论不回改。"""
    history = service.history_of(entry_id)
    if history is None:
        raise HTTPException(status_code=404, detail=f"传输设备 {entry_id} 不存在或已归档")
    return history


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条传输设备，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="传输设备已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条传输设备执行记录告警、记录误码、安排修复；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
