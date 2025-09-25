from typing import Optional, Dict, Any
from pydantic import BaseModel

class AIRequest(BaseModel):
    input: str
    model: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = {}
