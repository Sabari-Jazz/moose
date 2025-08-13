"""
Solar Device Status Polling Script

This script polls the Solar.web API to check device status, current power,
and error conditions for all inverters. It uses moon time periods to determine
status logic, updates DynamoDB when status changes, and sends SNS notifications.

Key Features:
- Monitors individual inverters instead of systems
- Uses device-specific endpoints for flowdata and messages
- Status logic: green/red/Moon based on moon time periods, power, and error conditions
- Updates DynamoDB only when status changes
- Sends SNS notifications for status changes

Status Logic:
- Within moon time (after sunset-1h OR before sunrise+1h): transitions between green/red/Moon based on power and current status
- Within daylight time (sunrise+1h to sunset-1h): green if power > 0, red if power = 0 (with specific error reasons)
- Uses system-specific timezones (America/New_York or America/Chicago) for accurate time comparisons

Usage:
- As a script: python device_status_polling.py
- As AWS Lambda: deploy and configure with appropriate environment variables
"""

import os
import json
import logging
import requests
import boto3
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import threading
import botocore.config
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for systems without zoneinfo (like older Python versions)
    ZoneInfo = None

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('device_status_polling')

# Configuration
def validate_env_vars():
    """Validate required environment variables"""
    required_vars = ['SOLAR_WEB_ACCESS_KEY_ID', 'SOLAR_WEB_ACCESS_KEY_VALUE', 
                    'SOLAR_WEB_USERID', 'SOLAR_WEB_PASSWORD']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.warning(f"Missing environment variables: {missing_vars}. Using defaults.")

validate_env_vars()

API_BASE_URL = os.environ.get('API_BASE_URL', 'https://api.solarweb.com/swqapi')
ACCESS_KEY_ID = os.environ.get('SOLAR_WEB_ACCESS_KEY_ID', 'FKIAD151D135048B4C709FFA341FF599BA72')
ACCESS_KEY_VALUE = os.environ.get('SOLAR_WEB_ACCESS_KEY_VALUE', '77619b46-d62d-495d-8a07-aeaa8cf4b228')
USER_ID = os.environ.get('SOLAR_WEB_USERID', 'monitoring@jazzsolar.com')
PASSWORD = os.environ.get('SOLAR_WEB_PASSWORD', 'solar123')

# Supabase Configuration
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://vemtgbvseyegqxychrzm.supabase.co')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZlbXRnYnZzZXllZ3F4eWNocnptIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDYwMTY1ODMsImV4cCI6MjA2MTU5MjU4M30.T8SFfZ2Ai1O77eNRQnKWk-_I9tePCjflJ4utGZKuBq4')

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:381492109487:solarSystemAlerts')

# Configuration constants
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '3'))

# Initialize AWS clients
sns = boto3.client('sns', region_name=AWS_REGION)

