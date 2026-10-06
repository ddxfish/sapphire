# core/routes/mcp.py - the MCP door (core/mcp_server.py has the protocol)
#
# POST /mcp is the Model Context Protocol over Streamable HTTP, tools only,
# JSON answers, no session. A client authenticates with one of her API
# tokens (Settings > System > API Keys) as a bearer; the browser session
# works too. A token that speaks as a persona gets that persona's voice and
# memory as well. The door is shut until Settings > MCP Server turns it on.
# GET /api/mcp/tools is for that settings page: every tool, with its state.
import asyncio
import logging

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from core.auth import require_login, get_client_ip

logger = logging.getLogger(__name__)
router = APIRouter()


def get_system():
    from core.api_fastapi import get_system as real      # at request time: api_fastapi imports this file
    return real()


def _server():
    from core import mcp_server
    return mcp_server


def _persona(request):
    """The persona the caller's API token speaks as, or None: a plain token,
    or the browser's own session."""
    auth = request.headers.get('Authorization') or ''
    if not auth.startswith('Bearer '):
        return None
    from core.api_tokens import api_tokens
    return api_tokens.persona_of(auth[len('Bearer '):].strip())


@router.post("/mcp")
async def mcp_post(request: Request, _=Depends(require_login), system=Depends(get_system)):
    srv = _server()
    if not srv.enabled():
        raise HTTPException(status_code=404, detail="The MCP server is off. Turn it on under Settings > MCP Server.")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={'jsonrpc': '2.0', 'id': None,
                                                      'error': {'code': -32700, 'message': 'The body is not JSON.'}})
    where = get_client_ip(request)
    answer = await asyncio.to_thread(srv.handle_body, system, body, where, _persona(request))
    if answer is None:
        return Response(status_code=202)              # a notification: taken, nothing to say
    return JSONResponse(content=answer, headers={'MCP-Protocol-Version': srv.PROTOCOL})


@router.get("/mcp")
async def mcp_get(_=Depends(require_login)):
    # No server-initiated stream: this door only answers.
    raise HTTPException(status_code=405, detail="This MCP server answers POST only; it opens no stream of its own.")


@router.delete("/mcp")
async def mcp_delete(_=Depends(require_login)):
    return Response(status_code=200)                  # there is no session to end


@router.get("/api/mcp/tools")
async def mcp_tools(_=Depends(require_login), system=Depends(get_system)):
    srv = _server()
    return {'enabled': srv.enabled(), 'tools': await asyncio.to_thread(srv.catalogue, system)}
