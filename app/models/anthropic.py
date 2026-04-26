from pydantic import BaseModel
from typing import List, Optional, Union, Any, Dict

class MessageContent(BaseModel):
    type: str
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None

class Message(BaseModel):
    role: str
    content: Union[str, List[MessageContent]]

class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]

class MessagesRequest(BaseModel):
    model: str
    messages: List[Message]
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    max_tokens: int = 1024
    metadata: Optional[Dict[str, Any]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    tools: Optional[List[ToolDefinition]] = None
    tool_choice: Optional[Dict[str, Any]] = None

class ErrorDetail(BaseModel):
    type: str
    message: str

class ErrorResponse(BaseModel):
    type: str = "error"
    error: ErrorDetail
