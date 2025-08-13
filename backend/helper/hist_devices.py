"""
Historical Device Energy Data Collection Script

This script fetches historical energy production data for individual inverters
from the Solar.web API and stores it in DynamoDB for the energy loss calculation feature.

Key Features:
- Queries DynamoDB for all inverter profiles
- Fetches 5 weeks of historical energy data per inverter
- Stores data in DynamoDB with proper schema
- Uses JWT authentication and rate limiting
- Handles errors and retries gracefully

Usage:
python hist_devices.py
"""

import os
import json
import logging
import requests
import boto3
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from decimal import Decimal
import botocore.config
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('hist_devices')

# Configuration - using same patterns as other scripts
def validate_env_vars():
    """Validate required environment variables"""
    required_vars = ['SOLAR_WEB_ACCESS_KEY_ID', 'SOLAR_WEB_ACCESS_KEY_VALUE', 
                    'SOLAR_WEB_USERID', 'SOLAR_WEB_PASSWORD']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.warning(f"Missing environment variables: {missing_vars}. Using defaults.")

validate_env_vars()

# Solar.web API Configuration
API_BASE_URL = os.environ.get('API_BASE_URL', 'https://api.solarweb.com/swqapi')
ACCESS_KEY_ID = os.environ.get('SOLAR_WEB_ACCESS_KEY_ID', 'FKIAD151D135048B4C709FFA341FF599BA72')
ACCESS_KEY_VALUE = os.environ.get('SOLAR_WEB_ACCESS_KEY_VALUE', '77619b46-d62d-495d-8a07-aeaa8cf4b228')
USER_ID = os.environ.get('SOLAR_WEB_USERID', 'monitoring@jazzsolar.com')
PASSWORD = os.environ.get('SOLAR_WEB_PASSWORD', 'solar123')

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '3'))

