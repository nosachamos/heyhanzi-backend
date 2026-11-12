import hashlib
import hmac
import ipaddress

from app.models.auth import AuthAuditEvent


def keyed_hash(config: dict, purpose: str, value: str) -> str:
    return hmac.new(config["AUTH"]["AuditHashSecret"].encode(),
                    f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def client_ip(request) -> str:
    # Fly's IPv4 proxy reaches the machine over its private 172.16/12 network.
    # Other proxies and direct/local requests cannot nominate a client address.
    import os
    peer = request.client.host if request.client else "unknown"
    try:
        address = ipaddress.ip_address(peer)
        if os.getenv("FLY_MACHINE_ID") and address in ipaddress.ip_network("172.16.0.0/12"):
            values = request.headers.getlist("Fly-Client-IP")
            if len(values) != 1:
                from fastapi import HTTPException
                raise HTTPException(400, detail="Invalid client address")
            address = ipaddress.ip_address(values[0])
        # Group IPv6 clients by /64 so rotating interface addresses cannot evade limits.
        return str(ipaddress.ip_network(f"{address}/{32 if address.version == 4 else 64}", strict=False))
    except ValueError:
        return peer


def record_auth_event(db, request, event: str, user=None):
    db.add(AuthAuditEvent(user_id=user.id if user else None, event_type=event,
                         ip_hash=keyed_hash(request.app.state.config, "ip", client_ip(request))))
