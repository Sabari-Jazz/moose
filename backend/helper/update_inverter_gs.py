#!/usr/bin/env python3
"""
Update Inverter GSI2 Fields Script
This script scans DynamoDB for inverter profile entries and adds GSI2PK and GSI2SK fields
"""

import os
import boto3
import json
from typing import Dict, Any, List
import logging
from botocore.exceptions import ClientError
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION_', 'us-east-1')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB')

def get_dynamodb_client():
    """Initialize DynamoDB client and table"""
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        logger.info(f"Connected to DynamoDB table: {DYNAMODB_TABLE_NAME}")
        return table
    except Exception as e:
        logger.error(f"Failed to connect to DynamoDB: {str(e)}")
        return None

def scan_inverter_profiles(table) -> List[Dict[str, Any]]:
    """
    Scan DynamoDB table for inverter profile entries
    Returns list of items where PK begins with 'Inverter#' and SK = 'PROFILE'
    """
    logger.info("Starting scan for inverter profile entries...")
    
    items = []
    scan_kwargs = {
        'FilterExpression': boto3.dynamodb.conditions.Key('PK').begins_with('Inverter#') & 
                           boto3.dynamodb.conditions.Attr('SK').eq('PROFILE')
    }
    
    try:
        # Handle pagination
        while True:
            response = table.scan(**scan_kwargs)
            items.extend(response['Items'])
            
            # Check if there are more items to scan
            if 'LastEvaluatedKey' not in response:
                break
            
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            logger.info(f"Scanned {len(items)} items so far...")
            
        logger.info(f"Total inverter profile entries found: {len(items)}")
        return items
        
    except ClientError as e:
        logger.error(f"Error scanning table: {e.response['Error']['Message']}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error during scan: {str(e)}")
        return []

def update_inverter_gsi_fields(table, item: Dict[str, Any]) -> bool:
    """
    Update a single inverter item with GSI2PK and GSI2SK fields
    """
    try:
        # Extract required fields
        pk = item.get('PK')
        sk = item.get('SK')
        pv_system_id = item.get('pvSystemId')
        device_id = item.get('deviceId')
        
        # Validate required fields
        if not pk or not sk or not pv_system_id or not device_id:
            logger.warning(f"Missing required fields in item: PK={pk}, SK={sk}, pvSystemId={pv_system_id}, deviceId={device_id}")
            return False
        
        # Create GSI2 fields
        gsi2_pk = f"System#{pv_system_id}"
        gsi2_sk = f"Inverter#{device_id}"
        
        # Check if GSI2 fields already exist
        if item.get('GSI2PK') == gsi2_pk and item.get('GSI2SK') == gsi2_sk:
            logger.info(f"Item {pk} already has correct GSI2 fields, skipping...")
            return True
        
        # Update the item
        update_expression = "SET GSI2PK = :gsi2pk, GSI2SK = :gsi2sk"
        expression_attribute_values = {
            ':gsi2pk': gsi2_pk,
            ':gsi2sk': gsi2_sk
        }
        
        # Use conditional update to ensure we're updating the right item
        condition_expression = "PK = :pk AND SK = :sk"
        expression_attribute_values[':pk'] = pk
        expression_attribute_values[':sk'] = sk
        
        table.update_item(
            Key={'PK': pk, 'SK': sk},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression=condition_expression
        )
        
        logger.info(f"Successfully updated item: {pk} -> GSI2PK={gsi2_pk}, GSI2SK={gsi2_sk}")
        return True
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ConditionalCheckFailedException':
            logger.warning(f"Item {pk} was modified during update, skipping...")
        else:
            logger.error(f"Error updating item {pk}: {e.response['Error']['Message']}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error updating item {pk}: {str(e)}")
        return False

