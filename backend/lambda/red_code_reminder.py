"""
Red Code Reminder Script

This script checks all inverters in DynamoDB for red status and sends daily reminder
SNS messages for inverters that have been in red status for over 15 hours.

Key Features:
- Scans all inverters in DynamoDB for red status
- Filters inverters with lastStatusChangeTime over 15 hours old
- Sends SNS notifications with type "Daily Reminder"
- Processes inverters concurrently for efficiency
- Designed as AWS Lambda function triggered by EventBridge scheduler every 24 hours

Usage:
- As AWS Lambda: deploy and configure with appropriate environment variables
- Triggered by EventBridge scheduler every 24 hours
"""

import os
import json
import logging
import boto3
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import botocore.config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('red_code_reminder')

# Configuration
def validate_env_vars():
    """Validate required environment variables"""
    required_vars = []  # Add any required vars if needed
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.warning(f"Missing environment variables: {missing_vars}. Using defaults.")

validate_env_vars()

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:381492109487:solarSystemAlerts')

# Configuration constants
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '3'))
RED_STATUS_THRESHOLD_HOURS = 15  # Hours after which to send reminder

# Initialize AWS clients
sns = boto3.client('sns', region_name=AWS_REGION)

# Configure DynamoDB with larger connection pool for concurrent operations
dynamodb_config = botocore.config.Config(
    max_pool_connections=50  # Increase from default 10 to handle concurrent threads
)
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION, config=dynamodb_config)
table = dynamodb.Table(os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB'))

# Thread lock for stats
stats_lock = threading.Lock()

class RedInverterInfo:
    def __init__(self, device_id: str, pv_system_id: str, status: str, reason: str, 
                 last_status_change_time: str, power: float):
        self.device_id = device_id
        self.pv_system_id = pv_system_id
        self.status = status
        self.reason = reason
        self.last_status_change_time = last_status_change_time
        self.power = power

def get_all_red_inverters() -> List[RedInverterInfo]:
    """Get all inverters with red status from DynamoDB"""
    try:
        # Query for all inverter status records with red status
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('PK').begins_with('Inverter#') & 
                           boto3.dynamodb.conditions.Attr('SK').eq('STATUS') &
                           boto3.dynamodb.conditions.Attr('status').eq('red')
        )
        
        red_inverters = []
        for item in response.get('Items', []):
            device_id = item.get('device_id', '')
            pv_system_id = item.get('pvSystemId', '')
            status = item.get('status', '')
            reason = item.get('reason', '')
            last_status_change_time = item.get('lastStatusChangeTime', '')
            power = float(item.get('power', 0))
            
            if device_id and pv_system_id and status == 'red':
                red_inverters.append(RedInverterInfo(
                    device_id=device_id,
                    pv_system_id=pv_system_id,
                    status=status,
                    reason=reason,
                    last_status_change_time=last_status_change_time,
                    power=power
                ))
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey'],
                FilterExpression=boto3.dynamodb.conditions.Attr('PK').begins_with('Inverter#') & 
                            boto3.dynamodb.conditions.Attr('SK').eq('STATUS') &
                            boto3.dynamodb.conditions.Attr('status').eq('red')
            )
            for item in response.get('Items', []):
                device_id = item.get('device_id', '')
                pv_system_id = item.get('pvSystemId', '')
                status = item.get('status', '')
                reason = item.get('reason', '')
                last_status_change_time = item.get('lastStatusChangeTime', '')
                power = float(item.get('power', 0))
                
                if device_id and pv_system_id and status == 'red':
                    red_inverters.append(RedInverterInfo(
                        device_id=device_id,
                        pv_system_id=pv_system_id,
                        status=status,
                        reason=reason,
                        last_status_change_time=last_status_change_time,
                        power=power
                    ))
        
        logger.info(f"Found {len(red_inverters)} red status inverters from DynamoDB")
        return red_inverters
        
    except Exception as e:
        logger.error(f"Failed to get red inverters from DynamoDB: {str(e)}")
        return []

