"""
Update System Status Lambda Function

This Lambda function subscribes to SNS notifications from device status polling
and updates the system status in DynamoDB based on individual device changes.

Key Features:
- Triggered by SNS messages from device_status_polling.py
- Updates system status records in DynamoDB
- Categorizes inverters by status (green, red, moon)
- Determines overall system status based on inverter statuses
- Only updates if there are actual changes to minimize DynamoDB writes

Logic:
- If ANY red inverters: system status = "red"
- Else if ALL inverters are moon: system status = "moon"  
- Else: system status = "green"

Usage:
- Deploy as AWS Lambda function
- Configure SNS trigger with the same topic as device_status_polling.py
- Set environment variables for DynamoDB access
"""

import os
import json
import logging
import boto3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from decimal import Decimal
import botocore.config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('update_status')

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION_', 'us-east-1')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB')

# Configure DynamoDB
dynamodb_config = botocore.config.Config(
    max_pool_connections=50
)
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION, config=dynamodb_config)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

def get_inverter_statuses(system_id: str) -> Dict[str, List[str]]:
    """Get current status of all inverters for a system, categorized by status"""
    try:
        green_inverters = []
        red_inverters = []
        moon_inverters = []
        # Query for all inverter status records for this system
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('PK').begins_with('Inverter#') & 
                           boto3.dynamodb.conditions.Attr('SK').eq('STATUS') &
                           boto3.dynamodb.conditions.Attr('pvSystemId').eq(system_id)
        )
        for item in response.get('Items', []):
            # Extract device ID from PK
            device_id = item.get('device_id', '')
            status = item.get('status', 'Moon')  # Default to Moon if no status
            
            if status == 'green':
                green_inverters.append(device_id)
            elif status == 'red':
                red_inverters.append(device_id)
            elif status == 'Moon':  # Capital M as used in device_status_polling.py
                moon_inverters.append(device_id)
            else:
                # Unknown status, treat as Moon (default safe state)
                moon_inverters.append(device_id)
        
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey'],
                FilterExpression=boto3.dynamodb.conditions.Attr('PK').begins_with('Inverter#') & 
                            boto3.dynamodb.conditions.Attr('SK').eq('STATUS') &
                           boto3.dynamodb.conditions.Attr('pvSystemId').eq(system_id)
            )
                    
            for item in response.get('Items', []):
                # Extract device ID from PK
                device_id = item.get('device_id', '')
                status = item.get('status', 'Moon')  # Default to Moon if no status
                
                if status == 'green':
                    green_inverters.append(device_id)
                elif status == 'red':
                    red_inverters.append(device_id)
                elif status == 'Moon':  # Capital M as used in device_status_polling.py
                    moon_inverters.append(device_id)
                else:
                    # Unknown status, treat as Moon (default safe state)
                    moon_inverters.append(device_id)
        
        logger.info(f"System {system_id} inverter status breakdown:")
        logger.info(f"  Green: {len(green_inverters)} inverters")
        logger.info(f"  Red: {len(red_inverters)} inverters")
        logger.info(f"  Moon: {len(moon_inverters)} inverters")
        
        return {
            'green': green_inverters,
            'red': red_inverters,
            'moon': moon_inverters
        }
        
    except Exception as e:
        logger.error(f"Error getting inverter statuses for system {system_id}: {str(e)}")
        return {
            'green': [],
            'red': [],
            'moon': []
        }

def determine_system_status(green_inverters: List[str], red_inverters: List[str], moon_inverters: List[str]) -> str:
    """Determine overall system status based on inverter statuses"""
    # If one of the inverters is red, then overall status is red
    if len(red_inverters) > 0:
        return "red"
    # Else if all inverters are in moon state, then overall status is moon
    elif len(moon_inverters) > 0 and len(green_inverters) == 0:
        return "moon"
    # Else it is green
    else:
        return "green"

