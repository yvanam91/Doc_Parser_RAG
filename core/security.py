import os
import json
from datetime import datetime, timezone
from typing import Tuple, Dict, Any

DB_PATH = "data/rate_limits.json"
USER_LIMIT = 300000
GLOBAL_LIMIT = 5000000

def _load_limits_db() -> Dict[str, Any]:
    """
    Loads raw rates database, resetting counters if UTC calendar day has changed.
    """
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    default_db = {
        "date": current_date,
        "global_consumed": 0,
        "users": {}
    }
    
    if not os.path.exists(DB_PATH):
        return default_db
        
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        return default_db
        
    if db.get("date") != current_date:
        db = default_db
        _save_limits_db(db)
        
    return db

def _save_limits_db(db: Dict[str, Any]) -> None:
    """
    Serializes state atomically to disk using rename replacement to guarantee file integrity.
    """
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        tmp_path = DB_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
        os.replace(tmp_path, DB_PATH)
    except Exception:
        pass

def check_and_update_quotas(user_id: str, estimated_tokens: int) -> Tuple[bool, str]:
    """
    Validates token transactions against daily personal and global budget bounds.
    """
    db = _load_limits_db()
    
    global_consumed = db.get("global_consumed", 0)
    if global_consumed + estimated_tokens > GLOBAL_LIMIT:
        remaining_global = max(0, GLOBAL_LIMIT - global_consumed)
        return (
            False,
            f"Global Daily Quota Exceeded. Requested: {estimated_tokens:,} tokens, "
            f"Remaining: {remaining_global:,} tokens (Limit: {GLOBAL_LIMIT:,} tokens/day)."
        )
        
    users = db.setdefault("users", {})
    user_consumed = users.get(user_id, 0)
    if user_consumed + estimated_tokens > USER_LIMIT:
        remaining_user = max(0, USER_LIMIT - user_consumed)
        return (
            False,
            f"Personal Daily Quota Exceeded. Requested: {estimated_tokens:,} tokens, "
            f"Remaining: {remaining_user:,} tokens (Limit: {USER_LIMIT:,} tokens/day)."
        )
        
    db["global_consumed"] = global_consumed + estimated_tokens
    db["users"][user_id] = user_consumed + estimated_tokens
    
    _save_limits_db(db)
    return (True, "")