def is_red_status_old_enough(last_status_change_time: str) -> bool:
    """Check if the red status is older than the threshold (15 hours)"""
    try:
        if not last_status_change_time:
            logger.warning("No lastStatusChangeTime provided, treating as old enough")
            return True
        
        # Parse the timestamp
        try:
            if last_status_change_time.endswith('Z'):
                # Remove Z and parse as UTC
                dt = datetime.fromisoformat(last_status_change_time.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(last_status_change_time)
        except ValueError:
            # Try alternative parsing
            dt = datetime.fromisoformat(last_status_change_time.replace('Z', ''))
            
        # Make datetime timezone-aware if it's not
        if dt.tzinfo is None:
            # Assume UTC if no timezone info
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo.utc)
        
        # Get current UTC time
        now_utc = datetime.now(datetime.now().astimezone().tzinfo.utc)
        
        # Calculate time difference
        time_diff = now_utc - dt
        hours_since_change = time_diff.total_seconds() / 3600
        
        is_old_enough = hours_since_change >= RED_STATUS_THRESHOLD_HOURS
        
        logger.debug(f"Status change time: {last_status_change_time}, Hours since: {hours_since_change:.2f}, Old enough: {is_old_enough}")
        
        return is_old_enough
        
    except Exception as e:
        logger.error(f"Error parsing lastStatusChangeTime '{last_status_change_time}': {str(e)}")
        return True  # Default to sending reminder if we can't parse the time

def send_red_code_reminder_sns(inverter: RedInverterInfo) -> bool:
    """Send SNS message for red code reminder"""
    try:
        message = {
            "deviceId": inverter.device_id,
            "pvSystemId": inverter.pv_system_id,
            "newStatus": inverter.status,  # Changed from "status" to "newStatus"
            "previousStatus": "red",       # Added required field - since it's a reminder, previous was also red
            "newReason": inverter.reason,  # Changed from "reason" to "newReason"
            "previousReason": inverter.reason,  # Added required field
            "lastStatusChangeTime": inverter.last_status_change_time,
            "power": inverter.power,
            "timestamp": datetime.utcnow().isoformat(),
            "type": "Daily Reminder"
        }
        
        # Build message attributes
        message_attributes = {
            'source': {
                'DataType': 'String',
                'StringValue': 'red-code-reminder-script'
            },
            'deviceId': {
                'DataType': 'String',
                'StringValue': inverter.device_id
            },
            'systemId': {
                'DataType': 'String',
                'StringValue': inverter.pv_system_id
            },
            'statusChange': {
                'DataType': 'String',
                'StringValue': f'red-{inverter.status}'  # Added statusChange attribute like test_sns.py
            },
            'type': {
                'DataType': 'String',
                'StringValue': 'Daily Reminder'
            }
        }
        
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"Daily Red Code Reminder - {inverter.device_id}",
            Message=json.dumps(message),
            MessageAttributes=message_attributes
        )
        
        logger.info(f"✅ Sent red code reminder for device {inverter.device_id} (reason: {inverter.reason}). Message ID: {response['MessageId']}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error sending red code reminder for device {inverter.device_id}: {str(e)}")
        return False

def process_red_inverter_reminder(inverter: RedInverterInfo, stats: Dict[str, int]) -> bool:
    """Process a single red inverter for reminder sending"""
    try:
        logger.info(f"Processing red inverter reminder for device: {inverter.device_id} (System: {inverter.pv_system_id})")
        
        # Check if the red status is old enough (over 15 hours)
        if is_red_status_old_enough(inverter.last_status_change_time):
            logger.info(f"Device {inverter.device_id} has been red for over {RED_STATUS_THRESHOLD_HOURS} hours, sending reminder")
            
            # Send SNS reminder
            sns_success = send_red_code_reminder_sns(inverter)
            
            if sns_success:
                update_stats_thread_safe(stats, 'reminders_sent')
                logger.info(f"✅ Red code reminder sent for device {inverter.device_id}")
                return True
            else:
                update_stats_thread_safe(stats, 'errors')
                return False
        else:
            logger.info(f"Device {inverter.device_id} has not been red long enough, skipping reminder")
            update_stats_thread_safe(stats, 'skipped_not_old_enough')
            return True
        
    except Exception as e:
        logger.error(f"❌ Error processing red inverter reminder for device {inverter.device_id}: {str(e)}")
        update_stats_thread_safe(stats, 'errors')
        return False

def update_stats_thread_safe(stats, key, increment=1):
    """Thread-safe stats update"""
    with stats_lock:
        stats[key] += increment

