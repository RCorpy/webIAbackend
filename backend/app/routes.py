from fastapi import APIRouter, Depends, HTTPException
from dotenv import load_dotenv
from fastapi.responses import StreamingResponse
from io import BytesIO
import os
import requests
import time
from .models import AIRequest
from .auth import get_current_user
from .db import users_collection, init_user, log_credit_movement

router = APIRouter()
load_dotenv()

INITIAL_CREDITS = 10
BFL_API_KEY = os.getenv("BFL_API_KEY")
BFL_ENDPOINT = "https://api.bfl.ai/v1/flux-kontext-pro"

tasks_store = {}

moderation_level = 0 #0 is super moderated, 6 is not moderated

@router.get("/credits")
async def get_credits(user: dict = Depends(get_current_user)):
    """Return current user credits, initializing if needed."""
    user_data = users_collection.find_one({"userId": user["uid"]})

    if not user_data:
        # Initialize new user and log credits
        init_user(user["uid"], user.get("email", ""), INITIAL_CREDITS)
        balance = log_credit_movement(user["uid"], INITIAL_CREDITS, "Initial signup")
        return {"credits": balance}

    return {"credits": user_data.get("credits", 0)}

###############################################################################################


@router.post("/ai")
async def create_ai_task(request: AIRequest, user: dict = Depends(get_current_user)):
    if not BFL_API_KEY:
        raise HTTPException(status_code=500, detail="BFL_API_KEY not set")

    headers = {
        "accept": "application/json",
        "x-key": BFL_API_KEY,
        "Content-Type": "application/json",
    }

    # Pick endpoint based on model
    if request.model == "flux-pro-1.1-model":
        bfl_endpoint = "https://api.bfl.ai/v1/flux-pro-1.1"
    elif request.model == "flux-pro-1.1-ultra-model":
        bfl_endpoint = "https://api.bfl.ai/v1/flux-pro-1.1-ultra"
    else:
        bfl_endpoint = "https://api.bfl.ai/v1/flux-kontext-pro"

    # Build payload
    payload = {"prompt": request.input}
    params = request.parameters or {}
    print("THIS ARE PARAMS: " , params)
    
    if request.model == "flux-pro-1.1-model":
        # flux-pro-1.1 → width/height
        payload["width"] = params.get("width", 1024)
        payload["height"] = params.get("height", 1024)
    else:
        # kontext → aspect_ratio
        payload["aspect_ratio"] = params.get("aspect_ratio", "1:1")

    payload["safety_tolerance"]=int(moderation_level) #MODERACION
    if request.model =="flux-pro-1.1-ultra-model":
        payload["raw"] = params.get("raw", False)

    # Debug mode: just log and return dummy task
    if os.getenv("BFL_DEBUG_MODE", "false").lower() == "true":
        print(f"🟡 [DEBUG] Would POST to: {bfl_endpoint}")
        print(f"🟡 [DEBUG] Payload: {payload}")
        task_id = f"debug-{len(tasks_store)}"
        tasks_store[task_id] = {
            "status": "Ready",
            "result": {
                "sample": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSp9Lf5jF7fnT9ft9KJokrIbrEXWq6CtbKEag&s"
            },
            "uid": user["uid"],
        }
        return {"task_id": task_id}

    # Normal mode: real request
    resp = requests.post(bfl_endpoint, headers=headers, json=payload)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Invalid BFL response: {resp.json()}",
        )
    print("🔹 Sent payload:", payload)
    print("🔹 BFL response:", resp.status_code, resp.text)

    task = resp.json()
    polling_url = task.get("polling_url")
    if not polling_url:
        raise HTTPException(status_code=500, detail="No polling_url in BFL response")

    # Store task
    task_id = f"task-{len(tasks_store)}"
    tasks_store[task_id] = {
        "polling_url": polling_url,
        "status": "Pending",
        "uid": user["uid"],
    }

    return {"task_id": task_id}



@router.get("/ai/status/{task_id}")
async def check_ai_status(task_id: str):
    task = tasks_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    uid = task.get("uid")
    user_data = users_collection.find_one({"userId": uid}) if uid else {}
    credits_left = user_data.get("credits", 0) if user_data else None

    # Debug mode task
    if task_id.startswith("debug-"):
        if task["status"] != "Ready":
            # Deduct credit on success
            credits_left = log_credit_movement(uid, -1, "AI request") if uid else credits_left
            task["status"] = "Ready"

        return {
            "status": "Ready",
            "output": task["result"]["sample"],
            "credits_left": credits_left
        }

    # Normal BFL task
    polling_url = task.get("polling_url")
    headers = {"accept": "application/json", "x-key": BFL_API_KEY}
    try:
        resp = requests.get(polling_url, headers=headers, timeout=30)
        print(f"🔹 BFL poll response for {task_id}:", resp.status_code, resp.text)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"BFL polling error: {str(e)}")

    result = resp.json()
    status = result.get("status")

    if status == "Ready":
        # Deduct 1 credit now
        credits_left = log_credit_movement(uid, -1, "AI request") if uid else credits_left

        task["status"] = "Ready"
        task["result"] = result.get("result", {})

        return {
            "status": "Ready",
            "output": task["result"].get("sample"),
            "credits_left": credits_left,
            "raw": result
        }

    elif status not in ["Pending", "Processing", "Generating", "Ready"]:
        task["status"] = "Failed"
        details = result.get("details") or {}
        reasons = details.get("Moderation Reasons") or []
        error_msg = f"Request blocked: {status}"
        if reasons:
            error_msg += f" ({', '.join(reasons)})"

        return {
            "status": "Failed",
            "detail": error_msg,
            "credits_left": credits_left,
            "raw": result
        }

    else:
        # Pending / still processing
        task["status"] = "Pending"
        return {
            "status": "Pending",
            "credits_left": credits_left,
            "raw": result
        }






@router.get("/ai/download")
async def proxy_download(url: str):
    try:
        r = requests.get(url, stream=True)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch image: {e}")

    return StreamingResponse(
        BytesIO(r.content),
        media_type="image/png",
        headers={"Content-Disposition": "attachment; filename=ai-result.png"}
    )

###############################################################################################
@router.post("/credits/add")
async def add_credits(amount: int, user: dict = Depends(get_current_user)):
    """Admin/test endpoint to add credits to the current user."""
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    # Add credits and log
    balance = log_credit_movement(user["uid"], amount, f"Manual add +{amount}")
    return {"credits_left": balance}
###############################################################################################
