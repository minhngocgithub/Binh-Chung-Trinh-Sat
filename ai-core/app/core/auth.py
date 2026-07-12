from fastapi import Request, Security, HTTPException, status
from fastapi.security import APIKeyHeader
from app.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def _get_real_ip(request: Request) -> str:
    # Ưu tiên X-Forwarded-For nếu có reverse proxy (nginx)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host

async def verify_request(
    request: Request,
    api_key: str = Security(_api_key_header),
) -> None:
    # Layer 1: IP Whitelist
    if settings.allowed_ips:
        client_ip = _get_real_ip(request)
        allowed = {ip.strip() for ip in settings.allowed_ips.split(",")}
        if client_ip not in allowed:
            raise HTTPException(status_code=403, detail="Access denied.")

    # Layer 2: API Key
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="API_KEY chưa cấu hình.")
    if api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Access denied.")