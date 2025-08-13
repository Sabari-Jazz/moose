"""
Solar System Notification Handler

This script handles incoming SNS messages for solar system status changes
and sends push notifications to relevant users via the Expo Push Notification service.

Key Features:
- Processes SNS messages for status changes
- Looks up users with access to each system
- Gathers Expo push tokens from all relevant user devices
- Sends notifications in a single batch request to Expo for efficiency
- No DynamoDB status updates (handled by status_polling.py)

Usage:
- As AWS Lambda: deploy and configure with SNS trigger.
- Note: This function requires the 'requests' library to be included in the deployment package.
"""

import json
import logging
import os
import boto3
import requests
import uuid
from typing import List, Dict, Any
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('notify')

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION_', 'us-east-1')

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
table = dynamodb.Table(os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB'))
sns = boto3.client('sns', region_name=AWS_REGION)
scheduler = boto3.client('scheduler', region_name=AWS_REGION)

def get_users_with_system_access(system_id: str) -> List[str]:
    """Get all users who have access to the specified system"""
    try:
        # Always include admin user ID (hardcoded)
        ADMIN_USER_ID = "04484418-1051-70ea-d0d3-afb45eadb6e7"
        user_ids = [ADMIN_USER_ID]
        

        response = table.query(
            IndexName='user-system-index',  # <- your actual GSI name
            KeyConditionExpression=Key('GSI1PK').eq(f'System#{system_id}') & Key('GSI1SK').begins_with('User#'),
        )
        logger.info(f"Query response: {response}")
        
        for item in response.get('Items', []):
            user_id = item.get('userId')
                # Avoid duplicates in case admin is already in the system access list
            if user_id not in user_ids:
                user_ids.append(user_id)
        
        logger.info(f"Found {len(user_ids)} users with access to system {system_id} (including admin)")
        return user_ids
        
    except Exception as e:
        logger.error(f"Error getting users for system {system_id}: {str(e)}")
        # Even if there's an error, always return admin user ID
        return ["04484418-1051-70ea-d0d3-afb45eadb6e7"]

def get_user_profile(user_id: str) -> Dict[str, Any]:
    """Get user profile data from DynamoDB"""
    try:
        response = table.get_item(
            Key={
                'PK': f'User#{user_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' in response:
            logger.info(f"Retrieved profile for user {user_id}")
            return response['Item']
        else:
            logger.warning(f"No profile found for user {user_id}")
            return {}
            
    except Exception as e:
        logger.error(f"Error getting profile for user {user_id}: {str(e)}")
        return {}

def get_user_devices(user_id: str) -> List[Dict[str, Any]]:
    """Get all logged-in devices for a user"""
    try:
        response = table.query(
            KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
            ExpressionAttributeValues={
                ':pk': f'User#{user_id}',
                ':sk': 'Device#'
            }
        )
        
        devices = []
        for item in response.get('Items', []):
            # Debug: show actual item structure
            logger.info(f"   Raw DDB item: {json.dumps(item, default=str, indent=2)}")
            
            # Check both possible field names for push token
            push_token = item.get('pushToken') or item.get('expo_push_token')
            
            # Only check if device has push token (don't require isActive field)
            if push_token:
                devices.append({
                    'deviceId': item.get('deviceId') or item.get('SK', '').replace('Device#', ''),
                    'pushToken': push_token,
                    'platform': item.get('platform', 'unknown')
                })
                logger.info(f"   ✅ Found push token using field: {'pushToken' if item.get('pushToken') else 'expo_push_token'}")
            else:
                logger.info(f"   ❌ No push token found in either 'pushToken' or 'expo_push_token' fields")
        
        logger.info(f"Found {len(devices)} active devices for user {user_id}")
        return devices
        
    except Exception as e:
        logger.error(f"Error getting devices for user {user_id}: {str(e)}")
        return []

def get_device_name(device_id: str, pv_system_id: str) -> str:
    """Get device name from DynamoDB, fallback to device ID"""
    try:
        response = table.get_item(
            Key={
                'PK': f'Inverter#{device_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' in response:
            device_name = response['Item'].get('deviceName')
            if device_name:
                logger.info(f"Found device name for {device_id}: {device_name}")
                return device_name
            else:
                logger.warning(f"Device profile found but no deviceName for {device_id}")
                return f"Inverter {device_id[:8]}"
        else:
            logger.warning(f"No device profile found for {device_id}")
            return f"Inverter {device_id[:8]}"
            
    except Exception as e:
        logger.error(f"Error getting device name for {device_id}: {str(e)}")
        return f"Inverter {device_id[:8]}"

def get_system_name(system_id: str) -> str:
    """Get system name from DynamoDB"""
    try:
        
       
        response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' in response:
            return response['Item'].get('name', response['Item'].get('pvSystemName', f'System {system_id[:8]}'))
        
        return f'System {system_id[:8]}'
            
    except Exception as e:
        logger.error(f"Error getting system name for {system_id}: {str(e)}")
        return f'System {system_id[:8]}'

def format_notification_message(display_name: str, new_status: str, previous_status: str, power: float, title: str, is_device: bool = False) -> Dict[str, str]:
    """Format notification title and body based on status change"""
    
    status_emojis = {
        'green': '✅',
        'red': '🔴', 
        'offline': '🔌'
    }
    
    status_names = {
        'green': 'Online',
        'red': 'Error',
        'offline': 'Offline'
    }
    
    new_emoji = status_emojis.get(new_status, '⚡')
    new_name = status_names.get(new_status, new_status.title())
    
    device_type = "Inverter" if is_device else "System"
    title = f"{new_emoji} {display_name} {title}"
 
    
    if new_status == 'green':
        if previous_status == 'red':
            body = f"{device_type} recovered and is now online. Current power: {power:,.0f}W"
        elif previous_status == 'offline':
            body = f"{device_type} is back online. Current power: {power:,.0f}W"
        else:
            body = f"{device_type} status: {new_name}. Current power: {power:,.0f}W"
    elif new_status == 'red':
        body = f"{device_type} has errors and needs attention. Current power: {power:,.0f}W"
    elif new_status == 'offline':
        body = f"{device_type} is offline and not responding."
    else:
        body = f"Status changed from {previous_status} to {new_status}"
    
    return {
        'title': title,
        'body': body
    }

def send_expo_notifications(tokens: List[str], title: str, body: str, data: Dict[str, Any]) -> bool:
    """Sends push notifications to a list of Expo push tokens in a single batch."""
    logger.info(f"🔔 Processing {len(tokens)} push tokens for notifications:")
    
    messages = []
    for i, token in enumerate(tokens, 1):
        token_preview = token[:50] + '...' if len(token) > 50 else token
        # Basic validation to ensure it's an Expo token
        if token.startswith('ExponentPushToken['):
            messages.append({
                'to': token,
                'sound': 'default',
                'title': title,
                'body': body,
                'data': data
            })
            logger.info(f"   {i}. ✅ Valid Expo token: {token_preview}")
        else:
            logger.warning(f"   {i}. ❌ Invalid token format (not Expo): {token_preview}")
    
    logger.info(f"📤 Prepared {len(messages)} notification messages out of {len(tokens)} tokens")
    
    if not messages:
        logger.warning("No valid Expo push tokens to send notifications to.")
        return True # Return true as there's no error, just no one to notify

    try:
        response = requests.post(
            'https://exp.host/--/api/v2/push/send',
            headers={
                'Accept': 'application/json',
                'Accept-encoding': 'gzip, deflate',
                'Content-Type': 'application/json',
            },
            json=messages,
            timeout=30
        )
        response.raise_for_status()
        
        response_data = response.json().get('data', [])
        success_count = sum(1 for ticket in response_data if ticket.get('status') == 'ok')
        error_count = len(response_data) - success_count

        logger.info(f"📊 Expo API Response Details:")
        logger.info(f"   📤 Messages sent: {len(messages)}")
        logger.info(f"   📨 Responses received: {len(response_data)}")
        logger.info(f"   ✅ Successful: {success_count}")
        logger.info(f"   ❌ Errors: {error_count}")
        
        # Log detailed status for each response
        for i, ticket in enumerate(response_data, 1):
            status = ticket.get('status', 'unknown')
            if status == 'ok':
                logger.info(f"   {i}. ✅ Success: {ticket.get('id', 'no-id')}")
            else:
                error_msg = ticket.get('message', 'Unknown error')
                error_details = ticket.get('details', {})
                logger.error(f"   {i}. ❌ Error: {error_msg} - Details: {error_details}")
        
        logger.info(f"🏁 Final result: Success: {success_count}, Errors: {error_count}")
        
        # Additional debugging for delivery issues
        if success_count > 0:
            logger.info(f"📱 NOTIFICATION TROUBLESHOOTING TIPS:")
            logger.info(f"   1. Make sure the app is CLOSED or BACKGROUNDED on your iPhone")
            logger.info(f"   2. Check iPhone Settings → [App Name] → Notifications are enabled")
            logger.info(f"   3. Ensure Do Not Disturb / Focus mode is OFF")
            logger.info(f"   4. Wait 10-30 seconds for delivery")
            logger.info(f"   5. Check notification history by swiping down from top of screen")

        return error_count == 0

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error sending notifications to Expo API: {str(e)}")
        return False

def process_status_change_notification(sns_message: Dict[str, Any]) -> Dict[str, int]:
    """Process a device-level status change notification from SNS"""
    stats = {
        'users_found': 0,
        'devices_found': 0,
        'notifications_sent': 0,
        'incidents_created': 0,
        'errors': 0
    }
    
    try:
        # Extract required fields - only device-level notifications supported
        device_id = sns_message.get('deviceId')
        system_id = sns_message.get('pvSystemId')
        new_status = sns_message.get('newStatus')
        previous_status = sns_message.get('previousStatus')
        power = sns_message.get('power', 0)
        title = sns_message.get('type')
        
        if not all([device_id, system_id, new_status, previous_status]):
            logger.error("Missing required fields in SNS message - device ID is required")
            stats['errors'] += 1
            return stats
        
        # ONLY process notifications and create incidents if newStatus is "red"
        if new_status != "red":
            logger.info(f"Status change notification ignored: {previous_status} → {new_status} (only 'red' status triggers notifications)")
            return stats
        
        logger.info(f"Processing device-level status change notification for device {device_id} in system {system_id}: {previous_status} → {new_status}")
        display_name = get_device_name(device_id, system_id)
        data_payload = {
            'deviceId': device_id,
            'systemId': system_id,
            'type': 'device_status_change'
        }
        
        # Get users with access to this system
        user_ids = get_users_with_system_access(system_id)
        stats['users_found'] = len(user_ids)
        
        if not user_ids:
            logger.warning(f"No users found with access to system {system_id}")
            return stats

        # Create incident records for each user (only if they have technician_email)
        for user_id in user_ids:
            if user_id != "04484418-1051-70ea-d0d3-afb45eadb6e7":
                # Get user profile to check for technician_email
                user_profile = get_user_profile(user_id)
                technician_email = user_profile.get('technician_email', '').strip()
                
                if technician_email:  # Check if technician_email exists and is not empty
                    logger.info(f"User {user_id} has technician_email: {technician_email} - creating incident record")
                    create_incident_record(user_id, system_id, device_id, new_status)
                    stats['incidents_created'] += 1
                else:
                    logger.info(f"User {user_id} does not have technician_email - skipping incident record creation")
            else:
                # Skip admin user for incident creation - this is expected behavior, not an error
                logger.info(f"Skipping incident creation for admin user {user_id}")

        # Collect all device tokens for all users with access
        all_expo_tokens = []
        total_devices = 0
        all_devices_details = []
        
        for user_id in user_ids:
            devices = get_user_devices(user_id)
            total_devices += len(devices)
            logger.info(f"📱 User {user_id} has {len(devices)} devices:")
            
            for device in devices:
                device_info = {
                    'user_id': user_id,
                    'device_id': device.get('deviceId', 'unknown'),
                    'platform': device.get('platform', 'unknown'),
                    'push_token': device.get('pushToken', 'none')[:50] + '...' if device.get('pushToken') else 'none',
                    'token_added': False
                }
                
                # Check if token looks like development or production
                token_env = "unknown"
                if device.get('pushToken'):
                    full_token = device.get('pushToken')
                    if 'ExponentPushToken[' in full_token and ']' in full_token:
                        # Extract the actual token part
                        token_inner = full_token.split('[')[1].split(']')[0]
                        if len(token_inner) < 30:
                            token_env = "development"
                        else:
                            token_env = "production"
                
                logger.info(f"   - Device {device_info['device_id']} ({device_info['platform']}): Token={device_info['push_token']} ({token_env})")
                all_devices_details.append(device_info)
                
                # Add token if it's not already in the list
                if device.get('pushToken') and device['pushToken'] not in all_expo_tokens:
                    all_expo_tokens.append(device['pushToken'])
                    device_info['token_added'] = True
                    logger.info(f"     ✅ Token added to notification list")
                elif device.get('pushToken') and device['pushToken'] in all_expo_tokens:
                    logger.info(f"     ⏭️  Token already in list (duplicate)")
                else:
                    logger.info(f"     ❌ No valid push token")
        
        logger.info(f"📊 Summary: {total_devices} total devices, {len(all_expo_tokens)} unique push tokens collected")
        
        stats['devices_found'] = total_devices

        if not all_expo_tokens:
            logger.warning(f"No active devices with push tokens found for system {system_id}")
            return stats

        # Format notification message (device-level only)
        notification = format_notification_message(
            display_name, new_status, previous_status, power, title, True
        )
        
        # Send one batch of notifications via Expo
        success = send_expo_notifications(
            tokens=all_expo_tokens,
            title=notification['title'],
            body=notification['body'],
            data=data_payload
        )
        
        if success:
            stats['notifications_sent'] = len(all_expo_tokens)
        else:
            stats['errors'] += len(all_expo_tokens)
        
        logger.info(f"✅ Device-level notification processing complete for {device_id}. Attempted to send to {stats['notifications_sent']} devices. Created {stats['incidents_created']} incident records.")
        return stats
        
    except Exception as e:
        logger.error(f"❌ Error processing status change notification: {str(e)}")
        stats['errors'] += 1
        return stats

def create_incident_record(user_id: str, system_id: str, device_id: str, new_status: str, max_retries: int = 3) -> bool:
    """Create an incident record in DynamoDB with retry logic"""
    incident_id = str(uuid.uuid4())
    expires_at = int((datetime.utcnow() + timedelta(hours=1)).timestamp())
    
    incident_record = {
        'PK': f'Incident#{incident_id}',
        'SK': f'User#{user_id}',
        'userId': user_id,
        'systemId': system_id,
        'deviceId': device_id,
        'GSI3PK': f'User#{user_id}',
        'status': 'pending',
        'expiresAt': expires_at,
        'newStatus': new_status
    }
    
    for attempt in range(max_retries):
        try:
            table.put_item(Item=incident_record)
            logger.info(f"✅ Created incident record {incident_id} for user {user_id}")
            
            # Create EventBridge scheduler for technician notification in 1 hour
            if create_technician_schedule(incident_id, user_id):
                logger.info(f"✅ Created EventBridge schedule for incident {incident_id}")
            else:
                logger.warning(f"⚠️ Failed to create EventBridge schedule for incident {incident_id}")
            
            return True
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed to create incident record: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))  # Exponential backoff
            else:
                logger.error(f"❌ Failed to create incident record after {max_retries} attempts: {str(e)}")
                return False
    
    return False

def create_technician_schedule(incident_id: str, user_id: str, max_retries: int = 3) -> bool:
    """Create a one-time EventBridge schedule to trigger technician notification in 1 hour"""
    schedule_name = f"incident-{incident_id[:8]}-user-{user_id[:8]}"
    
    # Calculate schedule time (1 hour from now)
    #schedule_time = datetime.utcnow() + timedelta(hours=1)
    schedule_time = datetime.utcnow() + timedelta(minutes=2)
    schedule_expression = f"at({schedule_time.strftime('%Y-%m-%dT%H:%M:%S')})"
    
    # Get the notify_technician Lambda function ARN from environment
    technician_lambda_arn = os.environ.get('NOTIFY_TECHNICIAN_LAMBDA_ARN', 'arn:aws:lambda:us-east-1:381492109487:function:lambda_notify_technician')
    if not technician_lambda_arn:
        logger.error("NOTIFY_TECHNICIAN_LAMBDA_ARN environment variable not set")
        return False
    
    for attempt in range(max_retries):
        try:
            response = scheduler.create_schedule(
                Name=schedule_name,
                ScheduleExpression=schedule_expression,
                Target={
                    'Arn': technician_lambda_arn,
                    'RoleArn': os.environ.get('EVENTBRIDGE_EXECUTION_ROLE_ARN', 'arn:aws:iam::381492109487:role/EventBridgeSchedulerExecutionRole'),
                    'Input': json.dumps({
                        'incident_id': incident_id,
                        'user_id': user_id
                    })
                },
                FlexibleTimeWindow={
                    'Mode': 'OFF'
                },
                State='ENABLED',
                Description=f'One-time schedule for incident {incident_id} technician notification'
            )
            
            logger.info(f"✅ Created EventBridge schedule {schedule_name} for {schedule_time}")
            return True
            
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed to create EventBridge schedule: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))  # Exponential backoff
            else:
                logger.error(f"❌ Failed to create EventBridge schedule after {max_retries} attempts: {str(e)}")
                return False
    
    return False

def lambda_handler(event, context):
    """AWS Lambda handler function triggered by SNS"""
    try:
        logger.info("Notification handler started")
        
        total_stats = {
            'messages_processed': 0,
            'users_found': 0,
            'devices_found': 0,
            'notifications_sent': 0,
            'incidents_created': 0,
            'errors': 0
        }
        
        # Process SNS records
        for record in event.get('Records', []):
            if record.get('EventSource') == 'aws:sns':
                try:
                    # Parse SNS message
                    sns_message = json.loads(record['Sns']['Message'])
                    
                    # Process the status change
                    stats = process_status_change_notification(sns_message)
                    
                    # Aggregate stats
                    total_stats['messages_processed'] += 1
                    total_stats['users_found'] += stats['users_found']
                    total_stats['devices_found'] += stats['devices_found']
                    total_stats['notifications_sent'] += stats['notifications_sent']
                    total_stats['incidents_created'] += stats['incidents_created']
                    total_stats['errors'] += stats['errors']
                    
                except Exception as e:
                    logger.error(f"Error processing SNS record: {str(e)}")
                    total_stats['errors'] += 1
        
        logger.info("=== NOTIFICATION PROCESSING COMPLETED ===")
        logger.info(f"📨 Messages processed: {total_stats['messages_processed']}")
        logger.info(f"👥 Users found: {total_stats['users_found']}")
        logger.info(f"📱 Devices found: {total_stats['devices_found']}")
        logger.info(f"🔔 Notifications sent: {total_stats['notifications_sent']}")
        logger.info(f"📋 Incidents created: {total_stats['incidents_created']}")
        logger.info(f"❌ Errors: {total_stats['errors']}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(total_stats)
        }
        
    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Notification processing failed'
            })
        }