def get_current_system_status(system_id: str) -> Dict[str, Any]:
    """Get current system status record from DynamoDB"""
    try:
        response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': 'STATUS'
            }
        )
        
        if 'Item' in response:
            return response['Item']
        else:
            # Return default structure if no record exists
            return {
                'PK': f'System#{system_id}',
                'SK': 'STATUS',
                'pvSystemId': system_id,
                'status': 'moon',
                'GreenInverters': [],
                'RedInverters': [],
                'MoonInverters': [],
                'TotalInverters': 0,
                'lastUpdated': None
            }
            
    except Exception as e:
        logger.error(f"Error getting current system status for {system_id}: {str(e)}")
        return {
            'PK': f'System#{system_id}',
            'SK': 'STATUS',
            'pvSystemId': system_id,
            'status': 'moon',
            'GreenInverters': [],
            'RedInverters': [],
            'MoonInverters': [],
            'TotalInverters': 0,
            'lastUpdated': None
        }

def is_daylight_saving_time(dt: datetime) -> bool:
    """Determine if a given datetime falls within US daylight saving time period"""
    year = dt.year
    
    # DST starts on the second Sunday in March
    march_first = datetime(year, 3, 1)
    march_first_weekday = march_first.weekday()  # Monday = 0, Sunday = 6
    days_until_first_sunday = (6 - march_first_weekday) % 7
    first_sunday_march = march_first + timedelta(days=days_until_first_sunday)
    second_sunday_march = first_sunday_march + timedelta(days=7)
    dst_start = second_sunday_march.replace(hour=2)  # 2 AM
    
    # DST ends on the first Sunday in November
    nov_first = datetime(year, 11, 1)
    nov_first_weekday = nov_first.weekday()
    days_until_first_sunday = (6 - nov_first_weekday) % 7
    first_sunday_nov = nov_first + timedelta(days=days_until_first_sunday)
    dst_end = first_sunday_nov.replace(hour=2)  # 2 AM
    
    return dst_start <= dt < dst_end

def get_local_date_from_utc(utc_timestamp: str, system_timezone: Optional[str] = None) -> str:
    """Convert UTC timestamp to local date string (YYYY-MM-DD) based on system timezone"""
    try:
        # Parse UTC timestamp
        if utc_timestamp.endswith('Z'):
            utc_timestamp = utc_timestamp[:-1] + '+00:00'
        
        utc_dt = datetime.fromisoformat(utc_timestamp.replace('Z', '+00:00'))
        
        # Convert to system timezone if provided
        if system_timezone:
            is_dst = is_daylight_saving_time(utc_dt)
            
            if system_timezone == "America/New_York":
                # Eastern Time: UTC-5 (EST) or UTC-4 (EDT)
                offset_hours = 4 if is_dst else 5
                local_dt = utc_dt - timedelta(hours=offset_hours)
            elif system_timezone == "America/Chicago":
                # Central Time: UTC-6 (CST) or UTC-5 (CDT)
                offset_hours = 5 if is_dst else 6
                local_dt = utc_dt - timedelta(hours=offset_hours)
            else:
                logger.warning(f"Unknown timezone {system_timezone}, using UTC")
                local_dt = utc_dt
        else:
            # Fallback to UTC if no timezone provided
            local_dt = utc_dt
        
        return local_dt.strftime("%Y-%m-%d")
        
    except Exception as e:
        logger.error(f"Error converting UTC timestamp to local date: {str(e)}")
        # Fallback to current UTC date
        return datetime.utcnow().strftime("%Y-%m-%d")

def log_historical_status(device_id: str, system_id: str, new_status: str, timestamp: str, system_timezone: Optional[str] = None) -> bool:
    """Log historical status change for a device on the current date"""
    try:
        # Get local date based on system timezone
        local_date = get_local_date_from_utc(timestamp, system_timezone)
        
        logger.info(f"Logging historical status for device {device_id} on date {local_date}: {new_status}")
        
        # Try to get existing historical record for this date
        pk = f'Inverter#{device_id}'
        sk = f'DAILYSTATUS#{local_date}'
        
        try:
            response = table.get_item(
                Key={
                    'PK': pk,
                    'SK': sk
                }
            )
            
            if 'Item' in response:
                # Update existing record
                existing_item = response['Item']
                historic_status = existing_item.get('historicStatus', [])
            else:
                # Create new record
                historic_status = []
                
        except Exception as get_error:
            logger.warning(f"Error getting existing historical record: {str(get_error)}, creating new one")
            historic_status = []
        
        # Append new status entry
        status_entry = {
            'status': new_status,
            'time': timestamp  # Keep UTC timestamp for consistency
        }
        historic_status.append(status_entry)
        
        # Create/update the historical record
        historical_record = {
            'PK': pk,
            'SK': sk,
            'deviceId': device_id,
            'pvSystemId': system_id,
            'date': local_date,
            'lastUpdated': datetime.utcnow().isoformat(),
            'historicStatus': historic_status
        }
        
        # Update DynamoDB
        table.put_item(Item=historical_record)
        
        logger.info(f"✅ Historical status logged for device {device_id} on {local_date}: {new_status} at {timestamp}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error logging historical status for device {device_id}: {str(e)}")
        return False

