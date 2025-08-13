"""
Automatic Monthly Report Lambda Function

This Lambda function sends automatic monthly text updates to users via SMS.
It queries DynamoDB for users with phone numbers, gets their systems data,
and sends formatted monthly reports via AWS SNS.

Features:
- Queries all users with phone numbers
- Gets monthly production and earnings data for each user's systems
- Formats system-level and aggregated KPIs in a readable SMS
- Sends reports via AWS SNS

Usage:
- Deploy as AWS Lambda function
- Configure to run monthly (e.g., via CloudWatch Events/EventBridge)
- Set environment variables for DynamoDB table and SNS configuration
"""

import os
import json
import boto3
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional
from boto3.dynamodb.conditions import Key
import uuid

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('automatic_report')

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION_', 'us-east-1')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB')

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)
sns = boto3.client('sns', region_name=AWS_REGION)

def get_users_with_phones() -> List[Dict[str, Any]]:
    """
    Get all users with role='user' and phoneNumber from DynamoDB
    """
    try:
        logger.info("Querying DynamoDB for user users with phone numbers...")
        
        # Scan for all user profiles
        response = table.scan(
            FilterExpression='begins_with(PK, :pk_prefix) AND SK = :sk_value',
            ExpressionAttributeValues={
                ':pk_prefix': 'User#',
                ':sk_value': 'PROFILE'
            }
        )
        
        user_users = []
        items = response.get('Items', [])
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            logger.info("Fetching more user profiles from DynamoDB...")
            response = table.scan(
                FilterExpression='begins_with(PK, :pk_prefix) AND SK = :sk_value',
                ExpressionAttributeValues={
                    ':pk_prefix': 'User#',
                    ':sk_value': 'PROFILE'
                },
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items.extend(response.get('Items', []))
        
        # Filter for user users with phone numbers
        for item in items:
            role = item.get('role', '')
            phone_number = item.get('phoneNumber', '')
            
            if role == 'user' and phone_number:
                user_users.append({
                    'userId': item.get('userId'),
                    'name': item.get('name', 'User'),
                    'phoneNumber': phone_number,
                    'email': item.get('email', '')
                })
        
        logger.info(f"Found {len(user_users)} user users with phone numbers")
        return user_users
        
    except Exception as e:
        logger.error(f"Error getting user users: {str(e)}")
        return []

def get_user_systems(user_id: str) -> List[str]:
    """
    Get all system IDs linked to a user
    """
    try:
        logger.info(f"Getting systems for user {user_id}")
        
        response = table.query(
            KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
            ExpressionAttributeValues={
                ':pk': f'User#{user_id}',
                ':sk': 'System#'
            }
        )
        
        system_ids = []
        for item in response.get('Items', []):
            system_id = item.get('systemId')
            if system_id:
                system_ids.append(system_id)
        
        logger.info(f"Found {len(system_ids)} systems for user {user_id}")
        return system_ids
        
    except Exception as e:
        logger.error(f"Error getting systems for user {user_id}: {str(e)}")
        return []

def get_system_monthly_data(system_id: str, month: str) -> Optional[Dict[str, Any]]:
    """
    Get monthly data for a specific system
    """
    try:
        logger.info(f"Getting monthly data for system {system_id} for month {month}")
        
        response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': f'DATA#MONTHLY#{month}'
            }
        )
        
        if 'Item' in response:
            item = response['Item']
            # Convert Decimals to float for calculations
            return {
                'systemId': system_id,
                'systemName': item.get('systemName', 'Unknown System'),
                'energyProductionWh': float(item.get('energyProductionWh', 0)),
                'earnings': float(item.get('earnings', 0)),
                'co2Savings': float(item.get('co2Savings', 0)),
                'month': month
            }
        else:
            logger.warning(f"No monthly data found for system {system_id} for month {month}")
            return None
            
    except Exception as e:
        logger.error(f"Error getting monthly data for system {system_id}: {str(e)}")
        return None

def format_report_message(user_name: str, systems_data: List[Dict[str, Any]], month: str) -> str:
    """
    Format the monthly report message for SMS
    """
    if not systems_data:
        return f"Hi {user_name}, no data available for your solar systems for {month}."
    
    # Calculate totals
    total_energy_kwh = sum(data['energyProductionWh'] for data in systems_data) / 1000  # Convert Wh to kWh
    total_earnings = sum(data['earnings'] for data in systems_data)
    total_co2_kg = sum(data['co2Savings'] for data in systems_data)
    
    # Start building message
    month_name = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    message_lines = [
        f"Solar Report - {month_name}",
        f"Hi {user_name}!",
        "",
        "MONTHLY SUMMARY:",
        f"Total Energy: {total_energy_kwh:.1f} kWh",
        f"Total Earnings: ${total_earnings:.2f}",
        f"CO2 Saved: {total_co2_kg:.1f} kg",
        ""
    ]
    
    # Add system breakdown if multiple systems
    if len(systems_data) > 1:
        message_lines.append("SYSTEM BREAKDOWN:")
        for data in systems_data:
            system_name = data['systemName'][:20] + "..." if len(data['systemName']) > 20 else data['systemName']
            energy_kwh = data['energyProductionWh'] / 1000
            earnings = data['earnings']
            message_lines.append(f"• {system_name}:")
            message_lines.append(f"  {energy_kwh:.1f} kWh, ${earnings:.2f}")
        message_lines.append("")
    
    # Add footer
    message_lines.extend([
        "Great work on clean energy!",
        "",
        "- Jazz Energy Team"
    ])
    
    return "\n".join(message_lines)

