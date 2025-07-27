from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import datetime

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    password: str

from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime

class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    user_id: str  # Email is fine if you're using it as ID
    created_at: datetime = Field(default_factory=datetime.utcnow)

    messages: List["Message"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    content: str
    role: str
    conversation_id: int = Field(foreign_key="conversation.id")

    conversation: Optional[Conversation] = Relationship(back_populates="messages")
