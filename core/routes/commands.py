# core/routes/commands.py - the composer's door to slash commands (core/commands.py)

import asyncio

from fastapi import APIRouter, Depends, Request

from core import commands
from core.api_fastapi import get_system
from core.auth import require_login
from core.routes.chat import _edit_target

router = APIRouter()


@router.get("/api/commands")
async def list_commands(_=Depends(require_login)):
    """What the composer's menu offers."""
    return {"commands": commands.all_commands()}


@router.post("/api/commands/run")
async def run_command(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Run one typed command on the viewed chat (`?chat=`, else the active one)."""
    line = ((await request.json()) or {}).get('line', '')
    target = _edit_target(request, system)
    return await asyncio.to_thread(commands.run, str(line), target, 'user')
