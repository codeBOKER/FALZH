import asyncio
import os

import httpx

BASE_URL = os.getenv("FALZH_BASE_URL", "http://127.0.0.1:8000")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
TEXT = os.getenv("JINA_TEXT", "رحلات الى مسقط اليوم")
ACTION = os.getenv("ACTION", "embed")

async def main() -> None:
    if not ADMIN_API_KEY:
        print("Set ADMIN_API_KEY environment variable before running this script.")
        return

    async with httpx.AsyncClient(timeout=20.0) as client:
        if ACTION == "embed":
            url = f"{BASE_URL}/admin/jina-embed"
            response = await client.post(
                url,
                json={"text": TEXT},
                headers={"X-Admin-Api-Key": ADMIN_API_KEY},
            )
        else:
            url = f"{BASE_URL}/admin/llm-tool-call"
            response = await client.post(
                url,
                json={"message": TEXT},
                headers={"X-Admin-Api-Key": ADMIN_API_KEY},
            )

    print("STATUS:", response.status_code)
    try:
        print(response.json())
    except Exception:
        print(response.text)

if __name__ == "__main__":
    asyncio.run(main())