# Configure DynamoDB with larger connection pool for concurrent operations
dynamodb_config = botocore.config.Config(
    max_pool_connections=50  # Increase from default 10 to handle concurrent threads
)
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION, config=dynamodb_config)
table = dynamodb.Table(os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB'))

# JWT token cache
_jwt_token_cache = {
    'token': None,
    'expires_at': None
}

# Error codes cache
_error_codes_cache = {
    'codes': None,
    'expires_at': None
}

# Thread lock for stats
stats_lock = threading.Lock()

# Rate limiter for WeatherAPI calls (max 50 calls per minute)
weatherapi_rate_limiter = {
    'calls': [],  # List of timestamps when calls were made
    'lock': threading.Lock()
}

class InverterMetadata:
    def __init__(self, pv_system_id: str, device_id: str, system_name: str = None):
        self.pv_system_id = pv_system_id
        self.device_id = device_id
        self.system_name = system_name or f"System {pv_system_id}"

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
                time.sleep(2 ** attempt)
            else:
                raise
        except Exception as e:
            logger.error(f"Error obtaining JWT token: {str(e)}")
            raise

def get_all_error_codes_from_supabase() -> Dict[int, str]:
    """Get all error codes and their colors from Supabase with caching"""
    global _error_codes_cache
    
    # Check if we have a valid cached response
    if (_error_codes_cache['codes'] and _error_codes_cache['expires_at'] and 
        datetime.utcnow() < _error_codes_cache['expires_at']):
        logger.debug("Using cached error codes")
        return _error_codes_cache['codes']
    
    try:
        url = f"{SUPABASE_URL}/rest/v1/error_codes"
        headers = {
            'apikey': SUPABASE_ANON_KEY,
            'Authorization': f'Bearer {SUPABASE_ANON_KEY}',
            'Content-Type': 'application/json'
        }
        
        params = {
            'select': 'code,colour'
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        color_map = {}
        for item in data:
            if 'code' in item and 'colour' in item:
                color_map[item['code']] = item['colour']
        
        # Cache the results for 1 hour
        _error_codes_cache['codes'] = color_map
        _error_codes_cache['expires_at'] = datetime.utcnow() + timedelta(hours=1)
        
        logger.info(f"Retrieved and cached {len(color_map)} error codes from Supabase")
        return color_map
        
    except Exception as e:
        logger.error(f"Error fetching all error codes from Supabase: {str(e)}")
        return {}

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
                logger.error(f"API request failed for endpoint {endpoint} after {MAX_RETRIES} attempts")
                raise
        except Exception as e:
            logger.error(f"Unexpected error in API request: {str(e)}")
            raise

def get_sunrise_sunset_data(system_id: str, target_date: datetime) -> Dict[str, str]:
    """Get sunrise and sunset times for a system from DynamoDB"""
    try:
        # Convert target_date to the required format (2025-06-30)
        date_str = target_date.strftime("%Y-%m-%d")
        
        response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': f'DATA#DAILY#{date_str}'
            }
        )
        logger.info('INSIDE SUN DATA RESPONSE', response)
        
        if 'Item' in response:
            item = response['Item']
            sunrise = item.get('sunrise')
            sunset = item.get('sunset')
            
            if sunrise and sunset:
                logger.info(f"Found sunrise/sunset for system {system_id} on {date_str}: {sunrise} - {sunset}")
                return {'sunrise': sunrise, 'sunset': sunset}
            else:
                logger.info(f"Sunrise/sunset fields missing for system {system_id} on {date_str}")
                return {}
        else:
            logger.debug(f"No daily data found for system {system_id} on {date_str}")
            return {}
            
    except Exception as e:
        logger.error(f"Error getting sunrise/sunset data for system {system_id}: {str(e)}")
        return {}

