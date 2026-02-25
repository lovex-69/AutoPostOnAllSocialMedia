import os
import sys
import time

import requests


def main() -> int:
    base_url = (os.getenv("KEEP_ALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if not base_url:
        print("KEEP_ALIVE_URL/RENDER_EXTERNAL_URL is not set")
        return 1

    health_url = f"{base_url}/health"
    attempts = 3

    for idx in range(1, attempts + 1):
        try:
            response = requests.get(health_url, timeout=25)
            print(f"[{idx}/{attempts}] {health_url} -> {response.status_code}")
            if response.ok:
                return 0
        except requests.RequestException as exc:
            print(f"[{idx}/{attempts}] request error: {exc}")

        if idx < attempts:
            time.sleep(5)

    return 1


if __name__ == "__main__":
    sys.exit(main())
