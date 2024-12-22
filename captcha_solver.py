# captcha_solver.py (with asyncio task)
import aiohttp
import asyncio
from config import *

async def solve_captcha():
    payload = {
        "clientKey": CAPSOLVER_KEY,
        "task": {
            "type": 'ReCaptchaV2TaskProxyLess',
            "websiteKey": RECAP_SITE_KEY,
            "websiteURL": RECAP_SITE_URL
        }
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.capsolver.com/createTask", json=payload) as res:
            resp = await res.json()
            task_id = resp.get("taskId")
            if not task_id:
                print("Failed to create task:", resp)
                return None

            print(f"Got taskId: {task_id} / Getting result...")

        # Polling for CAPTCHA result asynchronously
        while True:
            await asyncio.sleep(3)  # non-blocking delay
            payload = {"clientKey": CAPSOLVER_KEY, "taskId": task_id}
            async with session.post("https://api.capsolver.com/getTaskResult", json=payload) as res:
                resp = await res.json()
                status = resp.get("status")
                if status == "ready":
                    return resp.get("solution", {}).get('gRecaptchaResponse')
                elif status == "failed" or resp.get("errorId"):
                    print("Solve failed! response:", resp)
                    return None
