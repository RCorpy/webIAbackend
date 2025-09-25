from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
import os

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
db = client["ai_api_db"]
users_collection = db["users"]
credit_logs_collection = db["credit_logs"]

def init_user(user_id: str, email: str, credits: int = 10):
    """Initialize a user with default credits if they don't exist."""
    users_collection.update_one(
        {"userId": user_id},
        {"$setOnInsert": {"userId": user_id, "email": email, "credits": credits}},
        upsert=True
    )

def log_credit_movement(user_id: str, change: int, reason: str):
    """
    Log any credit change and update balance in one step.
    Positive change = add credits, negative = spend credits.
    """
    # Atomically update credits
    result = users_collection.find_one_and_update(
        {"userId": user_id},
        {"$inc": {"credits": change}},
        return_document=True
    )

    if not result:
        raise ValueError(f"User {user_id} not found when logging credit movement")

    balance_after = result["credits"]

    # Create log entry
    log_entry = {
        "userId": user_id,
        "change": change,             # +10 or -1
        "reason": reason,             # "Initial signup", "AI request", etc.
        "balance_after": balance_after,
        "timestamp": datetime.utcnow(),
    }
    credit_logs_collection.insert_one(log_entry)

    return balance_after