def process_red_code_reminders():
    """Main function to process red code reminders"""
    start_time = time.time()
    
    stats = {
        'red_inverters_found': 0,
        'reminders_sent': 0,
        'skipped_not_old_enough': 0,
        'errors': 0,
        'processed': 0
    }
    
    try:
        logger.info("Starting red code reminder processing...")
        
        # Get all red inverters from DynamoDB
        logger.info("Fetching red status inverters from DynamoDB...")
        red_inverters = get_all_red_inverters()
        
        if not red_inverters:
            logger.info("No red status inverters found")
            stats['red_inverters_found'] = 0
            return stats
        
        stats['red_inverters_found'] = len(red_inverters)
        logger.info(f"Found {len(red_inverters)} red status inverters. Processing reminders...")
        
        # Process inverters in batches for concurrent execution
        batch_size = 32
        total_batches = (len(red_inverters) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(red_inverters))
            batch_inverters = red_inverters[start_idx:end_idx]
            
            logger.info(f"Processing batch {batch_num + 1}/{total_batches}: inverters {start_idx + 1}-{end_idx}")
            
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_to_inverter = {
                    executor.submit(process_red_inverter_reminder, inverter, stats): inverter 
                    for inverter in batch_inverters
                }
                
                for future in as_completed(future_to_inverter):
                    inverter = future_to_inverter[future]
                    try:
                        success = future.result()
                        update_stats_thread_safe(stats, 'processed')
                        
                        if not success:
                            logger.warning(f"Failed to process reminder for device {inverter.device_id}")
                        
                    except Exception as e:
                        logger.error(f"❌ Error processing reminder for device {inverter.device_id}: {str(e)}")
                        update_stats_thread_safe(stats, 'errors')
            
            if batch_num < total_batches - 1:
                logger.info(f"Batch {batch_num + 1} completed. Waiting 0.5 seconds before next batch...")
                time.sleep(0.5)
        
        end_time = time.time()
        execution_time = end_time - start_time
        stats['execution_time'] = execution_time
        
        logger.info("=== RED CODE REMINDER PROCESSING COMPLETED ===")
        logger.info(f"⏱️  Total execution time: {execution_time:.2f} seconds")
        logger.info(f"🔴 Red inverters found: {stats['red_inverters_found']}")
        logger.info(f"📤 Reminders sent: {stats['reminders_sent']}")
        logger.info(f"⏭️  Skipped (not old enough): {stats['skipped_not_old_enough']}")
        logger.info(f"🔧 Total processed: {stats['processed']}")
        logger.info(f"❌ Errors: {stats['errors']}")
        
        # Print summary
        print(f"\n🏁 RED CODE REMINDER SUMMARY:")
        print(f"🔴 Red inverters found: {stats['red_inverters_found']}")
        print(f"📤 Reminders sent: {stats['reminders_sent']}")
        print(f"⏭️  Skipped (not old enough): {stats['skipped_not_old_enough']}")
        print(f"❌ Errors: {stats['errors']}")
        print("=" * 50)
        
        return stats
        
    except Exception as e:
        logger.error(f"Critical error in red code reminder processing: {str(e)}")
        stats['errors'] += 1
        return stats

def main():
    """Main entry point"""
    try:
        result = process_red_code_reminders()
        return result
        
    except Exception as e:
        logger.error(f"Error in main execution: {str(e)}")
        raise

def lambda_handler(event, context):
    """AWS Lambda handler function"""
    try:
        result = process_red_code_reminders()
        
        return {
            'statusCode': 200,
            'body': json.dumps(result, default=str)
        }
    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

if __name__ == "__main__":
    # Simulate EventBridge scheduler trigger for lambda_handler
    print("🚀 Simulating EventBridge scheduler trigger for red code reminder lambda_handler...")
    
    # Create mock event that EventBridge scheduler would send
    mock_event = {
        "version": "0",
        "id": "12345678-1234-1234-1234-123456789012",
        "detail-type": "Scheduled Event",
        "source": "aws.scheduler",
        "account": "123456789012",
        "time": datetime.utcnow().isoformat() + "Z",
        "region": "us-east-1",
        "resources": [
            "arn:aws:scheduler:us-east-1:123456789012:schedule/default/red-code-reminder"
        ],
        "detail": {}
    }
    
    # Create mock context object
    class MockContext:
        def __init__(self):
            self.function_name = "red-code-reminder"
            self.function_version = "$LATEST"
            self.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:red-code-reminder"
            self.memory_limit_in_mb = "512"
            self.remaining_time_in_millis = lambda: 300000  # 5 minutes
            self.log_group_name = "/aws/lambda/red-code-reminder"
            self.log_stream_name = "2024/01/01/[$LATEST]abcdefghijklmnopqrstuvwxyz123456"
            self.aws_request_id = "12345678-1234-1234-1234-123456789012"
    
    mock_context = MockContext()
    
    print("📋 Mock Event:", json.dumps(mock_event, indent=2, default=str))
    print("🏗️  Mock Context function_name:", mock_context.function_name)
    print("⏰ Starting lambda_handler simulation...")
    
    try:
        # Call the lambda_handler with mock event and context
        result = lambda_handler(mock_event, mock_context)
        
        print("✅ Lambda handler completed successfully!")
        print("📤 Lambda Response:", json.dumps(result, indent=2, default=str))
        
    except Exception as e:
        print(f"❌ Lambda handler failed: {str(e)}")
        raise 