def log_system_daily_status(system_id: str, new_status: str, timestamp: str, system_timezone: Optional[str] = None) -> bool:
    """Log daily status change for a system"""
    try:
        # Get local date based on system timezone
        local_date = get_local_date_from_utc(timestamp, system_timezone)
        
        logger.info(f"Logging daily status for system {system_id} on date {local_date}: {new_status}")
        
        # Try to get existing daily record for this date
        pk = f'System#{system_id}'
        sk = f'DAILYSTATUS#{local_date}'
        
        try:
            response = table.get_item(
                Key={
                    'PK': pk,
                    'SK': sk
                }
            )
            
            if 'Item' in response:
                # Update existing record
                existing_item = response['Item']
                historic_status = existing_item.get('historicStatus', [])
            else:
                # Create new record
                historic_status = []
                
        except Exception as get_error:
            logger.warning(f"Error getting existing system daily record: {str(get_error)}, creating new one")
            historic_status = []
        
        # Append new status entry
        status_entry = {
            'status': new_status,
            'time': timestamp  # Keep UTC timestamp for consistency
        }
        historic_status.append(status_entry)
        
        # Create/update the daily record
        daily_record = {
            'PK': pk,
            'SK': sk,
            'systemId': system_id,
            'date': local_date,
            'lastUpdated': datetime.utcnow().isoformat(),
            'historicStatus': historic_status
        }
        
        # Update DynamoDB
        table.put_item(Item=daily_record)
        
        logger.info(f"✅ Daily status logged for system {system_id} on {local_date}: {new_status} at {timestamp}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error logging daily status for system {system_id}: {str(e)}")
        return False

def update_system_status(system_id: str, green_inverters: List[str], red_inverters: List[str], moon_inverters: List[str], timestamp: str = None, system_timezone: str = None) -> bool:
    """Update system status in DynamoDB"""
    try:
        # Determine overall system status
        overall_status = determine_system_status(green_inverters, red_inverters, moon_inverters)
        
        # Get current system status to check for changes
        current_status_record = get_current_system_status(system_id)
        
        # Convert current lists to sets for comparison (handle None values)
        current_green = set(current_status_record.get('GreenInverters', []) or [])
        current_red = set(current_status_record.get('RedInverters', []) or [])
        current_moon = set(current_status_record.get('MoonInverters', []) or [])
        current_overall = current_status_record.get('status', 'moon')
        
        # Convert new lists to sets
        new_green = set(green_inverters)
        new_red = set(red_inverters)
        new_moon = set(moon_inverters)
        
        # Check if there are any changes
        if (current_green == new_green and 
            current_red == new_red and 
            current_moon == new_moon and 
            current_overall == overall_status):
            
            logger.info(f"No changes detected for system {system_id}, skipping update")
            return True
        
        # Create updated status record
        current_time = datetime.utcnow().isoformat()
        total_inverters = len(green_inverters) + len(red_inverters) + len(moon_inverters)
        
        status_record = {
            'PK': f'System#{system_id}',
            'SK': 'STATUS',
            'pvSystemId': system_id,
            'status': overall_status,
            'GreenInverters': green_inverters,
            'RedInverters': red_inverters,
            'MoonInverters': moon_inverters,
            'TotalInverters': total_inverters,
            'lastUpdated': current_time
        }
        
        # Update DynamoDB
        table.put_item(Item=status_record)
        
        # Log system daily status if we have timestamp and status changed
        if timestamp and current_overall != overall_status:
            daily_status_success = log_system_daily_status(system_id, overall_status, timestamp, system_timezone)
            if daily_status_success:
                logger.info(f"✅ Daily status logged for system {system_id}")
            else:
                logger.warning(f"⚠️ Failed to log daily status for system {system_id}")
        elif not timestamp:
            logger.debug(f"No timestamp provided for system {system_id}, skipping daily status logging")
        else:
            logger.debug(f"System {system_id} status unchanged, skipping daily status logging")
        
        # Log the update
        status_emoji = {"green": "✅", "red": "🔴", "moon": "🌙"}.get(overall_status, "❓")
        logger.info(f"{status_emoji} Updated system {system_id} status to {overall_status}")
        logger.info(f"  Green: {len(green_inverters)}, Red: {len(red_inverters)}, Moon: {len(moon_inverters)}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error updating system status for {system_id}: {str(e)}")
        return False

