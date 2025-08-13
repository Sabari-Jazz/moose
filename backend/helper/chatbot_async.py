import os
import json
import uuid
import base64
import boto3
from datetime import datetime

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SQS_QUEUE_URL = os.environ.get(
    "CHATBOT_SQS_QUEUE_URL",
    "https://sqs.us-east-1.amazonaws.com/381492109487/chatbot-queue",
)
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "Moose-DDB")

sqs_client = boto3.client("sqs", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
ddb_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


def _parse_body(event):
    body = event.get("body")
    if not body:
        return {}
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {}


def _get_status(job_id: str):
    try:
        resp = ddb_table.get_item(
            Key={
                "PK": f"ChatResponse#{job_id}",
                "SK": "RESULT",
            }
        )
        if "Item" in resp:
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({
                    "status": "done",
                    "response": resp["Item"].get("response", ""),
                    "timestamp": resp["Item"].get("timestamp"),
                }),
            }
        else:
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "pending"}),
            }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": f"Failed to get status: {str(e)}"}),
        }


def lambda_handler(event, context):
    try:
        method = event.get("httpMethod", "POST")
        if method == "GET":
            qs = event.get("queryStringParameters") or {}
            job_id = qs.get("jobId") if isinstance(qs, dict) else None
            if not job_id:
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": "jobId is required"}),
                }
            return _get_status(job_id)

        # Default POST: enqueue job
        body = _parse_body(event)

        # Expected fields from chat.tsx; pass through any additional supporting data
        message = body.get("message") or body.get("prompt") or ""
        user_id = body.get("user_id") or body.get("userId")
        system_id = body.get("system_id") or body.get("systemId")
        jwt_token = body.get("jwtToken") or body.get("jwt_token")
        username = body.get("username") or body.get("name")
        portfolio_systems = body.get("portfolioSystems")  # optional
        portfolio_data = body.get("portfolio_data")  # optional, preferred if provided

        if not message:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "message is required"}),
            }

        job_id = str(uuid.uuid4())

        payload = {
            "jobId": job_id,
            "message": message,
            "userId": user_id,
            "systemId": system_id,
            "jwtToken": jwt_token,
            "username": username,
            # Prefer structured portfolio_data if provided; otherwise pass through raw list
            "portfolio_data": portfolio_data,
            "portfolioSystems": portfolio_systems,
            "requestedAt": datetime.utcnow().isoformat() + "Z",
        }

        sqs_client.send_message(QueueUrl=SQS_QUEUE_URL, MessageBody=json.dumps(payload))

        return {
            "statusCode": 202,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "jobId": job_id,
                "status": "accepted",
                "queueUrl": SQS_QUEUE_URL,
            }),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": f"Failed to enqueue job or get status: {str(e)}"}),
        } 