def get_suntimes(system_id: str, target_date: datetime) -> Dict[str, str]:
    """Get sunrise and sunset times from Visual Crossing API and store in DynamoDB"""
    try:
        # Get system profile to extract GPS coordinates
        logger.info(f"Getting GPS coordinates for system {system_id}")
        profile_response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' not in profile_response:
            logger.warning(f"No system profile found for system {system_id}")
            return {}
        
        gps_data = profile_response['Item'].get('gpsData')
        if not gps_data:
            logger.warning(f"No gpsData found in system profile for system {system_id}")
            return {}
        
        latitude = gps_data.get('latitude')
        longitude = gps_data.get('longitude')
        
        if latitude is None or longitude is None:
            logger.warning(f"Missing latitude/longitude in gpsData for system {system_id}")
            return {}
        
        logger.info(f"Found GPS coordinates for system {system_id}: {latitude}, {longitude}")
        
        # Call WeatherAPI for astronomy data
        api_key = "54e30ff3809e4e15a98191133250907"  # You may want to update this key for WeatherAPI
        date_str = target_date.strftime('%Y-%m-%d')
        location = f"{latitude},{longitude}"
        
        astronomy_url = "http://api.weatherapi.com/v1/astronomy.json"
        astronomy_params = {
            "key": api_key,
            "q": location
        }
        
        # Enforce rate limit before making API call
        enforce_weatherapi_rate_limit()
        
        logger.info(f"Calling WeatherAPI for system {system_id}")
        response = requests.get(astronomy_url, params=astronomy_params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        # Extract sunrise and sunset times from WeatherAPI response
        if 'astronomy' not in data or 'astro' not in data['astronomy']:
            logger.warning(f"No astronomy data returned from WeatherAPI for system {system_id}")
            return {}
        
        astro_data = data['astronomy']['astro']
        sunrise = astro_data.get('sunrise')
        sunset = astro_data.get('sunset')
        
        if not sunrise or not sunset:
            logger.warning(f"Missing sunrise/sunset data in WeatherAPI response for system {system_id}")
            return {}
        
        logger.info(f"Retrieved sun times for system {system_id}: sunrise={sunrise}, sunset={sunset}")
        
        # Store in DynamoDB (update existing record and preserve other fields)
        try:
            table.update_item(
                Key={
                    'PK': f'System#{system_id}',
                    'SK': f'DATA#DAILY#{date_str}'
                },
                UpdateExpression='SET sunrise = :sunrise, sunset = :sunset',
                ExpressionAttributeValues={
                    ':sunrise': sunrise,
                    ':sunset': sunset
                }
            )
            logger.info(f"Successfully stored sunrise/sunset data in DynamoDB for system {system_id}")
        except Exception as db_error:
            logger.error(f"Error storing sunrise/sunset data in DynamoDB for system {system_id}: {str(db_error)}")
            # Continue and return the data even if storage fails
        
        # Return the sunrise/sunset times
        return {
            'sunrise': sunrise,
            'sunset': sunset
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"API request error getting sun times for system {system_id}: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Error getting sun times for system {system_id}: {str(e)}")
        return {}

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

def get_system_timezone(pv_system_id: str) -> Optional[str]:
    """Get timezone for a system from DynamoDB system profile"""
    try:
        response = table.get_item(
            Key={
                'PK': f'System#{pv_system_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' in response:
            timezone = response['Item'].get('timeZone')
            if timezone:
                logger.debug(f"Found timezone {timezone} for system {pv_system_id}")
                return timezone
            else:
                logger.warning(f"No timezone found in profile for system {pv_system_id}")
                return None
        else:
            logger.warning(f"No profile found for system {pv_system_id}")
            return None
            
    except Exception as e:
        logger.error(f"Error getting timezone for system {pv_system_id}: {str(e)}")
        return None

def is_moon_time(sunrise_str: str, sunset_str: str, system_timezone: Optional[str] = None) -> bool:
    """Check if current time in system timezone is AFTER 1 hour before sunset OR BEFORE 1 hour after sunrise
    
    Moon time logic:
    - Moon time starts 1 hour BEFORE sunset
    - Moon time ends 1 hour AFTER sunrise
    This covers evening/night/early morning hours
    
    Args:
        sunrise_str: Sunrise time in "HH:MM AM/PM" format (in system timezone)
        sunset_str: Sunset time in "HH:MM AM/PM" format (in system timezone)
        system_timezone: Timezone string like "America/New_York" or "America/Chicago"
    """
    try:
        # Parse sunrise and sunset times (format: "05:22 AM" or "08:50 PM")
        sunrise_time = datetime.strptime(sunrise_str, "%I:%M %p")
        sunset_time = datetime.strptime(sunset_str, "%I:%M %p")
        
        # Moon time boundaries
        moon_start = (sunset_time - timedelta(hours=1)).time()  # 1 hour before sunset
        moon_end = (sunrise_time + timedelta(hours=1)).time()   # 1 hour after sunrise
        
        # Get current time in the system's timezone
        if system_timezone:
            try:
                # Manual timezone conversion for the two supported timezones
                utc_now = datetime.utcnow()
                is_dst = is_daylight_saving_time(utc_now)
                
                if system_timezone == "America/New_York":
                    # Eastern Time: UTC-5 (EST) or UTC-4 (EDT)
                    offset_hours = 4 if is_dst else 5
                    current_time_in_system_tz = (utc_now - timedelta(hours=offset_hours)).time()
                    tz_name = "EDT" if is_dst else "EST"
                    logger.debug(f"Current time in {system_timezone} ({tz_name}): {current_time_in_system_tz}")
                elif system_timezone == "America/Chicago":
                    # Central Time: UTC-6 (CST) or UTC-5 (CDT)
                    offset_hours = 5 if is_dst else 6
                    current_time_in_system_tz = (utc_now - timedelta(hours=offset_hours)).time()
                    tz_name = "CDT" if is_dst else "CST"
                    logger.debug(f"Current time in {system_timezone} ({tz_name}): {current_time_in_system_tz}")
                else:
                    logger.warning(f"Unknown timezone {system_timezone}, using server local time")
                    current_time_in_system_tz = datetime.now().time()
                    
            except Exception as tz_error:
                logger.warning(f"Error converting to timezone {system_timezone}: {tz_error}. Using server local time.")
                current_time_in_system_tz = datetime.now().time()
        else:
            # Fallback to server local time if no timezone provided
            logger.warning("No system timezone provided, using server local time")
            current_time_in_system_tz = datetime.now().time()
        
        # Check if current time is in moon time period
        # Moon time is AFTER (sunset-1h) OR BEFORE (sunrise+1h)
        # This typically crosses midnight (e.g., after 5 PM or before 7 AM)
        is_moon = current_time_in_system_tz >= moon_start or current_time_in_system_tz <= moon_end
        
        logger.info(f"Moon time check for timezone {system_timezone}: {current_time_in_system_tz} after {moon_start} (sunset-1h) OR before {moon_end} (sunrise+1h) = {is_moon}")
        
        logger.debug(f"Moon time boundaries: starts at {moon_start} (sunset-1h), ends at {moon_end} (sunrise+1h)")
        logger.debug(f"Original times: sunrise={sunrise_str}, sunset={sunset_str}")
        return is_moon
        
    except Exception as e:
        logger.error(f"Error checking moon time with sunrise={sunrise_str}, sunset={sunset_str}, timezone={system_timezone}: {str(e)}")
        return False  # Default to daylight time if there's an error



def get_all_inverters() -> List[InverterMetadata]:
    """Get all inverters from DynamoDB"""
    try:
        # Query for all inverter status records (PK begins with "Inverter#" and SK = "STATUS")
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('PK').begins_with('Inverter#') & 
                           boto3.dynamodb.conditions.Attr('SK').eq('STATUS')
        )
        
        inverters = []
        for item in response.get('Items', []):
            # Extract device ID from PK (remove "Inverter<" prefix and ">" suffix)
            device_id = item.get('device_id', '')
            pv_system_id = item.get('pvSystemId', '')
            
            if device_id and pv_system_id:
                inverters.append(InverterMetadata(
                    pv_system_id=pv_system_id,
                    device_id=device_id
                ))
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey'],
                FilterExpression=boto3.dynamodb.conditions.Attr('PK').begins_with('Inverter#') & 
                            boto3.dynamodb.conditions.Attr('SK').eq('STATUS')
            )
            for item in response.get('Items', []):
                # Extract device ID from PK (remove "Inverter<" prefix and ">" suffix)
                device_id = item.get('device_id', '')
                pv_system_id = item.get('pvSystemId', '')
                
                if device_id and pv_system_id:
                    inverters.append(InverterMetadata(
                        pv_system_id=pv_system_id,
                        device_id=device_id
                    ))
       
        
        logger.info(f"Found {len(inverters)} inverters from DynamoDB")
        return inverters
        
    except Exception as e:
        logger.error(f"Failed to get inverters from DynamoDB: {str(e)}")
        return []

def get_device_flowdata(pv_system_id: str, device_id: str) -> Dict[str, Any]:
    """Get current power and online status for a device with retry logic for null data"""
    try:
        endpoint = f'pvsystems/{pv_system_id}/devices/{device_id}/flowdata'
        max_attempts = 5
        
        for attempt in range(1, max_attempts + 1):
            # Make API call
            if attempt == 1:
                logger.info(f"Making initial API call for device {device_id}")
                response = api_request(endpoint)
                logger.info(f"FLOW DATA FOR DEVICE {device_id}")
            else:
                logger.info(f"Making retry API call {attempt - 1} for device {device_id}")
                response = api_request(endpoint)
                logger.info(f"FLOW DATA RETRY {attempt - 1} FOR DEVICE {device_id}")
            
            # Log the API response
            logger.info(f"Complete API Response: {json.dumps(response, indent=2, default=str)}")
            
            # Check if we need to retry
            status_data = response.get('status') if response else None
            is_online = status_data.get('isOnline', False) if status_data else False
            data_section = response.get('data') if response else None
            
            # If device is online but data is null, retry (unless this is the last attempt)
            if is_online and data_section is None and attempt < max_attempts:
                logger.info(f"Device {device_id} is online but data is null, retrying in 1 second (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            else:
                # Either we have valid data, device is offline, or we've exhausted retries
                if is_online and data_section is not None:
                    logger.info(f"Got valid data on attempt {attempt} for device {device_id}")
                elif is_online and data_section is None:
                    logger.info(f"Device {device_id} still has null data after {max_attempts} attempts, treating as offline")
                else:
                    logger.info(f"Device {device_id} is offline (isOnline: {is_online}) on attempt {attempt}")
                
                return response
        
        # This shouldn't be reached, but just in case
        logger.warning(f"Unexpected end of retry loop for device {device_id}")
        return {}
        
    except Exception as e:
        logger.error(f"Failed to get flowdata for device {device_id} in system {pv_system_id}: {str(e)}")
        return {}

def get_device_messages(pv_system_id: str, device_id: str, from_timestamp: str) -> Dict[str, Any]:
    """Get error messages for a device from a specific timestamp"""
    try:
        endpoint = f'pvsystems/{pv_system_id}/devices/{device_id}/messages'
        params = {
            'from': from_timestamp,
            'statetype': 'Error',
            'stateseverity': 'Error'
        }
        response = api_request(endpoint, params=params)
        return response
    except Exception as e:
        logger.error(f"Failed to get messages for device {device_id} in system {pv_system_id}: {str(e)}")
        return {}

def get_device_status_from_db(device_id: str) -> Dict[str, Any]:
    """Get existing device status from DynamoDB"""
    try:
        response = table.get_item(
            Key={
                'PK': f'Inverter#{device_id}',
                'SK': 'STATUS'
            }
        )
        
        if 'Item' in response:
            return response['Item']
        else:
            return {
                'status': 'green',
                'reason': '',
                'lastUpdated': None,
                'lastStatusChangeTime': None,
                'power': 0
            }
            
    except Exception as e:
        logger.error(f"Error getting device status for {device_id}: {str(e)}")
        return {
            'status': 'green',
            'reason': '',
            'lastUpdated': None,
            'lastStatusChangeTime': None,
            'power': 0
        }

def update_device_status_in_db(device_id: str, pv_system_id: str, status: str, power: float, reason: str, status_changed: bool = False) -> bool:
    """Update device status in DynamoDB"""
    try:
        now = datetime.utcnow().isoformat()
        
        # Get existing record to preserve lastStatusChangeTime if status didn't change
        if not status_changed:
            existing_record = get_device_status_from_db(device_id)
            last_status_change_time = existing_record.get('lastStatusChangeTime', now)
        else:
            last_status_change_time = now
        
        status_item = {
            'PK': f'Inverter#{device_id}',
            'SK': 'STATUS',
            'pvSystemId': pv_system_id,
            'device_id': device_id,
            'status': status,
            'reason': reason,
            'power': Decimal(str(power)),  # Convert float to Decimal for DynamoDB
            'lastStatusChangeTime': last_status_change_time,  # Only update if status actually changed
            'lastUpdated': now  # Always update this timestamp
        }
        
        table.put_item(Item=status_item)
        if status_changed:
            logger.info(f"✅ Status changed for device {device_id} to {status} (reason: {reason})")
        else:
            logger.info(f"✅ Updated device {device_id} data (status remains {status})")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error updating status for device {device_id}: {str(e)}")
        return False

def send_device_status_change_sns(device_id: str, pv_system_id: str, new_status: str, previous_status: str, power: float, new_reason: str, previous_reason: str, sunrise_time: str = None, sunset_time: str = None, timezone: str = None, flow_data: Dict[str, Any] = None) -> bool:
    """Send SNS message for device status change"""
    try:
        message = {
            "deviceId": device_id,
            "pvSystemId": pv_system_id,
            "newStatus": new_status,
            "previousStatus": previous_status,
            "newReason": new_reason,
            "previousReason": previous_reason,
            "timestamp": datetime.utcnow().isoformat(),
            "power": power,
            "sunrise_time": sunrise_time,
            "sunset_time": sunset_time,
            "timezone": timezone,
            "flow_data": flow_data,
            "type": "Status Changed"
        }
        
        # Build message attributes
        message_attributes = {
            'source': {
                'DataType': 'String',
                'StringValue': 'device-status-polling-script'
            },
            'deviceId': {
                'DataType': 'String',
                'StringValue': device_id
            },
            'systemId': {
                'DataType': 'String',
                'StringValue': pv_system_id
            },
            'statusChange': {
                'DataType': 'String',
                'StringValue': f'{previous_status}-{new_status}'
            }
        }
        
        # Add sunrise_time if provided
        if sunrise_time:
            message_attributes['sunrise_time'] = {
                'DataType': 'String',
                'StringValue': sunrise_time
            }
        
        # Add sunset_time if provided
        if sunset_time:
            message_attributes['sunset_time'] = {
                'DataType': 'String',
                'StringValue': sunset_time
            }
        
        # Add timezone if provided
        if timezone:
            message_attributes['timezone'] = {
                'DataType': 'String',
                'StringValue': timezone
            }
        
        # Add flow_data if provided
        if flow_data:
            message_attributes['flow_data'] = {
                'DataType': 'String',
                'StringValue': json.dumps(flow_data, default=str)
            }
        
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"Solar Inverter Status Change - {device_id}",
            Message=json.dumps(message),
            MessageAttributes=message_attributes
        )
        
        logger.info(f"✅ Sent SNS status change notification for device {device_id}: {previous_status} → {new_status} (reason: {new_reason}). Message ID: {response['MessageId']}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error sending SNS message for device {device_id}: {str(e)}")
        return False

def process_device_status(inverter: InverterMetadata, target_date: datetime, stats: Dict[str, int]) -> bool:
    """Process status for a single inverter device - New Workflow Logic"""
    try:
        logger.info(f"Processing status for device: {inverter.device_id} (System: {inverter.pv_system_id})")
        
        # Get current device status from DynamoDB
        current_status_data = get_device_status_from_db(inverter.device_id)
        current_status = current_status_data.get('status', 'green')
        current_reason = current_status_data.get('reason', '')
        last_updated = current_status_data.get('lastUpdated')
        
        # Get flowdata to check online status and power
        flowdata = get_device_flowdata(inverter.pv_system_id, inverter.device_id)
        
        # Determine power - treat offline devices as no power
        power = 0.0
        if flowdata:
            status_data = flowdata.get('status', {})
            is_online = status_data.get('isOnline', False)
            
            if is_online:
                # Device is online, get power from data channels
                data_section = flowdata.get('data')
                
                if data_section is not None:
                    # Data section exists, extract power from channels
                    channels = data_section.get('channels', [])
                    
                    for channel in channels:
                        channel_name = channel.get('channelName', '')
                        if (channel_name in ['PowerPV', 'PowerOutput', 'Power'] and 
                            channel.get('value') is not None):
                            power = float(channel['value'])
                            logger.debug(f"Found power value {power}W in channel '{channel_name}' for device {inverter.device_id}")
                            break
                    
                    logger.info(f"Device {inverter.device_id} is online with power: {power}W")
                else:
                    # Data section is null - treat as online with 0 power
                    power = 0.0
                    logger.info(f"Device {inverter.device_id} is online but data is null - treating as 0 power")
            else:
                # Device is offline - treat as no power
                power = 0.0
                logger.info(f"Device {inverter.device_id} is offline - treating as no power")
        else:
            # No flowdata - treat as no power
            power = 0.0
            logger.info(f"Device {inverter.device_id} has no flowdata - treating as no power")
        
        # Get sunrise/sunset data for daylight hours check
        sun_data = get_sunrise_sunset_data(inverter.pv_system_id, target_date)
        logger.info(f"Sun data: {sun_data}")
        if sun_data == {}:
            sun_data = get_suntimes(inverter.pv_system_id, target_date)
            logger.info(f"New Sun data: {sun_data}")
        
        # Get system timezone for accurate time comparison
        system_timezone = get_system_timezone(inverter.pv_system_id)
        if system_timezone:
            logger.info(f"Using system timezone {system_timezone} for device {inverter.device_id}")
        else:
            logger.warning(f"No timezone found for system {inverter.pv_system_id}, using server local time")
        
        # Determine if we're within moon time (1 hour before sunset to 1 hour after sunrise)
        is_moon_time_period = False
        if sun_data and 'sunrise' in sun_data and 'sunset' in sun_data:
            is_moon_time_period = is_moon_time(sun_data['sunrise'], sun_data['sunset'], system_timezone)
        
        # Apply new workflow logic
        new_status = current_status
        new_reason = current_reason
        
        if is_moon_time_period:
            # Moon time logic (1 hour before sunset to 1 hour after sunrise)
            logger.info(f"Device {inverter.device_id} is within moon time")
            
            if current_status == 'red' and power == 0:
                # Red + no power → keep Red
                new_status = 'red'
                logger.info(f"Device {inverter.device_id}: Red + no power → keeping Red")
            elif current_status == 'red' and power > 0:
                # Red + power → set to Green
                new_status = 'green'
                new_reason = ''
                logger.info(f"Device {inverter.device_id}: Red + power → setting to Green")
            elif current_status == 'Moon' and power == 0:
                # Moon + no power → keep Moon
                new_status = 'Moon'
                logger.info(f"Device {inverter.device_id}: Moon + no power → keeping Moon")
            elif current_status == 'Moon' and power > 0:
                # Moon + power → set to Green
                new_status = 'green'
                new_reason = ''
                logger.info(f"Device {inverter.device_id}: Moon + power → setting to Green")
            elif current_status == 'green' and power > 0:
                # Green + power → keep Green
                new_status = 'green'
                logger.info(f"Device {inverter.device_id}: Green + power → keeping Green")
            elif current_status == 'green' and power == 0:
                # Green + no power → set to Moon
                new_status = 'Moon'
                new_reason = 'no production during moon time'
                logger.info(f"Device {inverter.device_id}: Green + no power → setting to Moon")
            
        else:
            # Daylight time logic (1 hour after sunrise to 1 hour before sunset)
            logger.info(f"Device {inverter.device_id} is within daylight time")
            
            if power > 0:
                # Power > 0 → set to Green (clear all errors)
                new_status = 'green'
                new_reason = ''
                logger.info(f"Device {inverter.device_id}: Power > 0 → setting to Green")
            else:
                # Power = 0 → check for red error codes
                logger.info(f"Device {inverter.device_id}: Power = 0 → checking for red errors")
                
                # Determine timestamp to check messages from
                if last_updated:
                    try:
                        dt = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                        from_timestamp = dt.strftime("%Y%m%dT%H%M%S") + "Z"
                    except Exception as e:
                        logger.warning(f"Error parsing lastUpdated timestamp: {e}")
                        from_timestamp = target_date.strftime("%Y%m%dT%H%M%S") + "Z"
                else:
                    from_timestamp = target_date.strftime("%Y%m%dT%H%M%S") + "Z"
                
                # Get error messages
                messages_response = get_device_messages(inverter.pv_system_id, inverter.device_id, from_timestamp)
                
                # Check for red error codes
                red_error_code = None
                if messages_response and 'messages' in messages_response and messages_response['messages']:
                    color_map = get_all_error_codes_from_supabase()
                    
                    for msg in messages_response['messages']:
                        error_code = msg['stateCode']
                        if color_map.get(error_code) == 'red':
                            red_error_code = error_code
                            logger.info(f"Found red error {error_code} for device {inverter.device_id}")
                            break
                
                # Set status based on error check
                if red_error_code:
                    new_status = 'red'
                    new_reason = f'error code: {red_error_code}'
                    logger.info(f"Device {inverter.device_id}: Red error found → setting to Red with reason: {new_reason}")
                else:
                    new_status = 'red'
                    new_reason = 'no production'
                    logger.info(f"Device {inverter.device_id}: No red errors → setting to Red with reason: no production")
        
        # Update status counters
        if new_status == 'green':
            update_stats_thread_safe(stats, 'green_devices')
        elif new_status == 'red':
            update_stats_thread_safe(stats, 'red_devices')
        elif new_status == 'Moon':
            update_stats_thread_safe(stats, 'moon_devices')
        
        # Check if status or reason changed
        status_changed = (new_status != current_status) or (new_reason != current_reason)
        
        if status_changed:
            # Update DynamoDB
            update_success = update_device_status_in_db(
                inverter.device_id, inverter.pv_system_id, new_status, power, new_reason, status_changed=True
            )
            
            if update_success:
                # Send SNS notification
                sns_success = send_device_status_change_sns(
                    inverter.device_id, inverter.pv_system_id, new_status, current_status, power, new_reason, current_reason,
                    sunrise_time=sun_data.get('sunrise') if sun_data else None,
                    sunset_time=sun_data.get('sunset') if sun_data else None,
                    timezone=system_timezone,
                    flow_data=flowdata
                )
                
                if sns_success:
                    update_stats_thread_safe(stats, 'status_changes')
                    logger.info(f"✅ Status change processed for device {inverter.device_id}: {current_status} → {new_status}")
                
                return sns_success
            else:
                return False
        else:
            # No status change, but still update lastUpdated timestamp and power
            update_device_status_in_db(inverter.device_id, inverter.pv_system_id, new_status, power, new_reason, status_changed=False)
            logger.info(f"No status change for device {inverter.device_id} (remains {current_status})")
            return True
        
    except Exception as e:
        logger.error(f"❌ Error processing device {inverter.device_id}: {str(e)}")
        return False

def update_stats_thread_safe(stats, key, increment=1):
    """Thread-safe stats update"""
    with stats_lock:
        stats[key] += increment

def enforce_weatherapi_rate_limit():
    """Enforce WeatherAPI rate limit of 50 calls per minute"""
    with weatherapi_rate_limiter['lock']:
        now = time.time()
        one_minute_ago = now - 60
        
        # Remove calls older than 1 minute
        weatherapi_rate_limiter['calls'] = [
            call_time for call_time in weatherapi_rate_limiter['calls'] 
            if call_time > one_minute_ago
        ]
        
        # Check if we're at the limit (use 50 to be safe)
        if len(weatherapi_rate_limiter['calls']) >= 50:
            # Find the oldest call in the current minute
            oldest_call = min(weatherapi_rate_limiter['calls'])
            wait_time = oldest_call + 60 - now
            
            if wait_time > 0:
                logger.info(f"WeatherAPI rate limit reached. Waiting {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                
                # Clean up the list again after waiting
                now = time.time()
                one_minute_ago = now - 60
                weatherapi_rate_limiter['calls'] = [
                    call_time for call_time in weatherapi_rate_limiter['calls'] 
                    if call_time > one_minute_ago
                ]
        
        # Record this call
        weatherapi_rate_limiter['calls'].append(now)
        logger.debug(f"WeatherAPI calls in last minute: {len(weatherapi_rate_limiter['calls'])}/50")

def process_devices_concurrently():
    """Main function to process all devices for status"""
    start_time = time.time()
    utc_now = datetime.utcnow()
    today = utc_now - timedelta(hours=5)  # EST timezone
    
    stats = {
        'devices_processed': 0,
        'status_changes': 0,
        'errors': 0,
        'api_calls_made': 0,
        'green_devices': 0,
        'red_devices': 0,
        'moon_devices': 0
    }
    
    try:
        # Fetch JWT token
        logger.info("Fetching JWT token...")
        get_jwt_token()
        
        # Pre-load all error codes from Supabase for efficiency
        logger.info("Pre-loading error codes from Supabase...")
        error_codes_loaded = get_all_error_codes_from_supabase()
        logger.info(f"Pre-loaded {len(error_codes_loaded)} error codes for efficient lookup")
        
        # Get all inverters from DynamoDB
        logger.info("Fetching inverters list from DynamoDB...")
        inverters = get_all_inverters()
        
        if not inverters:
            logger.warning("No inverters found")
            return stats
        
        logger.info(f"Found {len(inverters)} inverters. Starting status processing...")
        
        # Process devices in batches
        batch_size = 32
        total_batches = (len(inverters) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(inverters))
            batch_inverters = inverters[start_idx:end_idx]
            
            logger.info(f"Processing batch {batch_num + 1}/{total_batches}: devices {start_idx + 1}-{end_idx}")
            
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_to_inverter = {
                    executor.submit(process_device_status, inverter, today, stats): inverter 
                    for inverter in batch_inverters
                }
                
                for future in as_completed(future_to_inverter):
                    inverter = future_to_inverter[future]
                    try:
                        success = future.result()
                        
                        update_stats_thread_safe(stats, 'devices_processed')
                        update_stats_thread_safe(stats, 'api_calls_made', 2)  # flowdata + messages
                        
                        if not success:
                            update_stats_thread_safe(stats, 'errors')
                        
                    except Exception as e:
                        logger.error(f"❌ Error processing device {inverter.device_id}: {str(e)}")
                        update_stats_thread_safe(stats, 'errors')
            
            if batch_num < total_batches - 1:
                logger.info(f"Batch {batch_num + 1} completed. Waiting 0.5 seconds before next batch...")
                time.sleep(0.5)
        
        end_time = time.time()
        execution_time = end_time - start_time
        stats['execution_time'] = execution_time
        
        logger.info("=== DEVICE STATUS POLLING COMPLETED ===")
        logger.info(f"⏱️  Total execution time: {execution_time:.2f} seconds")
        logger.info(f"🔧 Devices processed: {stats['devices_processed']}")
        logger.info(f"🔄 Status changes: {stats['status_changes']}")
        logger.info(f"🌐 Total API calls made: {stats['api_calls_made']}")
        logger.info(f"✅ Green devices: {stats['green_devices']}")
        logger.info(f"🔴 Red devices: {stats['red_devices']}")
        logger.info(f"🌙 Moon devices: {stats['moon_devices']}")
        logger.info(f"❌ Errors: {stats['errors']}")
        
        # Print summary counts
        print(f"\n🏁 FINAL DEVICE STATUS SUMMARY:")
        print(f"✅ GREEN: {stats['green_devices']} devices")
        print(f"🔴 RED: {stats['red_devices']} devices") 
        print(f"🌙 MOON: {stats['moon_devices']} devices")
        print(f"📊 Total: {stats['green_devices'] + stats['red_devices'] + stats['moon_devices']} devices")
        print(f"🔄 Status changes: {stats['status_changes']}")
        print("=" * 50)
        
        return stats
        
    except Exception as e:
        logger.error(f"Critical error in device status processing: {str(e)}")
        stats['errors'] += 1
        return stats

def main():
    """Main entry point"""
    try:
        result = process_devices_concurrently()
        return result
        
    except Exception as e:
        logger.error(f"Error in main execution: {str(e)}")
        raise

def lambda_handler(event, context):
    """AWS Lambda handler function"""
    try:
        result = process_devices_concurrently()
        
        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

if __name__ == "__main__":
    # Simulate EventBridge scheduler trigger for lambda_handler
    print("🚀 Simulating EventBridge scheduler trigger for lambda_handler...")
    
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
            "arn:aws:scheduler:us-east-1:123456789012:schedule/default/device-status-polling"
        ],
        "detail": {}
    }
    
    # Create mock context object
    class MockContext:
        def __init__(self):
            self.function_name = "device-status-polling"
            self.function_version = "$LATEST"
            self.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:device-status-polling"
            self.memory_limit_in_mb = "512"
            self.remaining_time_in_millis = lambda: 300000  # 5 minutes
            self.log_group_name = "/aws/lambda/device-status-polling"
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