def main():
    """Main function to orchestrate the update process"""
    logger.info("Starting inverter GSI2 fields update process...")
    
    # Initialize DynamoDB connection
    table = get_dynamodb_client()
    if not table:
        logger.error("Failed to initialize DynamoDB connection. Exiting.")
        return
    
    # Scan for inverter profile entries
    inverter_items = scan_inverter_profiles(table)
    if not inverter_items:
        logger.warning("No inverter profile entries found. Exiting.")
        return
    
    # Process each item
    success_count = 0
    failure_count = 0
    skip_count = 0
    
    logger.info(f"Processing {len(inverter_items)} inverter profile entries...")
    
    for i, item in enumerate(inverter_items, 1):
        logger.info(f"Processing item {i}/{len(inverter_items)}: {item.get('PK')}")
        
        # Add a small delay to avoid throttling
        if i % 10 == 0:
            time.sleep(0.1)
        
        try:
            if update_inverter_gsi_fields(table, item):
                success_count += 1
            else:
                failure_count += 1
        except Exception as e:
            logger.error(f"Failed to process item {item.get('PK')}: {str(e)}")
            failure_count += 1
    
    # Summary
    logger.info("=" * 50)
    logger.info("UPDATE SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Total items processed: {len(inverter_items)}")
    logger.info(f"Successfully updated: {success_count}")
    logger.info(f"Failed to update: {failure_count}")
    logger.info(f"Update process completed!")

def preview_changes():
    """Preview what changes would be made without actually updating"""
    logger.info("PREVIEW MODE: Scanning for inverter profile entries...")
    
    table = get_dynamodb_client()
    if not table:
        logger.error("Failed to initialize DynamoDB connection. Exiting.")
        return
    
    inverter_items = scan_inverter_profiles(table)
    if not inverter_items:
        logger.warning("No inverter profile entries found.")
        return
    
    logger.info(f"Found {len(inverter_items)} inverter profile entries:")
    logger.info("=" * 80)
    
    for i, item in enumerate(inverter_items, 1):
        pk = item.get('PK', 'N/A')
        pv_system_id = item.get('pvSystemId', 'N/A')
        device_id = item.get('deviceId', 'N/A')
        existing_gsi2pk = item.get('GSI2PK', 'NOT SET')
        existing_gsi2sk = item.get('GSI2SK', 'NOT SET')
        
        proposed_gsi2pk = f"System#{pv_system_id}" if pv_system_id != 'N/A' else 'CANNOT SET'
        proposed_gsi2sk = f"Inverter#{device_id}" if device_id != 'N/A' else 'CANNOT SET'
        
        logger.info(f"{i}. PK: {pk}")
        logger.info(f"   pvSystemId: {pv_system_id}")
        logger.info(f"   deviceId: {device_id}")
        logger.info(f"   Current GSI2PK: {existing_gsi2pk}")
        logger.info(f"   Current GSI2SK: {existing_gsi2sk}")
        logger.info(f"   Proposed GSI2PK: {proposed_gsi2pk}")
        logger.info(f"   Proposed GSI2SK: {proposed_gsi2sk}")
        
        if existing_gsi2pk == proposed_gsi2pk and existing_gsi2sk == proposed_gsi2sk:
            logger.info("   Status: ALREADY CORRECT")
        elif pv_system_id == 'N/A' or device_id == 'N/A':
            logger.info("   Status: MISSING REQUIRED FIELDS")
        else:
            logger.info("   Status: NEEDS UPDATE")
        
        logger.info("-" * 80)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--preview":
        preview_changes()
    else:
        # Ask for confirmation before running
        print("This script will update inverter profile entries in your DynamoDB table.")
        print(f"Table: {DYNAMODB_TABLE_NAME}")
        print(f"Region: {AWS_REGION}")
        print("\nRun with --preview flag to see what changes would be made first.")
        
        response = input("\nAre you sure you want to proceed? (yes/no): ")
        if response.lower() in ['yes', 'y']:
            main()
        else:
            print("Operation cancelled.") 