"""
if __name__ == "__main__":
    #Test the notification handler with a mock SNS event
    print("🚀 Testing notification handler with mock SNS event...")
    
    # Create test SNS message with the specified IDs
    test_device_id = "062e5910-9e1c-438d-8214-5ee290515a3f"
    test_system_id = "bf915090-5f59-4128-a206-46c73f2f779d"
    
    # Create mock SNS message matching the structure from device_status_polling.py
    mock_sns_message = {
        "deviceId": test_device_id,
        "pvSystemId": test_system_id,
        "newStatus": "red",
        "previousStatus": "green", 
        "newReason": "TEST NOTIFICATION",
        "previousReason": "",
        "timestamp": datetime.utcnow().isoformat(),
        "power": 0.0,
        "sunrise_time": "06:45 AM",
        "sunset_time": "06:30 PM",
        "timezone": "America/New_York",
        "flow_data": {"status": {"isOnline": True}, "data": None}
    }
    
    # Create mock SNS event structure
    mock_event = {
        'Records': [{
            'EventSource': 'aws:sns',
            'Sns': {
                'Message': json.dumps(mock_sns_message),
                'MessageAttributes': {
                    'source': {
                        'Value': 'device-status-polling-script'
                    },
                    'deviceId': {
                        'Value': test_device_id
                    },
                    'systemId': {
                        'Value': test_system_id
                    },
                    'statusChange': {
                        'Value': 'green-red'
                    }
                }
            }
        }]
    }
    
    # Create mock context
    class MockContext:
        def __init__(self):
            self.function_name = "notify_user_test"
            self.aws_request_id = "test-request-id"
    
    mock_context = MockContext()
    
    print("📋 Test Event Details:")
    print(f"   Device ID: {test_device_id}")
    print(f"   System ID: {test_system_id}")
    print(f"   Status Change: green → red")
    print(f"   Power: 0.0W")
    print(f"   Reason: no production")
    print()
    
    try:
        print("🏃 Running lambda_handler...")
        result = lambda_handler(mock_event, mock_context)
        
        print("✅ Lambda handler completed!")
        print("📤 Response:")
        print(json.dumps(result, indent=2))
        
        # Parse the result to see if notifications were sent
        result_body = json.loads(result.get('body', '{}'))
        notifications_sent = result_body.get('notifications_sent', 0)
        
        if notifications_sent > 0:
            print(f"\n🔔 {notifications_sent} notifications were sent successfully!")
            print("📱 TO CHECK YOUR PHONE:")
            print("   1. Make sure your app is CLOSED or in the background")
            print("   2. Wait 10-30 seconds for notification delivery")
            print("   3. Check notification center (swipe down from top)")
            print("   4. Look for notification banner")
            print("   5. Check if notification sound/vibration occurred")
            
            # Wait a bit to give time for delivery
            print("\n⏳ Waiting 15 seconds for notification delivery...")
            import time
            for i in range(15, 0, -1):
                print(f"   {i} seconds remaining...", end='\r')
                time.sleep(1)
            print("   ✅ Wait complete!           ")
            print("\n📱 Check your iPhone now for the notification!")
        
    except Exception as e:
        print(f"❌ Error running lambda_handler: {str(e)}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*50)
    print("🏁 Test completed!") 
    """