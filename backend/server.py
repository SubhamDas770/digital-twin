import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from typing import Optional, List, Dict
import json
import uuid
from datetime import datetime
import boto3
from botocore.exceptions import ClientError
from context import prompt

# Load environment variables
load_dotenv()

app = FastAPI()

# Configure CORS
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Initialize Bedrock client - see Q42 on https://edwarddonner.com/faq if the Region gives you problems
bedrock_client = boto3.client(
    service_name="bedrock-runtime", 
    region_name=os.getenv("DEFAULT_AWS_REGION", "eu-north-1")
)

# Bedrock model selection - see Q42 on https://edwarddonner.com/faq for more
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
BEDROCK_MAX_INPUT_CHARS = int(os.getenv("BEDROCK_MAX_INPUT_CHARS", "100000"))
if BEDROCK_MAX_INPUT_CHARS <= 0:
    raise ValueError("BEDROCK_MAX_INPUT_CHARS must be greater than zero")

# Memory storage configuration
USE_S3 = os.getenv("USE_S3", "false").lower() == "true"
MEMORY_DIR = os.getenv("MEMORY_DIR", "../memory")
S3_BUCKET = os.getenv("S3_BUCKET")

# Initialize S3 client if needed
if USE_S3:
    if not S3_BUCKET:
        raise ValueError("S3_BUCKET must be set when USE_S3 is true")
    s3_client = boto3.client("s3")


# Request/Response models
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: str


class Message(BaseModel):
    role: str
    content: str
    timestamp: str


# Memory management functions
def get_memory_path(session_id: str) -> str:
    return f"{session_id}.json"


def load_conversation(session_id: str) -> List[Dict]:
    """Load conversation history from storage"""
    if USE_S3:
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=get_memory_path(session_id))
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return []
            raise
    else:
        # Local file storage
        file_path = os.path.join(MEMORY_DIR, get_memory_path(session_id))
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                return json.load(f)
        return []


def save_conversation(session_id: str, messages: List[Dict]):
    """Save conversation history to storage"""
    if USE_S3:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=get_memory_path(session_id),
            Body=json.dumps(messages, indent=2),
            ContentType="application/json",
        )
    else:
        # Local file storage
        os.makedirs(MEMORY_DIR, exist_ok=True)
        file_path = os.path.join(MEMORY_DIR, get_memory_path(session_id))
        with open(file_path, "w") as f:
            json.dump(messages, f, indent=2)


def call_bedrock(conversation: List[Dict], user_message: str) -> str:
    """Call AWS Bedrock with conversation history"""
    system_prompt = prompt()
    if len(system_prompt) + len(user_message) > BEDROCK_MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=413,
            detail="System prompt and message exceed the configured Bedrock input limit",
        )

    history_pairs = []
    if len(conversation) % 2:
        raise ValueError("Conversation history must contain complete user-assistant turns")

    for index in range(0, len(conversation), 2):
        user_turn, assistant_turn = conversation[index : index + 2]
        if (
            user_turn.get("role") != "user"
            or assistant_turn.get("role") != "assistant"
            or not isinstance(user_turn.get("content"), str)
            or not isinstance(assistant_turn.get("content"), str)
        ):
            raise ValueError("Conversation history contains an invalid user-assistant turn")
        history_pairs.append((user_turn["content"], assistant_turn["content"]))

    selected_pairs = []
    input_chars = len(system_prompt) + len(user_message)
    for user_turn, assistant_turn in reversed(history_pairs):
        turn_chars = len(user_turn) + len(assistant_turn)
        if input_chars + turn_chars > BEDROCK_MAX_INPUT_CHARS:
            break
        selected_pairs.append((user_turn, assistant_turn))
        input_chars += turn_chars

    messages = []
    for user_turn, assistant_turn in reversed(selected_pairs):
        messages.extend(
            [
                {"role": "user", "content": [{"text": user_turn}]},
                {"role": "assistant", "content": [{"text": assistant_turn}]},
            ]
        )
    messages.append({"role": "user", "content": [{"text": user_message}]})

    try:
        # Call Bedrock using the converse API
        response = bedrock_client.converse(
            modelId=BEDROCK_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=messages,
            inferenceConfig={
                "maxTokens": 2000,
                "temperature": 0.7,
                "topP": 0.9
            }
        )
        
        # Extract the response text
        return response["output"]["message"]["content"][0]["text"]
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ValidationException':
            # Handle message format issues
            print(f"Bedrock validation error: {e}")
            raise HTTPException(status_code=400, detail="Invalid message format for Bedrock")
        elif error_code == 'AccessDeniedException':
            print(f"Bedrock access denied: {e}")
            raise HTTPException(status_code=403, detail="Access denied to Bedrock model")
        else:
            print(f"Bedrock error: {e}")
            raise HTTPException(status_code=500, detail=f"Bedrock error: {str(e)}")


@app.get("/")
async def root():
    return {
        "message": "AI Digital Twin API (Powered by AWS Bedrock)",
        "memory_enabled": True,
        "storage": "S3" if USE_S3 else "local",
        "ai_model": BEDROCK_MODEL_ID
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy", 
        "use_s3": USE_S3,
        "bedrock_model": BEDROCK_MODEL_ID
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        # Generate session ID if not provided
        session_id = request.session_id or str(uuid.uuid4())

        # Load conversation history
        conversation = await asyncio.to_thread(load_conversation, session_id)

        # Call Bedrock for response
        assistant_response = await asyncio.to_thread(
            call_bedrock, conversation, request.message
        )

        # Update conversation history
        conversation.append(
            {"role": "user", "content": request.message, "timestamp": datetime.now().isoformat()}
        )
        conversation.append(
            {
                "role": "assistant",
                "content": assistant_response,
                "timestamp": datetime.now().isoformat(),
            }
        )

        # Save conversation
        await asyncio.to_thread(save_conversation, session_id, conversation)

        return ChatResponse(response=assistant_response, session_id=session_id)

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in chat endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/conversation/{session_id}")
async def get_conversation(session_id: str):
    """Retrieve conversation history"""
    try:
        conversation = await asyncio.to_thread(load_conversation, session_id)
        return {"session_id": session_id, "messages": conversation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
