from pydantic import BaseModel, Field


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=80)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)

