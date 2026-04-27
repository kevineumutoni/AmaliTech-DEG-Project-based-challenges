"""
Pydantic models for request validation and response serialisation.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class CreateMonitorRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=128, description="Unique device identifier")
    timeout: int = Field(..., gt=0, description="Countdown in seconds (must be positive)")
    alert_email: str = Field(..., description="Email to notify when device goes silent")


class MonitorResponse(BaseModel):
    id: str
    timeout: int
    alert_email: str
    status: str          # ACTIVE | PAUSED | DOWN
    deadline: datetime
    last_heartbeat: Optional[datetime]
    created_at: datetime


class MessageResponse(BaseModel):
    message: str