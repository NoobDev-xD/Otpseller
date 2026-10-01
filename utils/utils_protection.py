import time
from collections import defaultdict
from config import ADMINS, CREATOR_ID

# --- CONFIGURATION ---
SPAM_THRESHOLD = 5  # Messages allowed per window
TIME_WINDOW = 15     # Seconds for the window
BANNED_USERS = set() # Temporary runtime ban

# --- FLOOD CONTROL ---
user_last_action = defaultdict(list)

async def is_flooding(user_id):
    """Check if a user is spamming the bot."""
    if user_id in ADMINS or user_id == CREATOR_ID:
        return False
        
    current_time = time.time()
    # Filter actions within the time window
    user_last_action[user_id] = [t for t in user_last_action[user_id] if current_time - t < TIME_WINDOW]
    
    if len(user_last_action[user_id]) >= SPAM_THRESHOLD:
        return True
        
    user_last_action[user_id].append(current_time)
    return False

# --- INPUT SANITIZATION ---
DANGEROUS_PAYLOADS = [
    "<script>", "javascript:", "eval(", "exec(", "system(", "os.", 
    "subprocess.", "import ", "__init__", "globals()", "locals()",
    "drop table", "drop collection", "delete from", "{{"
]

def is_dangerous_input(text):
    """Detects suspicious patterns in user messages."""
    if not text: return False
    text_lower = str(text).lower()
    
    for payload in DANGEROUS_PAYLOADS:
        if payload in text_lower:
            return True
    return False