def process_device_status_change(device_id: str, system_id: str, new_status: str, previous_status: str, timestamp: str = None, system_timezone: str = None) -> bool:
    """Process a single device status change and update system status if needed"""
    try:
        logger.info(f"Processing device status change: {device_id} ({system_id}) {previous_status} → {new_status}")
        
        # Log historical status change if we have timestamp
        if timestamp:
            historical_success = log_historical_status(device_id, system_id, new_status, timestamp, system_timezone)
            if historical_success:
                logger.info(f"✅ Historical status logged for device {device_id}")
            else:
                logger.warning(f"⚠️ Failed to log historical status for device {device_id}")
        else:
            logger.warning(f"No timestamp provided for device {device_id}, skipping historical logging")
        
        # Get current status of all inverters for this system
        inverter_statuses = get_inverter_statuses(system_id)
        
        # Update system status based on current inverter statuses
        success = update_system_status(
            system_id,
            inverter_statuses['green'],
            inverter_statuses['red'],
            inverter_statuses['moon'],
            timestamp,
            system_timezone
        )
        
        if success:
            logger.info(f"✅ Successfully processed status change for device {device_id}")
        else:
            logger.error(f"❌ Failed to process status change for device {device_id}")
        
        return success
        
    except Exception as e:
        logger.error(f"Error processing device status change for {device_id}: {str(e)}")
        return False

def lambda_handler(event, context):
    """AWS Lambda handler function triggered by SNS"""
    try:
        logger.info("=== UPDATE STATUS LAMBDA TRIGGERED ===")
        logger.info(f"Received event: {json.dumps(event, indent=2)}")
        
        processed_count = 0
        success_count = 0
        
                        # Process each SNS record
        for record in event.get('Records', []):
            if record.get('EventSource') == 'aws:sns':
                try:
                    # Parse SNS message
                    message_body = record['Sns']['Message']
                    message_data = json.loads(message_body)
                    
                    # Extract device information
                    device_id = message_data.get('deviceId')
                    system_id = message_data.get('pvSystemId')
                    new_status = message_data.get('newStatus')
                    previous_status = message_data.get('previousStatus')
                    timestamp = message_data.get('timestamp')
                    system_timezone = message_data.get('timezone')
                    
                    if not all([device_id, system_id, new_status, previous_status]):
                        logger.warning(f"Incomplete message data: {message_data}")
                        continue
                    
                    processed_count += 1
                    
                    # Process the device status change
                    success = process_device_status_change(
                        device_id, system_id, new_status, previous_status, 
                        timestamp, system_timezone
                    )
                    
                    if success:
                        success_count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing SNS record: {str(e)}")
                    continue
        
        logger.info(f"=== UPDATE STATUS COMPLETED ===")
        logger.info(f"📊 Processed: {processed_count} messages")
        logger.info(f"✅ Successful: {success_count} updates")
        logger.info(f"❌ Failed: {processed_count - success_count} updates")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'processed': processed_count,
                'successful': success_count,
                'failed': processed_count - success_count
            })
        }
        
    except Exception as e:
        logger.error(f"Critical error in lambda handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

