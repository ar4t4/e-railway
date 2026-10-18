"""Windows service launcher with an opt-out sleep-prevention heartbeat."""
import ctypes
import os
import sys
import threading
import time
import uvicorn


def keep_awake():
    # Execution state belongs to this thread. Only prevent system sleep; the
    # display can switch off, and no synthetic keyboard input is generated.
    while True:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        time.sleep(30)


if __name__ == '__main__':
    if os.name == 'nt' and '--allow-sleep' not in sys.argv:
        threading.Thread(target=keep_awake, daemon=True).start()
    uvicorn.run('companion.server:create_app', factory=True, host='127.0.0.1', port=8787,
                access_log=False, workers=1, proxy_headers=False)