def send_sms_report(phone_number: str, message: str, user_id: str) -> bool:
    """
    Send SMS report via AWS SNS
    """
    try:
        logger.info(f"Sending SMS report to {phone_number}")
        
        # Ensure phone number is in E.164 format
        if not phone_number.startswith('+'):
            # Assume North American number if no country code
            if phone_number.startswith('1'):
                phone_number = '+' + phone_number
            else:
                phone_number = '+1' + phone_number
        
        # Remove any non-digit characters except the leading +
        import re
        phone_number = '+' + re.sub(r'[^\d]', '', phone_number[1:])
        logger.info(f"Sending SMS report to {phone_number}")
        logger.info(f"Message SENDING: {message}")
        
        response = sns.publish(
                #PhoneNumber=phone_number,
                PhoneNumber="+16135134833",
                Message=message,
                #Message="TEST MESSAGE",
                MessageAttributes={
                    'source': {
                        'DataType': 'String',
                        'StringValue': 'automatic-monthly-report'
                    },
                    'userId': {
                        'DataType': 'String',
                        'StringValue': user_id
                    },
                    'reportType': {
                        'DataType': 'String',
                        'StringValue': 'monthly-summary'
                    }
                }
            )

        
        logger.info(f"✅ SMS sent successfully to {phone_number}. Message ID: {response['MessageId']}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error sending SMS to {phone_number}: {str(e)}")
        return False

def lambda_handler(event, context):
    """
    Main Lambda handler function
    """
    logger.info("=== AUTOMATIC MONTHLY REPORT LAMBDA STARTED ===")
    
    try:
        # Get current month (or use month from event if provided)
        current_month = event.get('month') if event and 'month' in event else datetime.utcnow().strftime("%Y-%m")
        logger.info(f"Processing reports for month: {current_month}")
        
        # Get all user users with phone numbers
        user_users = get_users_with_phones()
        if not user_users:
            logger.warning("No user users with phone numbers found")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No user users with phone numbers found',
                    'processed_users': 0
                })
            }
        
        success_count = 0
        error_count = 0
        
        # Process each  user
        for user in user_users:
            try:
                user_id = user['userId']
                user_name = user['name']
                phone_number = user['phoneNumber']
                
                logger.info(f"Processing user: {user_name} ({user_id})")
                
                # Get user's systems
                system_ids = get_user_systems(user_id)
                if not system_ids:
                    logger.warning(f"No systems found for user {user_id}")
                    continue
                
                # Get monthly data for each system
                systems_data = []
                for system_id in system_ids:
                    monthly_data = get_system_monthly_data(system_id, current_month)
                    if monthly_data:
                        systems_data.append(monthly_data)
                
                if not systems_data:
                    logger.warning(f"No monthly data found for user {user_id}'s systems")
                    continue
                
                # Format and send report
                report_message = format_report_message(user_name, systems_data, current_month)
                
                if send_sms_report(phone_number, report_message, user_id):
                    success_count += 1
                    logger.info(f"✅ Successfully sent report to {user_name}")
                else:
                    error_count += 1
                    logger.error(f"❌ Failed to send report to {user_name}")
                    
            except Exception as e:
                logger.error(f"Error processing user {user.get('name', 'unknown')}: {str(e)}")
                error_count += 1
        
        logger.info(f"=== REPORT PROCESSING COMPLETED ===")
        logger.info(f"Successful reports: {success_count}")
        logger.info(f"Failed reports: {error_count}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Monthly reports processed successfully',
                'month': current_month,
                'successful_reports': success_count,
                'failed_reports': error_count,
                'total_users_processed': len(user_users)
            })
        }
        
    except Exception as e:
        logger.error(f"Critical error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal server error',
                'message': str(e)
            })
        }

def main():
    """
    Main function for local testing
    """
    logger.info("Running automatic report locally...")
    
    # Test with current month
    test_event = {}
    test_context = {}
    
    result = lambda_handler(test_event, test_context)
    logger.info(f"Result: {result}")

if __name__ == "__main__":
    main() 