# Initialize DynamoDB
dynamodb_config = botocore.config.Config(
    max_pool_connections=50
)
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION, config=dynamodb_config)
table = dynamodb.Table(os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB'))

# JWT token cache
_jwt_token_cache = {
    'token': None,
    'expires_at': None
}

# Thread lock for stats
stats_lock = threading.Lock()

# Cache for system earnings rates to avoid repeated DB queries
_earnings_rate_cache = {}

def get_jwt_token() -> str:
    """Get a JWT token for authentication with the Solar.web API with caching"""
    global _jwt_token_cache
    
    # Check if we have a valid cached token
    if (_jwt_token_cache['token'] and _jwt_token_cache['expires_at'] and 
        datetime.utcnow() < _jwt_token_cache['expires_at']):
        logger.debug("Using cached JWT token")
        return _jwt_token_cache['token']
    
    endpoint = f"{API_BASE_URL}/iam/jwt"
    headers = {
        'Content-Type': 'application/json',
        'AccessKeyId': ACCESS_KEY_ID,
        'AccessKeyValue': ACCESS_KEY_VALUE
    }
    payload = {
        'UserId': USER_ID,
        'password': PASSWORD
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Requesting JWT token from {endpoint} (attempt {attempt + 1})")
            response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            if 'jwtToken' not in data:
                raise ValueError("JWT response is missing the jwtToken field")
            
            # Cache the token
            _jwt_token_cache['token'] = data['jwtToken']
            _jwt_token_cache['expires_at'] = datetime.utcnow() + timedelta(hours=1)
            
            logger.info("JWT Token obtained and cached successfully")
            return data['jwtToken']
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"JWT request attempt {attempt + 1} failed: {str(e)}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise
        except Exception as e:
            logger.error(f"Error obtaining JWT token: {str(e)}")
            raise

def api_request(endpoint: str, method: str = 'GET', params: Dict[str, Any] = None) -> Dict[str, Any]:
    """Make an authenticated request to the Solar.web API with retry logic"""
    if endpoint.startswith('/'):
        endpoint = endpoint[1:]
    
    url = f"{API_BASE_URL}/{endpoint}"
    
    if params:
        query_parts = []
        for key, value in params.items():
            if value is not None:
                if isinstance(value, list):
                    query_parts.append(f"{key}={','.join(str(v) for v in value)}")
                else:
                    query_parts.append(f"{key}={value}")
        
        if query_parts:
            url += f"?{'&'.join(query_parts)}"
    
    for attempt in range(MAX_RETRIES):
        try:
            jwt_token = get_jwt_token()
            
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'AccessKeyId': ACCESS_KEY_ID,
                'AccessKeyValue': ACCESS_KEY_VALUE,
                'Authorization': f'Bearer {jwt_token}'
            }
            
            logger.debug(f"Making API request to {url}")
            response = requests.get(url, headers=headers, timeout=30)
            
            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
                continue
            
            response.raise_for_status()
            
            if response.status_code == 204:
                return {}
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"API request attempt {attempt + 1} failed: {str(e)}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                raise
        except Exception as e:
            logger.error(f"Error making API request: {str(e)}")
            raise

def get_all_inverter_profiles() -> List[Dict[str, Any]]:
    """
    Query DynamoDB for all inverter profiles where PK begins with "Inverter#" and SK = "PROFILE"
    Returns list of dictionaries with deviceId and pvSystemId
    """
    logger.info("Querying DynamoDB for inverter profiles...")
    
    inverter_profiles = []
    
    try:
        # Use scan with filter expression to find all inverter profiles
        response = table.scan(
            FilterExpression='begins_with(PK, :pk_prefix) AND SK = :sk_value',
            ExpressionAttributeValues={
                ':pk_prefix': 'Inverter#',
                ':sk_value': 'PROFILE'
            }
        )
        
        items = response.get('Items', [])
        
        # Handle pagination if there are more items
        while 'LastEvaluatedKey' in response:
            logger.info("Fetching more items from DynamoDB...")
            response = table.scan(
                FilterExpression='begins_with(PK, :pk_prefix) AND SK = :sk_value',
                ExpressionAttributeValues={
                    ':pk_prefix': 'Inverter#',
                    ':sk_value': 'PROFILE'
                },
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items.extend(response.get('Items', []))
        
        # Extract deviceId and pvSystemId from each item
        for item in items:
            device_id = item.get('deviceId')
            pv_system_id = item.get('pvSystemId')
            
            if device_id and pv_system_id:
                inverter_profiles.append({
                    'deviceId': device_id,
                    'systemId': pv_system_id
                })
        
        logger.info(f"Found {len(inverter_profiles)} inverter profiles")
        return inverter_profiles
        
    except ClientError as e:
        logger.error(f"DynamoDB query error: {e}")
        raise
    except Exception as e:
        logger.error(f"Error querying inverter profiles: {e}")
        raise

def get_inverter_daily_energy(system_id: str, device_id: str, date: str) -> Optional[float]:
    """
    Get daily energy production for a specific inverter on a specific date
    
    Args:
        system_id: PV system ID
        device_id: Device ID
        date: Date in YYYY-MM-DD format
        
    Returns:
        Energy production in Wh, or None if no data available
    """
    try:
        endpoint = f"pvsystems/{system_id}/devices/{device_id}/aggrdata"
        params = {
            'from': date,
            'to': date
        }
        
        logger.debug(f"Fetching energy data for device {device_id} on {date}")
        response = api_request(endpoint, params=params)
        
        # Extract energy value from response
        if 'data' in response and response['data']:
            for data_point in response['data']:
                if 'channels' in data_point and data_point['channels']:
                    for channel in data_point['channels']:
                        if channel.get('channelName') == 'EnergyExported':
                            energy_value = channel.get('value')
                            if energy_value is not None:
                                logger.debug(f"Found energy value: {energy_value} Wh for device {device_id} on {date}")
                                return float(energy_value)
        
        logger.warning(f"No energy data found for device {device_id} on {date}")
        return None
        
    except Exception as e:
        logger.error(f"Error fetching energy data for device {device_id} on {date}: {str(e)}")
        return None

def get_system_earnings_rate(system_id: str) -> float:
    """
    Get the earnings rate for a system from DynamoDB with caching
    
    Args:
        system_id: System ID
        
    Returns:
        Earnings rate in $/kWh, defaults to $0.30 if not found
    """
    global _earnings_rate_cache
    
    # Check cache first
    if system_id in _earnings_rate_cache:
        logger.debug(f"Using cached earnings rate for system {system_id}: ${_earnings_rate_cache[system_id]}/kWh")
        return _earnings_rate_cache[system_id]
    
    try:
        response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' in response:
            earnings_rate = response['Item'].get('earningsRate')
            if earnings_rate is not None:
                # Convert Decimal to float if needed
                earnings_rate = float(earnings_rate)
                logger.debug(f"Found earnings rate for system {system_id}: ${earnings_rate}/kWh")
                # Cache the result
                _earnings_rate_cache[system_id] = earnings_rate
                return earnings_rate
        
        logger.debug(f"No earnings rate found for system {system_id}, using default $0.30/kWh")
        earnings_rate = 0.30  # Default earnings rate
        # Cache the default value
        _earnings_rate_cache[system_id] = earnings_rate
        return earnings_rate
        
    except Exception as e:
        logger.error(f"Error getting earnings rate for system {system_id}: {str(e)}")
        earnings_rate = 0.30  # Default earnings rate on error
        # Cache the default value
        _earnings_rate_cache[system_id] = earnings_rate
        return earnings_rate

def store_daily_energy_data(device_id: str, system_id: str, date: str, energy_wh: float) -> bool:
    """
    Store daily energy data in DynamoDB with earnings calculation
    
    Args:
        device_id: Device ID
        system_id: System ID
        date: Date in YYYY-MM-DD format
        energy_wh: Energy production in Wh
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Get earnings rate for the system
        earnings_rate = get_system_earnings_rate(system_id)
        
        # Calculate earnings: convert Wh to kWh and multiply by earnings rate
        energy_kwh = energy_wh / 1000.0
        earnings = energy_kwh * earnings_rate
        
        item = {
            'PK': f'Inverter#{device_id}',
            'SK': f'DATA#DAILY#{date}',
            'deviceId': device_id,
            'systemId': system_id,
            'date': date,
            'dataType': 'DAILY_CONSOLIDATED',
            'energyProductionWh': Decimal(str(round(energy_wh, 2))),
            'earnings': Decimal(str(round(earnings, 4))),  # Store earnings with 4 decimal places
            'createdAt': datetime.utcnow().isoformat()
        }
        
        table.put_item(Item=item)
        logger.debug(f"Stored energy data for device {device_id} on {date}: {energy_wh} Wh, ${earnings:.4f} earnings")
        return True
        
    except Exception as e:
        logger.error(f"Error storing energy data for device {device_id} on {date}: {str(e)}")
        return False

def generate_date_range(start_date: str, end_date: str) -> List[str]:
    """
    Generate a list of dates between start_date and end_date (inclusive)
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        
    Returns:
        List of date strings in YYYY-MM-DD format
    """
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    
    return dates

def update_stats_thread_safe(stats, key, increment=1):
    """Thread-safe stats update"""
    with stats_lock:
        stats[key] += increment

def process_inverter_concurrent(inverter: Dict[str, Any], date_range: List[str], stats: Dict[str, int]) -> bool:
    """
    Process a single inverter for all dates in the date range
    
    Args:
        inverter: Dictionary with deviceId and systemId
        date_range: List of dates to process
        stats: Thread-safe statistics dictionary
        
    Returns:
        True if all dates processed successfully, False otherwise
    """
    device_id = inverter['deviceId']
    system_id = inverter['systemId']
    
    logger.info(f"Processing inverter {device_id} for {len(date_range)} dates")
    
    success_count = 0
    
    for date in date_range:
        try:
            # Get energy data from API
            energy_wh = get_inverter_daily_energy(system_id, device_id, date)
            
            # Update API call stats
            update_stats_thread_safe(stats, 'api_calls_made')
            
            # If no data found, store 0 instead of skipping the entry
            if energy_wh is None:
                energy_wh = 0.0
                logger.debug(f"No energy data found for device {device_id} on {date}, storing 0")
            
            # Store in DynamoDB (either actual value or 0 for missing data)
            if store_daily_energy_data(device_id, system_id, date, energy_wh):
                update_stats_thread_safe(stats, 'successful_requests')
                success_count += 1
            else:
                update_stats_thread_safe(stats, 'failed_requests')
                
            # Small delay to avoid overwhelming the API
            time.sleep(0.05)  # Reduced delay since we're processing concurrently
            
        except Exception as e:
            logger.error(f"Error processing device {device_id} on {date}: {str(e)}")
            update_stats_thread_safe(stats, 'failed_requests')
            continue
    
    # Update inverter completion stats
    update_stats_thread_safe(stats, 'inverters_processed')
    
    logger.info(f"Completed inverter {device_id}: {success_count}/{len(date_range)} dates successful")
    return success_count == len(date_range)

def main():
    """Main function to collect historical energy data with concurrent processing"""
    start_time = time.time()
    logger.info("Starting historical device energy data collection")
    
    # Define date range (5 weeks back from 2025-07-17)
    start_date = '2025-08-05'
    end_date = '2025-08-07'
  #  start_date = '2025-07-23'
   # end_date = '2025-07-24'
    date_range = generate_date_range(start_date, end_date)
    
    logger.info(f"Collecting data from {start_date} to {end_date} ({len(date_range)} days)")
    
    # Thread-safe statistics
    stats = {
        'inverters_processed': 0,
        'successful_requests': 0,
        'failed_requests': 0,
        'api_calls_made': 0,
        'errors': 0
    }
    
    try:
        # Fetch JWT token
        logger.info("Fetching JWT token...")
        get_jwt_token()
        
        # Get all inverter profiles
        logger.info("Fetching inverter profiles from DynamoDB...")
        inverter_profiles = get_all_inverter_profiles()
        if not inverter_profiles:
            logger.warning("No inverter profiles found")
            return stats
        
        logger.info(f"Found {len(inverter_profiles)} inverters. Starting concurrent processing...")
        
        # Process inverters in batches to avoid overwhelming the API
        batch_size = 16  # Smaller batch size than device polling since we're making more API calls per inverter
        total_batches = (len(inverter_profiles) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(inverter_profiles))
            batch_inverters = inverter_profiles[start_idx:end_idx]
            
            logger.info(f"Processing batch {batch_num + 1}/{total_batches}: inverters {start_idx + 1}-{end_idx}")
            
            # Process current batch concurrently
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_to_inverter = {
                    executor.submit(process_inverter_concurrent, inverter, date_range, stats): inverter 
                    for inverter in batch_inverters
                }
                
                # Wait for all inverters in this batch to complete
                for future in as_completed(future_to_inverter):
                    inverter = future_to_inverter[future]
                    try:
                        success = future.result()
                        
                        if not success:
                            update_stats_thread_safe(stats, 'errors')
                        
                    except Exception as e:
                        logger.error(f"❌ Error processing inverter {inverter['deviceId']}: {str(e)}")
                        update_stats_thread_safe(stats, 'errors')
            
            # Add delay between batches to avoid overwhelming the API
            if batch_num < total_batches - 1:
                logger.info(f"Batch {batch_num + 1} completed. Waiting 2 seconds before next batch...")
                time.sleep(2)
        
        end_time = time.time()
        execution_time = end_time - start_time
        stats['execution_time'] = execution_time
        
        # Calculate totals
        total_requests = len(inverter_profiles) * len(date_range)
        success_rate = (stats['successful_requests'] / total_requests * 100) if total_requests > 0 else 0
        
        # Final statistics
        logger.info("=== HISTORICAL DATA COLLECTION COMPLETED ===")
        logger.info(f"⏱️  Total execution time: {execution_time:.2f} seconds")
        logger.info(f"🔄 Inverters processed: {stats['inverters_processed']}")
        logger.info(f"📊 Total requests: {total_requests}")
        logger.info(f"✅ Successful requests: {stats['successful_requests']}")
        logger.info(f"❌ Failed requests: {stats['failed_requests']}")
        logger.info(f"🌐 API calls made: {stats['api_calls_made']}")
        logger.info(f"🚨 Errors: {stats['errors']}")
        logger.info(f"📈 Success rate: {success_rate:.1f}%")
        
        return stats
        
    except Exception as e:
        logger.error(f"Error in main process: {str(e)}")
        raise

if __name__ == "__main__":
    main() 