"""
Run discovery cycles for the shared queue.

Start this on another computer with the same DATABASE_URL to raise
how many people can run a cycle at the same time.
"""

import time

from discovery_worker import start_worker_pool

if __name__ == "__main__":
    start_worker_pool()
    print("Discovery worker is watching the queue.")
    while True:
        time.sleep(3600)
