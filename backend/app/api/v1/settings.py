"""Editable business / AI settings (each key validated by its own schema)."""
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select

from app.api.deps import AdminOnly, AnyRole, SessionDep
from app.models import AppSetting
from app.schemas.business import SETTINGS_SCHEMAS, SettingOut
from app.services.audit import record_audit

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=list[SettingOut])
async def list_settings(_: AnyRole, session: SessionDep):
    return (await session.scalars(select(AppSetting).order_by(AppSetting.key))).all()


@router.get("/{key}", response_model=SettingOut)
async def get_setting(key: str, _: AnyRole, session: SessionDep):
    setting = await session.get(AppSetting, key)
    if setting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown setting")
    return setting


@router.put("/{key}", response_model=SettingOut)
async def update_setting(key: str, value: dict[str, Any], admin: AdminOnly, session: SessionDep,
                         request: Request):
    schema = SETTINGS_SCHEMAS.get(key)
    if schema is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown setting. Allowed: {sorted(SETTINGS_SCHEMAS)}")
    try:
        clean = schema.model_validate(value).model_dump(mode="json")   # reject bad values
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            exc.errors(include_url=False, include_context=False)) from None

    setting = await session.get(AppSetting, key)
    old = setting.value if setting else None
    if setting is None:
        setting = AppSetting(key=key, value=clean)
        session.add(setting)
    setting.value = clean
    setting.updated_by_agent_id = admin.id
    await record_audit(session, action="settings.updated", entity_type="app_setting", entity_id=key,
                       actor=admin, request=request, changes={"before": old, "after": clean})
    await session.commit()
    await session.refresh(setting)
    return setting
