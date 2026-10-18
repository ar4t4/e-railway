import os
import uvicorn

if __name__ == "__main__":
    # A single process owns the scheduler. SQLite transactions serialize API claims.
    uvicorn.run("companion.server:create_app", factory=True, host=os.getenv("RC_HOST", "127.0.0.1"),
                port=int(os.getenv("RC_PORT", "8787")), workers=1, access_log=False,
                proxy_headers=True, forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"))
