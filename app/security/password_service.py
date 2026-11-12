import asyncio

import anyio
from fastapi import HTTPException

from app.security.passwords import hash_password, verify_password

# A real Argon2id hash with the production work factor, used for absent credentials.
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$b6a9GWNfiPTQmtTFX8/odg$W4haqnN83/S4AtKOSxk9gYB97Uzwf9DoXZPNbdq++Nk"


class PasswordService:
    def __init__(self):
        self.capacity = asyncio.Semaphore(1)

    async def run(self, operation):
        try:
            await asyncio.wait_for(self.capacity.acquire(), timeout=2)
        except TimeoutError:
            raise HTTPException(429, detail={"code": "auth_busy", "message": "Try again shortly."},
                                headers={"Retry-After": "2"}) from None
        try:
            # Do not abandon the thread on cancellation and exceed the memory budget.
            return await anyio.to_thread.run_sync(operation)
        finally:
            self.capacity.release()

    async def hash(self, password: str) -> str:
        return await self.run(lambda: hash_password(password, config={}).encoded_hash)

    async def verify(self, password: str, encoded_hash: str | None) -> bool:
        return await self.run(lambda: verify_password(password, encoded_hash or DUMMY_HASH))
