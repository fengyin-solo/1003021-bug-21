"""传输设备接口：列表、统计、告警名单、导出共用一条取数口径；误码率阈值按版本管理。

注意路由顺序：/summary、/alerts、/threshold、/export 这些静态路径必须声明在
/{entry_id} 之前，否则 "export" 会被当成设备编号匹配，导出永远走不到。
"""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.transmission import FIELD_FILTERS, TransmissionService

router = APIRouter(prefix="/api/transmission", tags=["传输设备"])

service = TransmissionService()

LIST_FIELDS = ["设备编号", "传输类型", "带宽容量", "所属站点", "光口状态", "电口状态", "误码率", "设备状态"]


def _filters(request: Request) -> dict[str, Any]:
    """列表、统计、告警名单、导出共用同一组过滤条件，各处不再各取各的数。"""
    params = request.query_params
    return {
        "keyword": params.get("keyword") or None,
        "status": params.get("status") or None,
        "field_filters": {field: params[field] for field in FIELD_FILTERS if params.get(field)},
    }


@router.get("", response_model=PageResult[dict])
def list_entries(request: Request, page: int = 1, size: int = 20) -> PageResult[dict]:
    """按设备编号、状态与字段条件过滤传输设备列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(page=page, size=size, **_filters(request))
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/summary")
def summary(request: Request) -> dict[str, Any]:
    """统计卡片：与列表同条件取数，告警合计与告警名单条数一致。"""
    return service.summary(**_filters(request))


@router.get("/alerts")
def list_alerts(request: Request) -> dict[str, Any]:
    """告警名单：同一口径下处于光口告警、误码超标的设备；已修复的已移除。"""
    rows = service.list_alerts(**_filters(request))
    return {"total": len(rows), "items": rows}


@router.get("/threshold")
def get_threshold() -> dict[str, Any]:
    """当前误码率阈值与全部历史版本。"""
    return {"current": service.current_threshold(), "versions": service.threshold_versions()}


@router.put("/threshold", response_model=ActionResult)
def update_threshold(payload: EntryPayload) -> ActionResult:
    """更新阈值：生成新版本，存量设备按新阈值重判，历史结论仍照当时那一版。"""
    raw = payload.values.get("误码率阈值")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="阈值需要是数值，例如 0.000001 或 1e-6")
    if value <= 0:
        return ActionResult(ok=False, message="阈值必须大于 0")
    version, judged, changed = service.update_threshold(value)
    return ActionResult(
        ok=True,
        message=f"阈值已更新为 {value:g}（第 {version['版本']} 版），按新阈值重判 {judged} 台，其中 {changed} 台状态变化；历史结论仍按当时版本保留",
        entry=version,
    )


@router.get("/export")
def export_entries(request: Request) -> Response:
    """导出传输设备清单：按当前过滤条件生成一份 CSV 文件，行数与页面总数一致。"""
    rows = service.export_rows(**_filters(request))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(LIST_FIELDS)
    for row in rows:
        writer.writerow([row.get(field, "") for field in LIST_FIELDS])
    filename = f"transmission-{date.today().isoformat()}.csv"
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条传输设备明细（含判定历史）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"传输设备 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条传输设备：缺字段说明原因；误码率过线直接记误码超标，不落正常。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"传输设备已登记，当前状态{entry['status']}", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条传输设备执行记录告警、记录误码、安排修复；状态由统一判定口径得出。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
