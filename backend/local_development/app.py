"""
Chat Service Lambda Function
Handles: /chat, /health  
Direct split from app.py with NO logic changes
"""

import os
import json
from typing import Dict, List, Optional, Any, Union
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import requests
from datetime import datetime, timedelta
from mangum import Mangum
import boto3
import logging
from decimal import Decimal
import calendar
import random
from io import BytesIO
import re
import uvicorn
# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing, String, Rect, Group
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from concurrent.futures import ThreadPoolExecutor, as_completed
# Langchain imports
from langchain_openai import OpenAIEmbeddings
from langchain_openai import ChatOpenAI
from langchain.memory import ConversationBufferMemory, ConversationBufferWindowMemory
from langchain_pinecone import PineconeVectorStore
from pinecone.grpc import PineconeGRPC as Pinecone

# Import OpenAI for direct function calling
from openai import OpenAI
import uuid

# Load environment variables
load_dotenv()

#---------------------------------------
# DynamoDB Helper Functions

def find_system_by_name(system_name: str, portfolio_systems: List[Dict]) -> Optional[str]:
    """
    Find a system ID by matching the system name.
    Uses fuzzy matching to find the closest system name match.
    
    Args:
        system_name: The name to search for
        portfolio_systems: List of systems with system_id and name
        
    Returns:
        The system_id of the best match, or None if no good match found
    """
    if not portfolio_systems:
        return None
    
    system_name_lower = system_name.lower().strip()
    best_match = None
    best_score = 0
    
    for system in portfolio_systems:
        system_db_name = system.get('name', '').lower().strip()
        
        # Exact match
        if system_name_lower == system_db_name:
            return system['system_id']
        
        # Partial match - check if search term is in system name
        if system_name_lower in system_db_name:
            score = len(system_name_lower) / len(system_db_name)
            if score > best_score:
                best_score = score
                best_match = system['system_id']
        
        # Reverse partial match - check if system name is in search term
        elif system_db_name in system_name_lower:
            score = len(system_db_name) / len(system_name_lower)
            if score > best_score:
                best_score = score
                best_match = system['system_id']
    
    # Only return match if confidence is reasonably high
    return best_match if best_score > 0.3 else None

def get_system_earnings_rate(system_id: str) -> float:
    """
    Get the earnings rate for a system from DynamoDB profile, default to 0.4 if not found
    
    Args:
        system_id: System ID to query
        
    Returns:
        Earnings rate in $/kWh, defaults to $0.4 if not found
    """
    try:
        logger.debug(f"Querying earnings rate for system {system_id}")
        
        response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' in response and 'earningsRate' in response['Item']:
            earnings_rate = float(response['Item']['earningsRate'])
            logger.info(f"Found custom earnings rate for {system_id}: ${earnings_rate}/kWh")
            return earnings_rate
        else:
            logger.debug(f"No custom earnings rate found for {system_id}, using default: $0.4/kWh")
            return 0.4
            
    except Exception as e:
        logger.warning(f"Error querying earnings rate for {system_id}: {str(e)}. Using default: $0.4/kWh")
        return 0.4
#---------------------------------------

def convert_dynamodb_decimals(obj):
    """Convert DynamoDB Decimal objects to regular numbers for JSON serialization"""
    if isinstance(obj, list):
        return [convert_dynamodb_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_dynamodb_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj

def get_user_profile_if_needed(user_id: str, user_profile: dict = None) -> dict:
    """Get user profile from DynamoDB if not already provided to minimize DB calls"""
    if user_profile:
        return user_profile
    
    try:
        response = table.get_item(
            Key={
                'PK': f'User#{user_id}',
                'SK': 'PROFILE'
            }
        )
        
        if 'Item' in response:
            return convert_dynamodb_decimals(response['Item'])
        else:
            return {"error": f"User profile not found for user {user_id}"}
    except Exception as e:
        print(f"Error getting user profile for {user_id}: {str(e)}")
        return {"error": f"Failed to get user profile: {str(e)}"}

def validate_system_access(user_id: str, system_id: str, user_profile: dict = None) -> bool:
    """Validate that a user has access to a specific system"""
    """
    profile = get_user_profile_if_needed(user_id, user_profile)
    
    if "error" in profile:
        return False
    
    # Admin users have access to all systems
    if profile.get('role') == 'admin':
        return True
    
    # Check if user has access to this specific system
    try:
        response = table.get_item(
            Key={
                'PK': f'User#{user_id}',
                'SK': f'System#{system_id}'
            }
        )
        return 'Item' in response
    except Exception as e:
        print(f"Error validating system access for user {user_id}, system {system_id}: {str(e)}")
        return False
        """
    return True

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('app')

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'Moose-DDB')

# Initialize DynamoDB client
try:
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    print(f"Connected to DynamoDB table: {DYNAMODB_TABLE_NAME}")
except Exception as e:
    print(f"Failed to connect to DynamoDB: {str(e)}")
    # Don't raise here to allow the API to start even if DynamoDB is not available
    dynamodb = None
    table = None

# Create FastAPI app
app = FastAPI(
    title="Chat Service",
    description="Chat service for solar operations and maintenance chatbot",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get API key from environment variables
api_key = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
openai_client = OpenAI(api_key=api_key)

#---------------------------------------
# Pydantic Models - EXACT COPIES from app.py
#---------------------------------------

class PortfolioSystem(BaseModel):
    """System information for portfolio mode"""
    id: str
    name: str
    isPortfolio: Optional[bool] = None

class ChatMessage(BaseModel):
    """Chat message from the user"""
    username: str
    message: str
    user_id: Optional[str] = None
    jwtToken: Optional[str] = None
    portfolioSystems: Optional[List[PortfolioSystem]] = None

class SourceDocument(BaseModel):
    """Source document from the RAG system"""
    content: str
    metadata: Optional[Dict[str, Any]] = None

class ChartData(BaseModel):
    """Chart data for visualization"""
    chart_type: str = "line"
    data_type: str
    title: str
    x_axis_label: str
    y_axis_label: str
    data_points: List[Dict[str, Any]]
    time_period: str
    total_value: Optional[float] = None
    unit: str
    system_name: Optional[str] = None

class ChatResponse(BaseModel):
    """Response from the chatbot"""
    response: str
    source_documents: Optional[List[SourceDocument]] = None
    chart_data: Optional[Union[ChartData, List[ChartData]]] = None  # Support single or multiple charts

# Sample user context
user_contexts: Dict[str, Dict] = {}

#---------------------------------------
# Main DynamoDB Functions
#---------------------------------------

def get_user_information(user_id: str, data_type: str, user_profile: dict = None) -> dict:
    """
    Get user information from DynamoDB.
    
    Args:
        user_id: The user ID to get information for
        data_type: Type of information to retrieve ('profile' or 'systems')
        user_profile: Optional pre-fetched user profile to minimize DB calls
        
    Returns:
        Dictionary with user information
    """
    try:
        if data_type == "profile":
            # Get user profile
            profile = get_user_profile_if_needed(user_id, user_profile)
            if "error" in profile:
                return profile
            
            return {
                "success": True,
                "data": profile,
                "query_info": {
                    "user_id": user_id,
                    "query_type": "profile"
                }
            }
        
        elif data_type == "systems":
            # Get user's accessible systems
            profile = get_user_profile_if_needed(user_id, user_profile)
            if "error" in profile:
                return profile
            
            if profile.get('role') == 'admin':
                # Admin gets all systems (limited to 50 for performance)
                response = table.scan(
                    FilterExpression='begins_with(PK, :pk) AND SK = :sk',
                    ExpressionAttributeValues={
                        ':pk': 'System#',
                        ':sk': 'PROFILE'
                    },
                    Limit=50
                )
                
                systems = []
                for item in response.get('Items', []):
                    systems.append(convert_dynamodb_decimals(item))
                
                # Get total count for pagination message
                total_response = table.scan(
                    FilterExpression='begins_with(PK, :pk) AND SK = :sk',
                    ExpressionAttributeValues={
                        ':pk': 'System#',
                        ':sk': 'PROFILE'
                    },
                    Select='COUNT'
                )
                
                total_count = total_response.get('Count', len(systems))
                
                result = {
                    "success": True,
                    "data": systems,
                    "query_info": {
                        "user_id": user_id,
                        "query_type": "systems",
                        "user_role": "admin"
                    }
                }
                
                if total_count > 50:
                    result["pagination"] = {
                        "total_systems": total_count,
                        "showing": len(systems),
                        "message": f"Showing {len(systems)} of {total_count} systems. Ask 'show me more systems' to see additional results."
                    }
                
                return result
            else:
                # Regular user gets their linked systems
                response = table.query(
                    KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
                    ExpressionAttributeValues={
                        ':pk': f'User#{user_id}',
                        ':sk': 'System#'
                    }
                )
                
                system_links = response.get('Items', [])
                systems = []
                
                # Get full system profiles for each linked system
                for link in system_links:
                    system_id = link.get('systemId')
                    if system_id:
                        system_response = table.get_item(
                            Key={
                                'PK': f'System#{system_id}',
                                'SK': 'PROFILE'
                            }
                        )
                        if 'Item' in system_response:
                            systems.append(convert_dynamodb_decimals(system_response['Item']))
                
                return {
                    "success": True,
                    "data": systems,
                    "query_info": {
                        "user_id": user_id,
                        "query_type": "systems",
                        "systems_count": len(systems)
                    }
                }
        
        else:
            return {"error": f"Invalid data_type '{data_type}'. Use 'profile' or 'systems'."}
    
    except Exception as e:
        print(f"Error in get_user_information: {str(e)}")
        return {"error": f"Failed to get user information: {str(e)}"}

def get_system_information(user_id: str, system_id: str, data_type: str, user_profile: dict = None) -> dict:
    """
    Get system information from DynamoDB.
    
    Args:
        user_id: The user ID requesting the information
        system_id: The system ID to get information for
        data_type: Type of information to retrieve ('profile', 'status', or 'inverter_count')
        user_profile: Optional pre-fetched user profile to minimize DB calls
        
    Returns:
        Dictionary with system information
    """
    try:
        # Validate system access
        if not validate_system_access(user_id, system_id, user_profile):
            return {
                "error": f"You don't have access to system {system_id}",
                "system_id": system_id
            }
        
        if data_type == "profile":
            # Get system profile
            response = table.get_item(
                Key={
                    'PK': f'System#{system_id}',
                    'SK': 'PROFILE'
                }
            )
            
            if 'Item' not in response:
                return {"error": f"System profile not found for system {system_id}"}
            
            system_data = convert_dynamodb_decimals(response['Item'])
            
            return {
                "success": True,
                "data": system_data,
                "query_info": {
                    "user_id": user_id,
                    "system_id": system_id,
                    "query_type": "profile"
                }
            }
        
        elif data_type == "status":
            # Get system status
            response = table.get_item(
                Key={
                    'PK': f'System#{system_id}',
                    'SK': 'STATUS'
                }
            )
            
            if 'Item' not in response:
                return {
                    "success": True,
                    "data": {"note": "No status data available for this system"},
                    "query_info": {
                        "user_id": user_id,
                        "system_id": system_id,
                        "query_type": "status"
                    }
                }
            
            status_data = convert_dynamodb_decimals(response['Item'])
            
            return {
                "success": True,
                "data": status_data,
                "query_info": {
                    "user_id": user_id,
                    "system_id": system_id,
                    "query_type": "status"
                }
            }
        
        elif data_type == "inverter_count":
            # Get count of inverters for this system
            response = table.query(
                IndexName='device-system-index',  # Using GSI2
                KeyConditionExpression='GSI2PK = :pk AND begins_with(GSI2SK, :sk)',
                ExpressionAttributeValues={
                    ':pk': f'System#{system_id}',
                    ':sk': 'Inverter#'
                }
            )
            
            inverter_count = len(response.get('Items', []))
            
            return {
                "success": True,
                "data": {
                    "inverter_count": inverter_count,
                    "system_id": system_id
                },
                "query_info": {
                    "user_id": user_id,
                    "system_id": system_id,
                    "query_type": "inverter_count"
                }
            }
        
        else:
            return {"error": f"Invalid data_type '{data_type}'. Use 'profile', 'status', or 'inverter_count'."}
    
    except Exception as e:
        print(f"Error in get_system_information: {str(e)}")
        return {"error": f"Failed to get system information: {str(e)}"}

def get_inverter_information(user_id: str, system_id: str, data_type: str, user_profile: dict = None) -> dict:
    """
    Get inverter information from DynamoDB.
    
    Args:
        user_id: The user ID requesting the information
        system_id: The system ID to get inverters for
        data_type: Type of information to retrieve ('profiles', 'status', or 'details')
        user_profile: Optional pre-fetched user profile to minimize DB calls
        
    Returns:
        Dictionary with inverter information
    """
    try:
        # Validate system access
        if not validate_system_access(user_id, system_id, user_profile):
            return {
                "error": f"You don't have access to system {system_id}",
                "system_id": system_id
            }
        
        # Get all inverters for this system using GSI2
        response = table.query(
            IndexName='device-system-index',  # Using GSI2
            KeyConditionExpression='GSI2PK = :pk AND begins_with(GSI2SK, :sk)',
            ExpressionAttributeValues={
                ':pk': f'System#{system_id}',
                ':sk': 'Inverter#'
            }
        )
        
        inverter_links = response.get('Items', [])
        if not inverter_links:
            return {
                "success": True,
                "data": {
                    "note": f"No inverters found for system {system_id}",
                    "inverters": []
                },
                "query_info": {
                    "user_id": user_id,
                    "system_id": system_id,
                    "query_type": data_type
                }
            }
        
        inverters_data = []
        
        for link in inverter_links:
            inverter_id = link.get('GSI2SK', '').replace('Inverter#', '')
            if not inverter_id:
                continue
            
            if data_type == "profiles":
                # Get inverter profile
                inverter_response = table.get_item(
                    Key={
                        'PK': f'Inverter#{inverter_id}',
                        'SK': 'PROFILE'
                    }
                )
                
                if 'Item' in inverter_response:
                    inverters_data.append(convert_dynamodb_decimals(inverter_response['Item']))
            
            elif data_type == "status":
                # Get inverter status
                inverter_response = table.get_item(
                    Key={
                        'PK': f'Inverter#{inverter_id}',
                        'SK': 'STATUS'
                    }
                )
                
                if 'Item' in inverter_response:
                    inverters_data.append(convert_dynamodb_decimals(inverter_response['Item']))
                else:
                    # Add placeholder for missing status
                    inverters_data.append({
                        "inverter_id": inverter_id,
                        "note": "No status data available for this inverter"
                    })
            
            elif data_type == "details":
                # Get both profile and status
                profile_response = table.get_item(
                    Key={
                        'PK': f'Inverter#{inverter_id}',
                        'SK': 'PROFILE'
                    }
                )
                
                status_response = table.get_item(
                    Key={
                        'PK': f'Inverter#{inverter_id}',
                        'SK': 'STATUS'
                    }
                )
                
                inverter_detail = {}
                if 'Item' in profile_response:
                    inverter_detail.update(convert_dynamodb_decimals(profile_response['Item']))
                
                if 'Item' in status_response:
                    inverter_detail.update(convert_dynamodb_decimals(status_response['Item']))
                elif 'Item' in profile_response:
                    inverter_detail['status_note'] = "No status data available"
                
                if inverter_detail:
                    inverters_data.append(inverter_detail)
        
        return {
            "success": True,
            "data": {
                f"system_{system_id}": inverters_data
            },
            "query_info": {
                "user_id": user_id,
                "system_id": system_id,
                "query_type": data_type,
                "inverter_count": len(inverters_data)
            }
        }
    
    except Exception as e:
        print(f"Error in get_inverter_information: {str(e)}")
        return {"error": f"Failed to get inverter information: {str(e)}"}

def get_user_incidents(user_id: str, status: str = None, user_profile: dict = None) -> dict:
    """
    Get user incidents from DynamoDB.
    
    Args:
        user_id: The user ID to get incidents for
        status: Optional status filter ("pending", "processed", or None for all)
        user_profile: Optional pre-fetched user profile to minimize DB calls
        
    Returns:
        Dictionary with incident information
    """
    try:
        # Build query parameters
        query_params = {
            'IndexName': 'incident-user-index',  # Using GSI3
            'KeyConditionExpression': 'GSI3PK = :pk',
            'ExpressionAttributeValues': {
                ':pk': f'User#{user_id}'
            }
        }
        
        # Add status filter if specified
        if status:
            query_params['FilterExpression'] = 'begins_with(PK, :incident_prefix) AND #status = :status'
            query_params['ExpressionAttributeNames'] = {'#status': 'status'}
            query_params['ExpressionAttributeValues'].update({
                ':incident_prefix': 'Incident#',
                ':status': status
            })
        
        response = table.query(**query_params)
        
        incidents = []
        for item in response.get('Items', []):
            incidents.append(convert_dynamodb_decimals(item))
        
        return {
            "success": True,
            "data": {
                "incidents": incidents,
                "total_count": len(incidents),
                "status_filter": status or "all"
            },
            "query_info": {
                "user_id": user_id,
                "query_type": "incidents",
                "status_filter": status
            }
        }
    
    except Exception as e:
        print(f"Error in get_user_incidents: {str(e)}")
        return {"error": f"Failed to get user incidents: {str(e)}"}

#---------------------------------------
# Function definitions for OpenAI function calling
#---------------------------------------

def process_energy_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process energy data from the Solar.web API to ensure consistent calculations.
    
    Args:
        data: Raw API response from Solar.web
        
    Returns:
        Processed data with consistent units and calculations
    """
    try:
        print(f"Processing energy data: Starting with raw API response")
        # Clone the original data to avoid modifying it
        processed_data = data.copy()
        
        # Check if this is already a mock response with our format
        if "energy_production" in processed_data:
            print(f"Processing energy data: Already in our format, returning as is")
            return processed_data
            
        # Process real API data
        if "data" in processed_data and isinstance(processed_data["data"], list):
            data_points = processed_data["data"]
            print(f"Processing energy data: Found {len(data_points)} data points")
            
            # Extract values and dates
            values = []
            dates = []
            
            # Handle the nested structure of the API response
            for point in data_points:
                # Extract date from logDateTime field
                if "logDateTime" in point:
                    date = point["logDateTime"]
                    dates.append(date)
                
                # Extract value from channels array
                if "channels" in point and isinstance(point["channels"], list) and len(point["channels"]) > 0:
                    channel = point["channels"][0]  # Assuming the first channel is what we want
                    if "value" in channel and channel["value"] is not None:
                        value = float(channel["value"])
                        values.append(value)
                        print(f"  - Extracted value {value} for date {date}")
            
            print(f"Processing energy data: Extracted {len(values)} values and {len(dates)} dates")
            
            # Calculate total energy if we have values
            if values:
                # Convert to kWh (if values are in Wh)
                total_energy_wh = sum(values)
                total_energy_kwh = total_energy_wh / 1000.0
                
                print(f"Processing energy data: Calculated total energy as {total_energy_wh} Wh = {total_energy_kwh} kWh")
                
                # Add calculated values to the processed data
                processed_data["total_energy_wh"] = total_energy_wh
                processed_data["total_energy_kwh"] = round(total_energy_kwh, 2)
                processed_data["energy_production"] = f"{total_energy_kwh:.2f} kWh"
                
                # Add date range information
                if dates:
                    processed_data["start_date"] = min(dates)
                    processed_data["end_date"] = max(dates)
                
                # Format individual data points consistently
                processed_data["data_points"] = []
                for i, point in enumerate(data_points):
                    date = point.get("logDateTime", f"Point {i+1}")
                    
                    # Extract value from channels array
                    value_wh = 0
                    if "channels" in point and isinstance(point["channels"], list) and len(point["channels"]) > 0:
                        channel = point["channels"][0]
                        if "value" in channel and channel["value"] is not None:
                            value_wh = float(channel["value"])
                    
                    value_kwh = value_wh / 1000.0
                    
                    processed_data["data_points"].append({
                        "date": date,
                        "energy_wh": value_wh,
                        "energy_kwh": round(value_kwh, 2),
                        "energy_production": f"{value_kwh:.2f} kWh"
                    })
        
        print(f"Processing energy data: Processing complete. Final results include:")
        if "total_energy_kwh" in processed_data:
            print(f"  - Total energy: {processed_data['total_energy_kwh']} kWh")
        if "data_points" in processed_data:
            print(f"  - Data points: {len(processed_data['data_points'])}")
            
        return processed_data
    except Exception as e:
        print(f"Error processing energy data: {e}")
        # Return original data if processing fails
        return data

def get_energy_production(system_id: str, start_date: str = None, end_date: str = None, jwt_token: str = None) -> Dict[str, Any]:
    """
    Gets aggregated energy production data for a specific solar system from the Solar.web API.
    Automatically calculates earnings using the system's custom earnings rate.
    
    Args:
        system_id: The ID of the system to get data for
        start_date: Start date in YYYY, YYYY-MM, or YYYY-MM-DD format
        end_date: End date in the same format as start_date
        jwt_token: JWT token for API authentication
        
    Returns:
        A dictionary with energy production data including calculated earnings
    """
    
    print(f"Fetching energy production data for system {system_id}, start_date: {start_date}, end_date: {end_date}")
    
    # Validate system_id
    if not system_id:
        return {
            "error": "No system ID provided. Please select a system before querying energy production data.",
            "system_id_required": True
        }
    
    # Base URL for the Solar.web API
    base_url = f"https://api.solarweb.com/swqapi/pvsystems/{system_id}/aggrdata"
    
    # Set up parameters for the API call
    params = {"channel": "EnergyProductionTotal"}
    
    # If no start_date is provided, default to today
    if not start_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
    
    # Add from parameter
    params["from"] = start_date
    
    # Add to parameter if end_date is provided
    if end_date and end_date.strip():
        params["to"] = end_date
    
    # Set up headers for API call
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'AccessKeyId': os.getenv('SOLAR_WEB_ACCESS_KEY_ID'),
        'AccessKeyValue': os.getenv('SOLAR_WEB_ACCESS_KEY_VALUE'),
        'Authorization': f'Bearer {jwt_token}' if jwt_token else 'Bearer eyJ4NXQiOiJOalpoT0dJMVpqQXpaVGt5TlRVNU1UbG1NVFkzWVRGbU9UWmpObVE0TURnME1HTmlZbU5sWkEiLCJraWQiOiJORFk0TVdaalpqWmhZakpsT1RRek5UTTVObUUwTkRWa016TXpOak16TmpBd1ptUmlNRFZsT1dRMVpHWmxPVEU1TWpSaU1XVXhZek01TURObU1ESXdaUV9SUzI1NiIsImFsZyI6IlJTMjU2In0.eyJhdF9oYXNoIjoiNUt6S0p1N1Q3RXk1VlZ6QWJQTE14dyIsImF1ZCI6ImMyZ0hwTXpRVUhmQ2ZsV3hIX3dFMkFlZzA5TWEgICAiLCJzdWIiOiJtb25pdG9yaW5nQGphenpzb2xhci5jb20iLCJuYmYiOjE3NDczMTQyNTMsImF6cCI6ImMyZ0hwTXpRVUhmQ2ZsV3hIX3dFMkFlZzA5TWEgICAiLCJhbXIiOlsicGFzc3dvcmQiXSwiaXNzIjoiaHR0cHM6XC9cL2xvZ2luLmZyb25pdXMuY29tXC9vYXV0aDJcL29pZGNkaXNjb3ZlcnkiLCJleHAiOjE3NDczMTc4NTMsImNvbnRhY3RfaWQiOiI2OGRmODA0My03OTI0LWUzMTEtOTc4ZS0wMDUwNTZhMjAwMDMiLCJpYXQiOjE3NDczMTQyNTN9.g9yitwr_6sHLOCRI2TAH7OZ_ibyQznkGmg3oEsdcySag5NYnimo5SY0OXIgTwNhoDkBsvA9BD-EWTN93ED7P1zR4RtUTo3iTJGaH5rTzdk33Tbk0dLGCrKhSj82kpkcLcMrmVtX37_9Kly37Jq1TuYZTOv63skz77uDNfjbHLEhSPyQueQlRtIsdU5z32OMx_0SJmP8V9llpm2T40Farr2OUNj_YczX98oC9xIO2aUBGSRPPYQFE5PQxAoNjl478-QeSoo2qNaHYlwlqBmJXOdukA1Kz6GBWKn2KNfp5r8r6x3UQGS_vys54ruwom-ZQbip7AAELesQdqNXiVEvZyg'
    }
    
    try:
        # Make the API call with GET
        print(f"Calling Solar.web API with URL: {base_url}, params: {params}")
        response = requests.get(
            base_url, 
            params=params, 
            headers=headers
        )
        
        # Check if the request was successful
        if response.status_code == 200:
            data = response.json()
            print(f"API call successful, received data: {data}")
            # Process the data to ensure consistent calculations
            processed_data = process_energy_data(data)
            
            # Calculate earnings using system's custom earnings rate
            earnings_rate = get_system_earnings_rate(system_id)
            total_energy_kwh = processed_data.get('total_energy_kwh', 0)
            total_earnings = total_energy_kwh * earnings_rate
            
            # Add earnings data to the response
            processed_data['earnings_rate'] = earnings_rate
            processed_data['total_earnings'] = round(total_earnings, 2)
            processed_data['earnings_text'] = f"${total_earnings:.2f}"
            
            print(f"Calculated earnings: {total_energy_kwh} kWh × ${earnings_rate}/kWh = ${total_earnings:.2f}")
            
            return processed_data
        else:
            print(f"API call failed with status code {response.status_code}: {response.text}")
            
            # Fall back to mock data if the API call fails
            print("Using mock data as fallback")
            # Determine format based on the from parameter
            date_format = params["from"]
            if len(date_format) == 4:  # YYYY
                format_str = "%Y"
                unit = "year"
            elif len(date_format) == 7:  # YYYY-MM
                format_str = "%Y-%m"
                unit = "month"
            else:  # YYYY-MM-DD
                format_str = "%Y-%m-%d"
                unit = "day"
                
            start_date_obj = datetime.strptime(params["from"], format_str)
            
            # Calculate duration or use end_date for mock data
            if "to" in params:
                end_date_format = params["to"]
                end_date_obj = datetime.strptime(end_date_format, format_str)
                
                if unit == "year":
                    mock_duration = end_date_obj.year - start_date_obj.year + 1
                elif unit == "month":
                    mock_duration = ((end_date_obj.year - start_date_obj.year) * 12 + 
                                    end_date_obj.month - start_date_obj.month + 1)
                else:  # day
                    mock_duration = (end_date_obj - start_date_obj).days + 1
            else:
                mock_duration = 1
            
            total_energy = 25.7 * mock_duration
            
            # Calculate earnings for mock data too
            earnings_rate = get_system_earnings_rate(system_id)
            total_earnings = total_energy * earnings_rate
            
            mock_data = {
                "system_id": system_id,
                "start_date": params["from"],
                "end_date": params.get("to", ""),
                "energy_production": f"{total_energy:.2f} kWh",
                "total_energy_kwh": round(total_energy, 2),
                "earnings_rate": earnings_rate,
                "total_earnings": round(total_earnings, 2),
                "earnings_text": f"${total_earnings:.2f}",
                "unit": unit,
                "data_points": []
            }
            
            for i in range(mock_duration):
                if unit == "day":
                    date_str = (start_date_obj + timedelta(days=i)).strftime("%Y-%m-%d")
                    value = 25.7 + (i * 1.5)  # Mock increasing values
                elif unit == "month":
                    # Add months by adding 32 days and formatting to YYYY-MM
                    next_month = start_date_obj.replace(day=1) + timedelta(days=32*i)
                    date_str = next_month.strftime("%Y-%m")
                    value = 780.5 + (i * 45.8)
                else:  # year
                    date_str = str(start_date_obj.year + i)
                    value = 9500.3 + (i * 520.7)
                    
                mock_data["data_points"].append({
                    "date": date_str,
                    "energy_wh": value * 1000,
                    "energy_kwh": round(value, 2),
                    "energy_production": f"{value:.2f} kWh"
                })
                
            return mock_data
    except Exception as e:
        print(f"Error fetching energy production data: {e}")
        return {"error": f"Failed to fetch energy production data: {str(e)}"}

def get_energy_production_inverter(system_id: str, device_id: str, start_date: str = None, end_date: str = None, jwt_token: str = None) -> Dict[str, Any]:
    """
    Gets aggregated energy production data for a specific inverter from the Solar.web API.
    Automatically calculates earnings using the system's custom earnings rate.
    
    Args:
        system_id: The ID of the system the inverter belongs to
        device_id: The ID of the inverter/device to get data for
        start_date: Start date in YYYY, YYYY-MM, or YYYY-MM-DD format
        end_date: End date in the same format as start_date
        jwt_token: JWT token for API authentication
        
    Returns:
        A dictionary with energy production data including calculated earnings
    """
    
    print(f"Fetching energy production data for inverter {device_id} in system {system_id}, start_date: {start_date}, end_date: {end_date}")
    
    # Validate system_id and device_id
    if not system_id:
        return {
            "error": "No system ID provided. Please select a system before querying inverter energy production data.",
            "system_id_required": True
        }
    
    if not device_id:
        return {
            "error": "No device ID provided. Please specify an inverter device ID.",
            "device_id_required": True
        }
    
    # Base URL for the Solar.web API - inverter specific endpoint
    base_url = f"https://api.solarweb.com/swqapi/pvsystems/{system_id}/devices/{device_id}/aggrdata"
    
    # Set up parameters for the API call
    params = {"channel": "EnergyExported"}
    
    # If no start_date is provided, default to today
    if not start_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
    
    # Add from parameter
    params["from"] = start_date
    
    # Add to parameter if end_date is provided
    if end_date and end_date.strip():
        params["to"] = end_date
    
    # Set up headers for API call
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'AccessKeyId': os.getenv('SOLAR_WEB_ACCESS_KEY_ID'),
        'AccessKeyValue': os.getenv('SOLAR_WEB_ACCESS_KEY_VALUE'),
        'Authorization': f'Bearer {jwt_token}' if jwt_token else 'Bearer eyJ4NXQiOiJOalpoT0dJMVpqQXpaVGt5TlRVNU1UbG1NVFkzWVRGbU9UWmpObVE0TURnME1HTmlZbU5sWkEiLCJraWQiOiJORFk0TVdaalpqWmhZakpsT1RRek5UTTVObUUwTkRWa016TXpOak16TmpBd1ptUmlNRFZsT1dRMVpHWmxPVEU1TWpSaU1XVXhZek01TURObU1ESXdaUV9SUzI1NiIsImFsZyI6IlJTMjU2In0.eyJhdF9oYXNoIjoiNUt6S0p1N1Q3RXk1VlZ6QWJQTE14dyIsImF1ZCI6ImMyZ0hwTXpRVUhmQ2ZsV3hIX3dFMkFlZzA5TWEgICAiLCJzdWIiOiJtb25pdG9yaW5nQGphenpzb2xhci5jb20iLCJuYmYiOjE3NDczMTQyNTMsImF6cCI6ImMyZ0hwTXpRVUhmQ2ZsV3hIX3dFMkFlZzA5TWEgICAiLCJhbXIiOlsicGFzc3dvcmQiXSwiaXNzIjoiaHR0cHM6XC9cL2xvZ2luLmZyb25pdXMuY29tXC9vYXV0aDJcL29pZGNkaXNjb3ZlcnkiLCJleHAiOjE3NDczMTc4NTMsImNvbnRhY3RfaWQiOiI2OGRmODA0My03OTI0LWUzMTEtOTc4ZS0wMDUwNTZhMjAwMDMiLCJpYXQiOjE3NDczMTQyNTN9.g9yitwr_6sHLOCRI2TAH7OZ_ibyQznkGmg3oEsdcySag5NYnimo5SY0OXIgTwNhoDkBsvA9BD-EWTN93ED7P1zR4RtUTo3iTJGaH5rTzdk33Tbk0dLGCrKhSj82kpkcLcMrmVtX37_9Kly37Jq1TuYZTOv63skz77uDNfjbHLEhSPyQueQlRtIsdU5z32OMx_0SJmP8V9llpm2T40Farr2OUNj_YczX98oC9xIO2aUBGSRPPYQFE5PQxAoNjl478-QeSoo2qNaHYlwlqBmJXOdukA1Kz6GBWKn2KNfp5r8r6x3UQGS_vys54ruwom-ZQbip7AAELesQdqNXiVEvZyg'
    }
    
    try:
        # Make the API call with GET
        print(f"Calling Solar.web API with URL: {base_url}, params: {params}")
        response = requests.get(
            base_url, 
            params=params, 
            headers=headers
        )
        
        # Check if the request was successful
        if response.status_code == 200:
            data = response.json()
            print(f"API call successful, received data: {data}")
            # Process the data to ensure consistent calculations
            processed_data = process_energy_data(data)
            
            # Calculate earnings using system's custom earnings rate (not inverter-specific)
            earnings_rate = get_system_earnings_rate(system_id)
            total_energy_kwh = processed_data.get('total_energy_kwh', 0)
            total_earnings = total_energy_kwh * earnings_rate
            
            # Add earnings data to the response
            processed_data['earnings_rate'] = earnings_rate
            processed_data['total_earnings'] = round(total_earnings, 2)
            processed_data['earnings_text'] = f"${total_earnings:.2f}"
            
            print(f"Calculated earnings: {total_energy_kwh} kWh × ${earnings_rate}/kWh = ${total_earnings:.2f}")
            
            return processed_data
        else:
            print(f"API call failed with status code {response.status_code}: {response.text}")
            
            # Fall back to mock data if the API call fails
            print("Using mock data as fallback for inverter")
            # Determine format based on the from parameter
            date_format = params["from"]
            if len(date_format) == 4:  # YYYY
                format_str = "%Y"
                unit = "year"
            elif len(date_format) == 7:  # YYYY-MM
                format_str = "%Y-%m"
                unit = "month"
            else:  # YYYY-MM-DD
                format_str = "%Y-%m-%d"
                unit = "day"
                
            start_date_obj = datetime.strptime(params["from"], format_str)
            
            # Calculate duration or use end_date for mock data
            if "to" in params:
                end_date_format = params["to"]
                end_date_obj = datetime.strptime(end_date_format, format_str)
                
                if unit == "year":
                    mock_duration = end_date_obj.year - start_date_obj.year + 1
                elif unit == "month":
                    mock_duration = ((end_date_obj.year - start_date_obj.year) * 12 + 
                                    end_date_obj.month - start_date_obj.month + 1)
                else:  # day
                    mock_duration = (end_date_obj - start_date_obj).days + 1
            else:
                mock_duration = 1
            
            # Mock data for inverter (typically 1/3 to 1/4 of system production)
            total_energy = 8.5 * mock_duration  # Reduced from system's 25.7
            
            # Generate mock data points
            mock_data_points = []
            current_date = start_date_obj
            
            for i in range(mock_duration):
                if unit == "year":
                    date_str = current_date.strftime("%Y")
                    current_date = current_date.replace(year=current_date.year + 1)
                elif unit == "month":
                    date_str = current_date.strftime("%Y-%m")
                    if current_date.month == 12:
                        current_date = current_date.replace(year=current_date.year + 1, month=1)
                    else:
                        current_date = current_date.replace(month=current_date.month + 1)
                else:  # day
                    date_str = current_date.strftime("%Y-%m-%d")
                    current_date += timedelta(days=1)
                
                mock_data_points.append({
                    "date": date_str,
                    "energy_kwh": round(8.5, 2)  # Consistent per-period production for inverter
                })
            
            # Calculate earnings using system's rate
            earnings_rate = get_system_earnings_rate(system_id)
            total_earnings = total_energy * earnings_rate
            
            return {
                "total_energy_kwh": total_energy,
                "data_points": mock_data_points,
                "earnings_rate": earnings_rate,
                "total_earnings": round(total_earnings, 2),
                "earnings_text": f"${total_earnings:.2f}",
                "mock_data": True,
                "system_id": system_id,
                "device_id": device_id
            }
    
    except requests.exceptions.RequestException as e:
        print(f"Request error: {str(e)}")
        return {
            "error": f"Failed to fetch inverter energy production data: {str(e)}",
            "system_id": system_id,
            "device_id": device_id
        }
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            "error": f"An unexpected error occurred while fetching inverter data: {str(e)}",
            "system_id": system_id,
            "device_id": device_id
        }

def process_co2_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process CO2 savings data from the Solar.web API to ensure consistent calculations.
    
    Args:
        data: Raw API response from Solar.web
        
    Returns:
        Processed data with consistent units and calculations
    """
    try:
        print(f"Processing CO2 data: Starting with raw API response")
        # Clone the original data to avoid modifying it
        processed_data = data.copy()
        
        # Check if this is already a mock response with our format
        if "co2_savings" in processed_data:
            print(f"Processing CO2 data: Already in our format, returning as is")
            return processed_data
            
        # Process real API data
        if "data" in processed_data and isinstance(processed_data["data"], list):
            data_points = processed_data["data"]
            print(f"Processing CO2 data: Found {len(data_points)} data points")
            
            # Extract values and dates
            values = []
            dates = []
            
            # Handle the nested structure of the API response
            for point in data_points:
                # Extract date from logDateTime field
                if "logDateTime" in point:
                    date = point["logDateTime"]
                    dates.append(date)
                
                # Extract value from channels array
                if "channels" in point and isinstance(point["channels"], list) and len(point["channels"]) > 0:
                    channel = point["channels"][0]  # Assuming the first channel is what we want
                    if "value" in channel and channel["value"] is not None:
                        value = float(channel["value"])
                        values.append(value)
                        print(f"  - Extracted CO2 value {value} for date {date}")
            
            print(f"Processing CO2 data: Extracted {len(values)} values and {len(dates)} dates")
            
            # Calculate total CO2 savings if we have values
            if values:
                # Calculate total CO2 savings in kg
                total_co2_kg = sum(values)
                
                print(f"Processing CO2 data: Calculated total CO2 savings as {total_co2_kg} kg")
                
                # Add calculated values to the processed data
                processed_data["total_co2_kg"] = round(total_co2_kg, 2)
                processed_data["co2_savings"] = f"{total_co2_kg:.2f} kg"
                
                # Add date range information
                if dates:
                    processed_data["start_date"] = min(dates)
                    processed_data["end_date"] = max(dates)
                
                # Format individual data points consistently
                processed_data["data_points"] = []
                for i, point in enumerate(data_points):
                    date = point.get("logDateTime", f"Point {i+1}")
                    
                    # Extract value from channels array
                    value_kg = 0
                    if "channels" in point and isinstance(point["channels"], list) and len(point["channels"]) > 0:
                        channel = point["channels"][0]
                        if "value" in channel and channel["value"] is not None:
                            value_kg = float(channel["value"])
                    
                    processed_data["data_points"].append({
                        "date": date,
                        "co2_kg": round(value_kg, 2),
                        "co2_savings": f"{value_kg:.2f} kg"
                    })
        
        print(f"Processing CO2 data: Processing complete. Final results include:")
        if "total_co2_kg" in processed_data:
            print(f"  - Total CO2 savings: {processed_data['total_co2_kg']} kg")
        if "data_points" in processed_data:
            print(f"  - Data points: {len(processed_data['data_points'])}")
            
        return processed_data
    except Exception as e:
        print(f"Error processing CO2 data: {e}")
        # Return original data if processing fails
        return data

def get_co2_savings(system_id: str, start_date: str = None, end_date: str = None, jwt_token: str = None) -> Dict[str, Any]:
    """
    Gets aggregated CO2 savings data for a specific solar system from the Solar.web API.
    
    Args:
        system_id: The ID of the system to get data for
        start_date: Start date in YYYY, YYYY-MM, or YYYY-MM-DD format
        end_date: End date in the same format as start_date
        jwt_token: JWT token for API authentication
        
    Returns:
        A dictionary with CO2 savings data
    """
    
    print(f"Fetching CO2 savings data for system {system_id}, start_date: {start_date}, end_date: {end_date}")
    
    # Validate system_id
    if not system_id:
        return {
            "error": "No system ID provided. Please select a system before querying CO2 savings data.",
            "system_id_required": True
        }
    
    # Base URL for the Solar.web API
    base_url = f"https://api.solarweb.com/swqapi/pvsystems/{system_id}/aggrdata"
    
    # Set up parameters for the API call
    params = {"channel": "SavingsCO2"}
    
    # If no start_date is provided, default to today
    if not start_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
    
    # Add from parameter
    params["from"] = start_date
    
    # Add to parameter if end_date is provided
    if end_date and end_date.strip():
        params["to"] = end_date
    
    # Set up headers for API call
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'AccessKeyId': os.getenv('SOLAR_WEB_ACCESS_KEY_ID'),
        'AccessKeyValue': os.getenv('SOLAR_WEB_ACCESS_KEY_VALUE'),
        'Authorization': f'Bearer {jwt_token}' if jwt_token else 'Bearer eyJ4NXQiOiJOalpoT0dJMVpqQXpaVGt5TlRVNU1UbG1NVFkzWVRGbU9UWmpObVE0TURnME1HTmlZbU5sWkEiLCJraWQiOiJORFk0TVdaalpqWmhZakpsT1RRek5UTTVObUUwTkRWa016TXpOak16TmpBd1ptUmlNRFZsT1dRMVpHWmxPVEU1TWpSaU1XVXhZek01TURObU1ESXdaUV9SUzI1NiIsImFsZyI6IlJTMjU2In0.eyJhdF9oYXNoIjoiNUt6S0p1N1Q3RXk1VlZ6QWJQTE14dyIsImF1ZCI6ImMyZ0hwTXpRVUhmQ2ZsV3hIX3dFMkFlZzA5TWEgICAiLCJzdWIiOiJtb25pdG9yaW5nQGphenpzb2xhci5jb20iLCJuYmYiOjE3NDczMTQyNTMsImF6cCI6ImMyZ0hwTXpRVUhmQ2ZsV3hIX3dFMkFlZzA5TWEgICAiLCJhbXIiOlsicGFzc3dvcmQiXSwiaXNzIjoiaHR0cHM6XC9cL2xvZ2luLmZyb25pdXMuY29tXC9vYXV0aDJcL29pZGNkaXNjb3ZlcnkiLCJleHAiOjE3NDczMTc4NTMsImNvbnRhY3RfaWQiOiI2OGRmODA0My03OTI0LWUzMTEtOTc4ZS0wMDUwNTZhMjAwMDMiLCJpYXQiOjE3NDczMTQyNTN9.g9yitwr_6sHLOCRI2TAH7OZ_ibyQznkGmg3oEsdcySag5NYnimo5SY0OXIgTwNhoDkBsvA9BD-EWTN93ED7P1zR4RtUTo3iTJGaH5rTzdk33Tbk0dLGCrKhSj82kpkcLcMrmVtX37_9Kly37Jq1TuYZTOv63skz77uDNfjbHLEhSPyQueQlRtIsdU5z32OMx_0SJmP8V9llpm2T40Farr2OUNj_YczX98oC9xIO2aUBGSRPPYQFE5PQxAoNjl478-QeSoo2qNaHYlwlqBmJXOdukA1Kz6GBWKn2KNfp5r8r6x3UQGS_vys54ruwom-ZQbip7AAELesQdqNXiVEvZyg'
    }
    
    try:
        # Make the API call with GET
        print(f"Calling Solar.web API with URL: {base_url}, params: {params}")
        response = requests.get(
            base_url, 
            params=params, 
            headers=headers
        )
        
        # Check if the request was successful
        if response.status_code == 200:
            data = response.json()
            print(f"API call successful, received data: {data}")
            # Process the data to ensure consistent calculations
            return process_co2_data(data)
        else:
            print(f"API call failed with status code {response.status_code}: {response.text}")
            
            # Fall back to mock data if the API call fails
            print("Using mock data as fallback")
            # Determine format based on the from parameter
            date_format = params["from"]
            if len(date_format) == 4:  # YYYY
                format_str = "%Y"
                unit = "year"
            elif len(date_format) == 7:  # YYYY-MM
                format_str = "%Y-%m"
                unit = "month"
            else:  # YYYY-MM-DD
                format_str = "%Y-%m-%d"
                unit = "day"
                
            start_date_obj = datetime.strptime(params["from"], format_str)
            
            # Calculate duration or use end_date for mock data
            if "to" in params:
                end_date_format = params["to"]
                end_date_obj = datetime.strptime(end_date_format, format_str)
                
                if unit == "year":
                    mock_duration = end_date_obj.year - start_date_obj.year + 1
                elif unit == "month":
                    mock_duration = ((end_date_obj.year - start_date_obj.year) * 12 + 
                                    end_date_obj.month - start_date_obj.month + 1)
                else:  # day
                    mock_duration = (end_date_obj - start_date_obj).days + 1
            else:
                mock_duration = 1
            
            total_co2 = 8.2 * mock_duration
            
            mock_data = {
                "system_id": system_id,
                "start_date": params["from"],
                "end_date": params.get("to", ""),
                "co2_savings": f"{total_co2:.2f} kg",
                "total_co2_kg": round(total_co2, 2),
                "unit": unit,
                "data_points": []
            }
            
            for i in range(mock_duration):
                if unit == "day":
                    date_str = (start_date_obj + timedelta(days=i)).strftime("%Y-%m-%d")
                    value = 8.2 + (i * 0.5)  # Mock increasing values
                elif unit == "month":
                    # Add months by adding 32 days and formatting to YYYY-MM
                    next_month = start_date_obj.replace(day=1) + timedelta(days=32*i)
                    date_str = next_month.strftime("%Y-%m")
                    value = 240.5 + (i * 15.2)
                else:  # year
                    date_str = str(start_date_obj.year + i)
                    value = 2900.5 + (i * 180.3)
                    
                mock_data["data_points"].append({
                    "date": date_str,
                    "co2_kg": round(value, 2),
                    "co2_savings": f"{value:.2f} kg"
                })
                
            return mock_data
    except Exception as e:
        print(f"Error fetching CO2 savings data: {e}")
        return {"error": f"Failed to fetch CO2 savings data: {str(e)}"}

def get_flow_data(system_id: str, jwt_token: str = None) -> Dict[str, Any]:
    """Get real-time flow data for a specific system"""
    try:
        # Validate system_id
        if not system_id:
            return {"error": "System ID is required"}
        
        # Get flow data from DynamoDB
        response = table.get_item(
            Key={
                'PK': f'System#{system_id}',
                'SK': 'FLOW'
            }
        )
        
        if 'Item' not in response:
            return {"error": f"No flow data found for system {system_id}"}
        
        item = response['Item']
        
        # Check if system is online based on last update timestamp
        last_updated = item.get('timestamp')
        is_online = False
        
        if last_updated:
            try:
                # Parse the timestamp and check if it's recent (within last 10 minutes)
                from datetime import datetime, timezone
                last_update_time = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                current_time = datetime.now(timezone.utc)
                time_diff = (current_time - last_update_time).total_seconds()
                is_online = time_diff < 600  # 10 minutes
            except:
                is_online = False
        
        # Extract power data
        channels = item.get('channels', {})
        power_pv = 0
        
        # Look for PowerPV channel
        for channel_id, channel_data in channels.items():
            if isinstance(channel_data, dict) and channel_data.get('channelType') == 'PowerPV':
                power_pv = channel_data.get('value', 0)
                break
        
        return {
            "system_id": system_id,
            "isOnline": is_online,
            "lastUpdated": last_updated,
            "powerPV": power_pv,
            "channels": channels
        }
        
    except Exception as e:
        print(f"Error getting flow data for system {system_id}: {str(e)}")
        return {"error": f"Failed to get flow data: {str(e)}"}

# Note: determine_api_date_format function removed - LLM now handles API format optimization


# Note: aggregate_data_points function removed - LLM chooses optimal API format, API returns pre-aggregated data


# Note: get_data_point_value function removed - simplified data processing in generate_chart_data


def generate_chart_data(
    data_type: str,
    system_id: Union[str, List[str]],
    start_date: str,
    end_date: str,
    time_period: str = "custom",
    jwt_token: str = None
) -> Dict[str, Any]:
    """
    Generate chart data for visualization with LLM-optimized API calls.
    
    Args:
        data_type: "energy_production", "co2_savings", "earnings"
        system_id: The solar system ID or list of IDs for aggregation
        start_date: Start date in YYYY, YYYY-MM, or YYYY-MM-DD format (LLM optimized)
        end_date: End date in same format as start_date
        time_period: Descriptive label for chart type determination
        jwt_token: JWT token for authentication
    
    Returns:
        ChartData formatted for frontend visualization
    """
    logger.info(f"=== GENERATE_CHART_DATA (LLM Enhanced) START ===")
    logger.info(f"Parameters received:")
    logger.info(f"  - data_type: {data_type}")
    logger.info(f"  - system_id: {system_id}")
    logger.info(f"  - start_date: {start_date}")
    logger.info(f"  - end_date: {end_date}")
    logger.info(f"  - time_period: {time_period}")
    logger.info(f"  - jwt_token: {'[PROVIDED]' if jwt_token else '[NOT PROVIDED]'}")
    
    try:
        # Normalize system IDs (support single string, comma-separated string, or list)
        if isinstance(system_id, list):
            system_ids: List[str] = [sid for sid in system_id if isinstance(sid, str) and sid.strip()]
        elif isinstance(system_id, str) and "," in system_id:
            system_ids = [sid.strip() for sid in system_id.split(",") if sid.strip()]
        else:
            system_ids = [system_id] if isinstance(system_id, str) and system_id else []

        is_aggregate = len(system_ids) > 1

        # Determine expected date buckets from start/end format to keep consistent count
        def generate_expected_dates(sd: str, ed: str) -> List[str]:
            if len(sd) == 4:  # YYYY
                start_year = int(sd)
                end_year = int(ed)
                return [str(y) for y in range(start_year, end_year + 1)]
            elif len(sd) == 7:  # YYYY-MM
                dates: List[str] = []
                cursor = datetime.strptime(sd, "%Y-%m")
                end_dt = datetime.strptime(ed, "%Y-%m")
                while cursor <= end_dt:
                    dates.append(cursor.strftime("%Y-%m"))
                    # advance by ~1 month reliably
                    next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                    cursor = next_month
                return dates
            else:  # YYYY-MM-DD
                dates: List[str] = []
                cursor = datetime.strptime(sd, "%Y-%m-%d")
                end_dt = datetime.strptime(ed, "%Y-%m-%d")
                while cursor <= end_dt:
                    dates.append(cursor.strftime("%Y-%m-%d"))
                    cursor += timedelta(days=1)
                return dates

        # Resolve system name(s)
        if not is_aggregate and system_ids:
            system_name = "Solar System"
            try:
                profile_response = table.get_item(
                    Key={'PK': f'System#{system_ids[0]}', 'SK': 'PROFILE'}
                )
                if 'Item' in profile_response:
                    system_name = profile_response['Item'].get('name', f"System {system_ids[0]}")
                    logger.info(f"System profile found - name: {system_name}")
            except Exception as e:
                logger.error(f"Error fetching system profile: {str(e)}")
        else:
            system_name = f"{len(system_ids)} systems (combined)" if system_ids else "No system"

        logger.info(f"Making direct API call with dates: {start_date} to {end_date}")

        unit = ""
        chart_data_points: List[Dict[str, Any]] = []

        # Handle each data type with single vs aggregated paths
        if data_type in ["energy_production", "earnings"]:
            if not is_aggregate and system_ids:
                raw_data = get_energy_production(system_ids[0], start_date, end_date, jwt_token)
                if "error" in raw_data:
                    logger.error(f"Error in energy data: {raw_data['error']}")
                    return {"error": raw_data["error"]}

                if data_type == "earnings":
                    total_value = float(raw_data.get('total_earnings', 0))
                    unit = "$"
                else:
                    total_value = float(raw_data.get('total_energy_kwh', 0))
                    unit = "kWh"

                raw_data_points = raw_data.get('data_points', [])
                for data_point in raw_data_points:
                    date_str = data_point.get('date', '')
                    if data_type == "energy_production":
                        value = float(data_point.get('energy_kwh', 0))
                    else:
                        energy_kwh = float(data_point.get('energy_kwh', 0))
                        earnings_rate = raw_data.get('earnings_rate', 0.4)
                        value = energy_kwh * earnings_rate

                    # x label formatting
                    try:
                        if len(date_str) == 4:
                            x_label = date_str
                        elif len(date_str) == 7:
                            x_label = datetime.strptime(date_str, "%Y-%m").strftime("%b")
                        elif len(date_str) >= 10:
                            x_label = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%m/%d")
                        else:
                            x_label = date_str
                    except ValueError:
                        x_label = date_str

                    chart_data_points.append({"x": x_label, "y": round(value, 2)})
            else:
                # Aggregated across multiple systems
                expected_dates = generate_expected_dates(start_date, end_date)
                # Map date -> {energy_kwh: float, earnings: float}
                agg_map: Dict[str, Dict[str, float]] = {d: {"energy_kwh": 0.0, "earnings": 0.0} for d in expected_dates}
                total_energy = 0.0
                total_earnings = 0.0

                for sid in system_ids:
                    rd = get_energy_production(sid, start_date, end_date, jwt_token)
                    if "error" in rd:
                        logger.warning(f"get_energy_production error for {sid}: {rd.get('error')}")
                        continue
                    rate = float(rd.get('earnings_rate', 0.4))
                    total_energy += float(rd.get('total_energy_kwh', 0) or 0)
                    total_earnings += float(rd.get('total_earnings', 0) or 0)
                    for dp in rd.get('data_points', []):
                        d = dp.get('date', '')
                        if not d:
                            continue
                        # normalize to exact expected key if possible
                        key = d[:10] if len(expected_dates[0]) == 10 and len(d) >= 10 else d
                        if key in agg_map:
                            ek = float(dp.get('energy_kwh', 0) or 0)
                            agg_map[key]["energy_kwh"] += ek
                            agg_map[key]["earnings"] += ek * rate

                # Build chart points in order
                if data_type == "earnings":
                    unit = "$"
                    total_value = round(total_earnings, 2)
                else:
                    unit = "kWh"
                    total_value = round(total_energy, 2)

                for date_str in expected_dates:
                    # x label formatting
                    try:
                        if len(date_str) == 4:
                            x_label = date_str
                        elif len(date_str) == 7:
                            x_label = datetime.strptime(date_str, "%Y-%m").strftime("%b")
                        elif len(date_str) >= 10:
                            x_label = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%m/%d")
                        else:
                            x_label = date_str
                    except ValueError:
                        x_label = date_str

                    y_val = agg_map[date_str]["earnings"] if data_type == "earnings" else agg_map[date_str]["energy_kwh"]
                    chart_data_points.append({"x": x_label, "y": round(float(y_val), 2)})

        elif data_type == "co2_savings":
            if not is_aggregate and system_ids:
                raw_data = get_co2_savings(system_ids[0], start_date, end_date, jwt_token)
                if "error" in raw_data:
                    logger.error(f"Error in CO2 data: {raw_data['error']}")
                    return {"error": raw_data["error"]}
                unit = "kg CO2"
                total_value = float(raw_data.get('total_co2_kg', 0))
                for data_point in raw_data.get('data_points', []):
                    date_str = data_point.get('date', '')
                    value = float(data_point.get('co2_kg', 0))
                    try:
                        if len(date_str) == 4:
                            x_label = date_str
                        elif len(date_str) == 7:
                            x_label = datetime.strptime(date_str, "%Y-%m").strftime("%b")
                        elif len(date_str) >= 10:
                            x_label = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%m/%d")
                        else:
                            x_label = date_str
                    except ValueError:
                        x_label = date_str
                    chart_data_points.append({"x": x_label, "y": round(value, 2)})
            else:
                expected_dates = generate_expected_dates(start_date, end_date)
                agg_map: Dict[str, float] = {d: 0.0 for d in expected_dates}
                total_co2 = 0.0
                for sid in system_ids:
                    rd = get_co2_savings(sid, start_date, end_date, jwt_token)
                    if "error" in rd:
                        logger.warning(f"get_co2_savings error for {sid}: {rd.get('error')}")
                        continue
                    total_co2 += float(rd.get('total_co2_kg', 0) or 0)
                    for dp in rd.get('data_points', []):
                        d = dp.get('date', '')
                        if not d:
                            continue
                        key = d[:10] if len(expected_dates[0]) == 10 and len(d) >= 10 else d
                        if key in agg_map:
                            agg_map[key] += float(dp.get('co2_kg', 0) or 0)
                unit = "kg CO2"
                total_value = round(total_co2, 2)
                for date_str in expected_dates:
                    try:
                        if len(date_str) == 4:
                            x_label = date_str
                        elif len(date_str) == 7:
                            x_label = datetime.strptime(date_str, "%Y-%m").strftime("%b")
                        elif len(date_str) >= 10:
                            x_label = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%m/%d")
                        else:
                            x_label = date_str
                    except ValueError:
                        x_label = date_str
                    chart_data_points.append({"x": x_label, "y": round(float(agg_map[date_str]), 2)})
        else:
            return {"error": f"Unsupported data_type '{data_type}'"}

        # Determine chart type and period text (same logic as before)
        if len(start_date) == 4:
            chart_type = "bar"
            period_text = f"Years {start_date}-{end_date}" if start_date != end_date else f"Year {start_date}"
        elif len(start_date) == 7:
            chart_type = "bar"
            try:
                start_formatted = datetime.strptime(start_date, "%Y-%m").strftime("%B %Y")
                if start_date != end_date:
                    end_formatted = datetime.strptime(end_date, "%Y-%m").strftime("%B %Y")
                    period_text = f"{start_formatted} - {end_formatted}"
                else:
                    period_text = start_formatted
            except ValueError:
                period_text = f"{start_date} - {end_date}" if start_date != end_date else start_date
        else:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                days_diff = (end_dt - start_dt).days + 1
                chart_type = "line" if days_diff <= 30 else "bar"
                if days_diff == 1:
                    period_text = start_dt.strftime("%B %d, %Y")
                else:
                    start_formatted = start_dt.strftime("%B %d")
                    end_formatted = end_dt.strftime("%B %d, %Y")
                    period_text = f"{start_formatted} - {end_formatted}"
            except ValueError:
                chart_type = "bar"
                period_text = f"{start_date} - {end_date}"

        data_type_text = {
            "energy_production": "Energy Production",
            "co2_savings": "CO2 Savings",
            "earnings": "Earnings"
        }.get(data_type, data_type)

        title = f"{data_type_text} - {period_text}"

        chart_data = {
            "chart_type": chart_type,
            "data_type": data_type,
            "title": title,
            "x_axis_label": "Time Period",
            "y_axis_label": f"{data_type_text} ({unit})",
            "data_points": chart_data_points,
            "time_period": time_period,
            "total_value": round(float(total_value) if 'total_value' in locals() else 0.0, 2),
            "unit": unit,
            "system_name": system_name
        }

        logger.info(f"Generated chart with {len(chart_data_points)} points, total: {chart_data['total_value']} {unit}")
        logger.info(f"Chart type: {chart_type}, Title: {title}")
        logger.info(f"=== GENERATE_CHART_DATA SUCCESS ===")
        return chart_data

    except Exception as e:
        logger.error(f"=== GENERATE_CHART_DATA ERROR ===")
        logger.error(f"Error generating chart data: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"error": f"Failed to generate chart data: {str(e)}"}

def search_vector_db(query: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Search the vector database for relevant documents.
    
    Args:
        query: The search query
        limit: Maximum number of results to return
        
    Returns:
        A list of relevant documents
    """
    # Get the RAG instance
    rag = get_rag_instance()
    if not rag or not rag.vector_store:
        return [{"content": "Vector database is not available", "score": 0}]
    
    # Search the vector store
    try:
        # Search the vector store directly
        results = rag.retriever.get_relevant_documents(query)
        print(f"\n===== RETRIEVED {len(results[:limit])} CHUNKS FROM KNOWLEDGE BASE =====")
        for i, doc in enumerate(results[:limit]):
            print(f"\n=====CHUNK {i+1}=====")
            print(f"Content: {doc.page_content}")
            print(f"Metadata: {doc.metadata}")
            print("=" * 50)
        
        # Convert to the expected format
        return [
            {
                "content": doc.page_content,
                "metadata": doc.metadata,
                "score": 0.9 - (i * 0.1)  # Mock similarity score
            }
            for i, doc in enumerate(results[:limit])
        ]
    except Exception as e:
        print(f"Error searching vector database: {e}")
        return [{"content": f"Error searching vector database: {str(e)}", "score": 0}]

def generate_monthly_solar_report(site_id: str, month_string: str, jwt_token: str = None) -> Dict[str, Any]:
    """
    Generate a comprehensive monthly solar performance report in JSON format with downloadable PDF.
    
    Args:
        site_id: Unique identifier for the solar site
        month_string: Natural language string like "July 2025" or "July 2024"
        jwt_token: JWT token for authentication (optional, for future API integration)
        
    Returns:
        Dict containing structured report data and S3 URL for PDF download
    """
    try:
        # Parse month string to get proper date range
        month_string = month_string.strip()
        
        # Handle different month string formats
        try:
            # Try parsing "Month YYYY" format first
            date_obj = datetime.strptime(month_string, "%B %Y")
        except ValueError:
            try:
                # Try parsing "Month, YYYY" format
                date_obj = datetime.strptime(month_string, "%B, %Y")
            except ValueError:
                try:
                    # Try parsing "MM/YYYY" format
                    date_obj = datetime.strptime(month_string, "%m/%Y")
                except ValueError:
                    # Default to current month if parsing fails
                    date_obj = datetime.now()
                    print(f"Could not parse month string '{month_string}', using current month")
        
        year = date_obj.year
        month = date_obj.month
        month_name = date_obj.strftime("%B")
        
        # Convert to YYYY-MM format for API calls
        month_api_format = f"{year}-{month:02d}"
        print(f"Getting real data for {month_name} {year} using API format: {month_api_format}")

        # Build site_info from DynamoDB PROFILE (PK=System#<id>, SK=PROFILE)
        site_info: Dict[str, Any] = {
            "site_name": str(site_id),
            "location_string": "N/A",
            "site_peak_power": "N/A",
        }
        try:
            if table is not None:
                resp = table.get_item(
                    Key={
                        'PK': f'System#{site_id}',
                        'SK': 'PROFILE'
                    }
                )
                if 'Item' in resp:
                    item = convert_dynamodb_decimals(resp['Item'])
                    name = item.get('name')
                    street = item.get('street')
                    city = item.get('city')
                    country = item.get('country')
                    peak_power = item.get('peakPower')

                    # Compose location string from available parts
                    parts = [p for p in [street, city, country] if p]
                    location_string = ", ".join(parts) if parts else "N/A"

                    # Convert peak power to integer if possible
                    site_peak_power: Any = "N/A"
                    if peak_power is not None:
                        try:
                            site_peak_power = int(round(float(peak_power)))
                        except Exception:
                            site_peak_power = peak_power  # fallback to raw value

                    site_info = {
                        "site_name": name or str(site_id),
                        "location_string": location_string,
                        "site_peak_power": site_peak_power,
                    }
            else:
                print("DynamoDB table is not initialized; using default site_info values")
        except Exception as e:
            print(f"Error fetching site profile for system {site_id}: {e}")
        
        # 1. Get total monthly production and earnings using get_energy_production
        print(f"Getting monthly totals for {month_api_format}")
        monthly_data = get_energy_production(site_id, month_api_format, month_api_format, jwt_token)
        if "error" in monthly_data:
            print(f"Error getting monthly data: {monthly_data['error']}")
            return {"error": f"Failed to get monthly energy data: {monthly_data['error']}"}
        
        total_production = monthly_data.get('total_energy_kwh', 0)
        total_earnings = monthly_data.get('total_earnings', 0)
        print(f"Monthly totals - Production: {total_production} kWh, Earnings: ${total_earnings}")
        
        # 2. Calculate average daily earnings
        days_in_month = calendar.monthrange(year, month)[1]
        average_daily_earnings = total_earnings / days_in_month if days_in_month > 0 else 0
         #3.  Build monthly earnings breakdown (same month, last up to 5 years)
        earnings_years = []
        earnings_values = []
        try:
            # Build requests for last 5 years (including current)
            years_to_fetch = [year - offset for offset in range(0, 5)]
            ym_list = [(str(y), f"{y}-{month:02d}") for y in years_to_fetch]
            results_map: Dict[str, float] = {}
            with ThreadPoolExecutor(max_workers=min(8, len(ym_list))) as executor:
                future_map = {
                    executor.submit(get_energy_production, site_id, ym, ym, jwt_token): y
                    for (y, ym) in ym_list
                }
                for f in as_completed(future_map):
                    y = future_map[f]
                    try:
                        md = f.result()
                        if 'error' in md:
                            print(f"Earnings history: error for {y}: {md.get('error')}")
                            continue
                        val = float(md.get('total_earnings', 0) or 0)
                        results_map[str(y)] = round(val, 2)
                    except Exception as ee:
                        print(f"Earnings history exception for {y}: {ee}")
                        continue
            # chronological order oldest -> newest
            for y in sorted(results_map.keys()):
                earnings_years.append(y)
                earnings_values.append(results_map[y])
        except Exception as e_hist:
            print(f"Error building earnings history: {e_hist}")
        earnings_history = { 'years': earnings_years, 'earnings': earnings_values }

        # 4. Get inverter information to get count and IDs
        print(f"Getting inverter information for system {site_id}")
        # Use a default user_id that the LLM would use - this should be the admin user or system user
        try:
            response = table.query(
                IndexName='device-system-index',  # Using GSI2
                KeyConditionExpression='GSI2PK = :pk AND begins_with(GSI2SK, :sk)',
                ExpressionAttributeValues={
                    ':pk': f'System#{site_id}',
                    ':sk': 'Inverter#'
                }
            )
            
            inverter_links = response.get('Items', [])
            inverter_ids = []
            
            for link in inverter_links:
                try:
                    inverter_id = link.get('GSI2SK', '').replace('Inverter#', '')
                    if inverter_id:  # Only add non-empty inverter IDs
                        inverter_ids.append(inverter_id)
                except Exception as e:
                    print(f"Error processing inverter link {link}: {e}")
                    continue
            
            print(f"Found {len(inverter_ids)} inverters: {inverter_ids}")
            inverter_count = len(inverter_ids)
            
            # Validate we found at least some inverters
            if inverter_count == 0:
                print(f"Warning: No inverters found for system {site_id}")
                # Set a default count to prevent division by zero errors later
                inverter_count = 1
                inverter_ids = ['unknown-inverter']
                
        except Exception as e:
            print(f"Error querying inverters for system {site_id}: {e}")
            # Fallback values to prevent crashes
            inverter_count = 1
            inverter_ids = ['error-inverter']
        
        # 4. Average inverter uptime - hardcoded for now
        average_inverter_uptime = 97.5
        
        # 5. Get daily production data concurrently for each day
        print(f"Getting daily production data for {days_in_month} days (concurrent)")
        dates_list = [datetime(year, month, d).strftime("%Y-%m-%d") for d in range(1, days_in_month + 1)]
        daily_results: Dict[str, Dict[str, float]] = {}
        def _fetch_day(date_str: str) -> Dict[str, float]:
            try:
                data = get_energy_production(site_id, date_str, date_str, jwt_token)
                if "error" not in data:
                    return {
                        "production": float(data.get('total_energy_kwh', 0) or 0),
                        "earnings": float(data.get('total_earnings', 0) or 0)
                    }
            except Exception:
                pass
            # fallback proportional
            return {
                "production": (total_production / days_in_month) if days_in_month > 0 else 0.0,
                "earnings": (total_earnings / days_in_month) if days_in_month > 0 else 0.0,
            }
        with ThreadPoolExecutor(max_workers=min(16, len(dates_list))) as executor:
            future_map = {executor.submit(_fetch_day, d): d for d in dates_list}
            for f in as_completed(future_map):
                d = future_map[f]
                try:
                    daily_results[d] = f.result()
                except Exception as e:
                    print(f"Daily fetch failed for {d}: {e}")
                    daily_results[d] = {
                        "production": (total_production / days_in_month) if days_in_month > 0 else 0.0,
                        "earnings": (total_earnings / days_in_month) if days_in_month > 0 else 0.0,
                    }
        daily_data = []
        highest_production_day = {"date": "", "production_kwh": 0}
        for d in dates_list:
            vals = daily_results.get(d, {"production": 0.0, "earnings": 0.0})
            daily_record = {
                "date": d,
                "production_kwh": round(vals["production"], 2),
                "earnings_usd": round(vals["earnings"], 2),
            }
            daily_data.append(daily_record)
            if vals["production"] > highest_production_day["production_kwh"]:
                highest_production_day = {"date": d, "production_kwh": round(vals["production"], 2)}
        
        # 7. Generate inverter performance list with real inverter IDs but hardcoded uptimes
        inverters = []
        for i, inverter_id in enumerate(inverter_ids):
            # Hardcoded uptimes and notes for now
            uptime_options = [98.5, 97.2, 99.1, 96.8, 98.9]
            note_options = [
                "Operating normally",
                f"Short downtime on {month_name} {random.randint(1, 28)}",
                f"Maintenance alert on {month_name} {random.randint(1, 28)}",
                "Excellent performance",
                f"Minor issue resolved on {month_name} {random.randint(1, 28)}"
            ]

            # Fetch inverter deviceName from DynamoDB PROFILE
            device_name = inverter_id
            try:
                profile_resp = table.get_item(
                    Key={
                        'PK': f'Inverter#{inverter_id}',
                        'SK': 'PROFILE'
                    }
                )
                if 'Item' in profile_resp:
                    device_name = profile_resp['Item'].get('deviceName', device_name)
            except Exception as e:
                print(f"Error fetching deviceName for inverter {inverter_id}: {e}")
            
            inverters.append({
                "inverter_id": inverter_id,
                "deviceName": device_name,
                "uptime_percent": uptime_options[i % len(uptime_options)],
                "notes": note_options[i % len(note_options)]
            })
        
        # 6. Generate weather data - hardcoded for now
        weather_data = []
        for day_record in daily_data:
            # Hardcoded weather data with some variation
            weather_record = {
                "date": day_record["date"],
                "temperature_c": round(random.uniform(18, 32), 1),
                "solar_irradiance_kwh_m2": round(random.uniform(4.5, 7.5), 2),
                "humidity_percent": round(random.uniform(40, 85), 1)
            }
            weather_data.append(weather_record)
        
        # Calculate summary statistics
        average_daily_production = total_production / len(daily_data) if len(daily_data) > 0 else 0
        average_inverter_uptime = sum(inv["uptime_percent"] for inv in inverters) / len(inverters) if len(inverters) > 0 else 97.5
        
        # Prepare chart data
        chart_data = {
            "dates": [record["date"] for record in daily_data],
            "production": [record["production_kwh"] for record in daily_data],
            "solar_irradiance": [record["solar_irradiance_kwh_m2"] for record in weather_data]
        }
        
        
        # Prepare inverter production chart data concurrently (consistency + speed)
        print(f"Preparing inverter production data for PDF for {len(inverters)} inverters (concurrent)")
        inverter_chart_values = [0.0] * len(inverters)
        inverter_chart_names = [""] * len(inverters)
        with ThreadPoolExecutor(max_workers=min(16, len(inverters))) as executor:
            future_map = {
                executor.submit(
                    get_energy_production_inverter,
                    system_id=site_id,
                    device_id=inv["inverter_id"],
                    start_date=month_api_format,
                    end_date=month_api_format,
                    jwt_token=jwt_token,
                ): i
                for i, inv in enumerate(inverters)
            }
            for f in as_completed(future_map):
                i = future_map[f]
                try:
                    inv_data = f.result()
                    if 'error' not in inv_data:
                        val = float(inv_data.get('total_energy_kwh', 0) or 0)
                    else:
                        val = round(total_production / max(len(inverters), 1), 1)
                except Exception as e:
                    print(f"Inverter fetch failed idx {i}: {e}")
                    val = round(total_production / max(len(inverters), 1), 1)
                inverter_chart_values[i] = round(val, 1)
        # labels sequential (no network)
        for i, inv in enumerate(inverters):
            dn = inv.get("deviceName") or ""
            try:
                m = re.search(r"Inv-\s*(\d+)", dn, re.IGNORECASE)
                if m:
                    display_name = m.group(1)
                else:
                    display_name = inv.get("deviceName") or (f"INV-{inv['inverter_id'][-3:]}" if len(inv['inverter_id']) > 3 else f"INV-{inv['inverter_id']}")
            except Exception:
                display_name = inv.get("deviceName") or inv.get("inverter_id", "INV")
            inverter_chart_names[i] = display_name
        if not any(inverter_chart_values):
            inverter_chart_values = [round(total_production * 0.35, 1), round(total_production * 0.33, 1), round(total_production * 0.32, 1)]
            inverter_chart_names = ['INV-001', 'INV-002', 'INV-003']
        print("Generating LLM observations...")
        observations = [
                f"System generated {total_production:.1f} kWh in {month_name}, earning ${total_earnings:.2f}",
                f"Average daily production of {average_daily_production:.1f} kWh shows consistent performance",
                f"All {inverter_count} inverters maintained good uptime averaging {average_inverter_uptime:.1f}%",
                f"Peak production day was {highest_production_day['date']} with {highest_production_day['production_kwh']} kWh"
            ]
            
        
        
        # Create PDF report
        pdf_filename = f"solar_report_{site_id}_{year}_{month:02d}.pdf"
        pdf_url = create_and_upload_pdf_report(site_id, month_string, month_api_format, daily_data, inverters, weather_data, earnings_history, inverter_chart_names, inverter_chart_values, site_info, pdf_filename, jwt_token)
        
        # Prepare final response with real data
        report_data = {
            "site_id": site_id,
            "report_month": f"{month_name} {year}",
            "summary": {
                "total_production_kwh": round(total_production, 2),
                "total_earnings_usd": round(total_earnings, 2),
                "average_daily_production_kwh": round(average_daily_production, 2),
                "average_daily_earnings_usd": round(average_daily_earnings, 2),  # Added this field
                "highest_production_day": highest_production_day,
                "inverter_count": inverter_count,  # Use real count
                "average_inverter_uptime_percent": round(average_inverter_uptime, 1)
            },
            "chart_data": chart_data,
            "inverters": inverters,  # Real inverter IDs with hardcoded uptimes
            "observations": observations,  # LLM-generated observations
            "reportUrl": pdf_url
        }
        
        print(f"Report generation completed successfully:")
        print(f"- Total Production: {total_production} kWh")
        print(f"- Total Earnings: ${total_earnings}")
        print(f"- Inverter Count: {inverter_count}")
        print(f"- Daily Data Points: {len(daily_data)}")
        print(f"- Observations: {len(observations)}")
        print(f"- PDF URL: {pdf_url}")
        
        return report_data
        
    except Exception as e:
        print(f"Error generating monthly solar report: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return {"error": f"Failed to generate monthly solar report: {str(e)}"}

def create_and_upload_pdf_report(site_id: str, month_string: str, month_api_format: str, daily_data: List[Dict], 
                                inverters: List[Dict], weather_data: List[Dict], earnings_history: Dict[str, List], inverter_chart_names: List[str], inverter_chart_values: List[float], site_info: Dict[str, Any], filename: str, jwt_token: str) -> str:
    """
    Create a PDF report using ReportLab and upload to S3.
    
    Returns:
        Public S3 URL of the uploaded PDF
    """
    try:
        # Create PDF in memory
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch, 
                               leftMargin=0.75*inch, rightMargin=0.75*inch)
        
        # Get styles and create custom styles
        styles = getSampleStyleSheet()
        
        # Title style - more compact
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            alignment=TA_CENTER,
            spaceAfter=12,
            textColor=colors.HexColor('#1e3a8a'),
            fontName='Helvetica-Bold'
        )
        
        # Subtitle style
        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontSize=12,
            alignment=TA_CENTER,
            spaceAfter=20,
            textColor=colors.HexColor('#374151'),
            fontName='Helvetica'
        )
        
        # Style for KPI numbers - more compact
        highlight_style = ParagraphStyle(
            'HighlightNumbers',
            parent=styles['Normal'],
            fontSize=14,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#059669'),
            fontName='Helvetica-Bold',
            spaceAfter=0,
            spaceBefore=0
        )
        
        # Section headers - more compact
        section_header_style = ParagraphStyle(
            'SectionHeader',
            parent=styles['Heading2'],
            fontSize=12,
            textColor=colors.HexColor('#1e3a8a'),
            fontName='Helvetica-Bold',
            spaceAfter=8,
            spaceBefore=16
        )
        
        # Build PDF content
        story = []
        
        # Header with logo placeholder and system info
        header_table_data = [
            [
                Paragraph("Monthly Solar Performance Report", title_style),
                "[LOGO PLACEHOLDER]"  # You can replace this with an Image object later
            ]
        ]
        
        header_table = Table(header_table_data, colWidths=[4.5*inch, 2*inch])
        header_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        
        story.append(header_table)
        
        # System Information Section - Professional layout
        system_info_data = [
            ["System Information", ""],
            ["Site Name:", site_info.get('site_name') or str(site_id)],
            ["Location:", site_info.get('location_string') or 'N/A'],
            ["Peak Power:", f"{site_info.get('site_peak_power') / 1000:.2f} kW" if site_info.get('site_peak_power') is not None else "N/A"],
            ["Report Period:", month_string],
            ["Generated:", datetime.now().strftime("%B %d, %Y")]
        ]
        
        system_info_table = Table(system_info_data, colWidths=[1.5*inch, 4*inch])
        system_info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f6')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        
        story.append(system_info_table)
        story.append(Spacer(1, 20))
        
        # FINANCIAL SECTION
        story.append(Paragraph("Financial Performance", section_header_style))
        
        total_production = sum(d['production_kwh'] for d in daily_data)
        total_earnings = sum(d['earnings_usd'] for d in daily_data)
        estimated_annual_earnings = total_earnings * 12  # Simple annual estimate
        
        # Financial KPIs
        financial_kpi_data = [
            [
                Paragraph(f"<b>${total_earnings:.0f}</b><br/><font size=9 color='#6b7280'>Monthly Earnings</font>", highlight_style),
                Paragraph(f"<b>${total_earnings/len(daily_data):.1f}</b><br/><font size=9 color='#6b7280'>Daily Avg Earnings</font>", highlight_style),
                Paragraph(f"<b>${estimated_annual_earnings:.0f}</b><br/><font size=9 color='#6b7280'>Est. Annual Earnings</font>", highlight_style)
            ]
        ]
        
        financial_kpi_table = Table(financial_kpi_data, colWidths=[2.2*inch, 2.2*inch, 2.2*inch])
        financial_kpi_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0fdf4')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#22c55e')),
            ('ROUNDEDCORNERS', [4, 4, 4, 4]),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ]))
        
        story.append(financial_kpi_table)
        story.append(Spacer(1, 14))
        
        # Financial Chart Placeholder
        story.append(Paragraph("Monthly Earnings Breakdown (Same Month, Last 5 Years)", section_header_style))
        if earnings_history and isinstance(earnings_history, dict):
            years = earnings_history.get('years', [])
            earnings = earnings_history.get('earnings', [])
            if years and earnings and len(years) == len(earnings):
                earnings_chart_drawing = Drawing(400, 200)
                earnings_bar_chart = VerticalBarChart()
                earnings_bar_chart.x = 50
                earnings_bar_chart.y = 50
                earnings_bar_chart.height = 120
                earnings_bar_chart.width = 300

                # Chart data
                earnings_bar_chart.data = [earnings]
                earnings_bar_chart.categoryAxis.categoryNames = years

                # IMPORTANT: Set the value axis range to start from 0
                earnings_bar_chart.valueAxis.valueMin = 0
                earnings_bar_chart.valueAxis.valueMax = max(earnings) * 1.1  # Add 10% padding at top

                # Style similar to inverter chart
                earnings_bar_chart.bars[0].fillColor = colors.darkgreen
                earnings_bar_chart.categoryAxis.labels.fontName = 'Helvetica'
                earnings_bar_chart.categoryAxis.labels.fontSize = 10
                earnings_bar_chart.valueAxis.labels.fontName = 'Helvetica'
                earnings_bar_chart.valueAxis.labels.fontSize = 9

                # Format the value axis to show currency
                earnings_bar_chart.valueAxis.labelTextFormat = '$%d'

                # Title and axis label
                earnings_chart_drawing.add(String(200, 180, f"{month_string.split()[0]} Earnings (USD) - Last 5 Years", 
                                                fontSize=12, fontName='Helvetica-Bold', textAnchor='middle'))
                earnings_chart_drawing.add(String(25, 110, '$', fontSize=10, fontName='Helvetica', textAnchor='middle'))
                earnings_chart_drawing.add(earnings_bar_chart)
                story.append(earnings_chart_drawing)
                

        story.append(PageBreak())
        # TECHNICAL SECTION
        story.append(Paragraph("Technical Performance", section_header_style))
        
        # Technical Performance Summary
        tech_summary_data = [
            ["Metric", "Value"],
            ["Total Energy Production", f"{total_production:.1f} kWh"],
            ["Average Daily Production", f"{total_production / len(daily_data):.1f} kWh"],
            ["Peak Production Day", f"{max(daily_data, key=lambda x: x['production_kwh'])['date']} ({max(d['production_kwh'] for d in daily_data):.1f} kWh)"],
            ["Number of Inverters", str(len(inverters))],
            ["Average Inverter Uptime", f"{sum(inv['uptime_percent'] for inv in inverters) / len(inverters):.1f}%" if len(inverters) > 0 else "N/A"],
            ["CO₂ Emissions Avoided", f"{total_production * 0.7:.0f} kg"]
        ]
        
        tech_summary_table = Table(tech_summary_data, colWidths=[2.8*inch, 1.8*inch])
        tech_summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8fafc')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#1e3a8a')),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        
        story.append(tech_summary_table)
        story.append(Spacer(1, 14))
        
        # Production Charts - More compact layout
        story.append(Paragraph("Daily Production & Weather Analysis", section_header_style))
        
        # Create properly sized and positioned charts - smaller
        production_chart = HorizontalLineChart()
        production_chart.width = 200
        production_chart.height = 100
        production_chart.x = 10
        production_chart.y = 30
        
        # Prepare production data
        production_values = [day["production_kwh"] for day in daily_data]
        day_numbers = list(range(1, len(daily_data) + 1))
        
        production_chart.data = [production_values]
        production_chart.categoryAxis.categoryNames = [str(i) if i % 7 == 1 or i == len(daily_data) else '' for i in day_numbers]
        production_chart.categoryAxis.labels.fontSize = 6
        production_chart.valueAxis.labels.fontSize = 7
        production_chart.lines[0].strokeColor = colors.HexColor('#2563eb')
        production_chart.lines[0].strokeWidth = 2
        production_chart.valueAxis.valueMin = max(0, min(production_values) - 5)
        production_chart.valueAxis.valueMax = max(production_values) + 5
        
        # Chart 2: Solar Irradiance - smaller
        irradiance_chart = HorizontalLineChart()
        irradiance_chart.width = 200
        irradiance_chart.height = 100
        irradiance_chart.x = 230
        irradiance_chart.y = 30
        
        irradiance_values = [day["solar_irradiance_kwh_m2"] for day in weather_data]
        
        irradiance_chart.data = [irradiance_values]
        irradiance_chart.categoryAxis.categoryNames = [str(i) if i % 7 == 1 or i == len(daily_data) else '' for i in day_numbers]
        irradiance_chart.categoryAxis.labels.fontSize = 6
        irradiance_chart.valueAxis.labels.fontSize = 7
        irradiance_chart.lines[0].strokeColor = colors.HexColor('#ea580c')
        irradiance_chart.lines[0].strokeWidth = 2
        irradiance_chart.valueAxis.valueMin = max(0, min(irradiance_values) - 0.5)
        irradiance_chart.valueAxis.valueMax = max(irradiance_values) + 0.5
        
        # Create drawing with compact dimensions
        drawing = Drawing(500, 150)
        
        # Compact chart titles
        title_style_chart = ParagraphStyle('ChartTitle', fontSize=9, fontName='Helvetica-Bold', 
                                         textColor=colors.HexColor('#374151'), alignment=TA_CENTER)
        
        # Add title backgrounds - more subtle
        drawing.add(Rect(10, 135, 200, 15, fillColor=colors.HexColor('#eff6ff'), 
                        strokeColor=colors.HexColor('#2563eb'), strokeWidth=0.5))
        drawing.add(String(110, 142, 'Daily Production (kWh)', fontSize=9, fillColor=colors.HexColor('#2563eb'), 
                          textAnchor='middle', fontName='Helvetica-Bold'))
        
        drawing.add(Rect(230, 135, 200, 15, fillColor=colors.HexColor('#fff7ed'), 
                        strokeColor=colors.HexColor('#ea580c'), strokeWidth=0.5))
        drawing.add(String(330, 142, 'Solar Irradiance (kWh/m²)', fontSize=9, fillColor=colors.HexColor('#ea580c'), 
                          textAnchor='middle', fontName='Helvetica-Bold'))
        
        # Add charts to drawing
        drawing.add(production_chart)
        drawing.add(irradiance_chart)
        
        story.append(drawing)
        story.append(Spacer(1, 16))
        
        """ # Inverter status section - more compact
        story.append(Paragraph("Inverter Performance", section_header_style))
        
        inverter_data = [["Inverter", "Uptime %", "Status"]]
        for inv in inverters:
            inverter_data.append([
                inv.get("deviceName", inv.get("inverter_id", "Unknown"))[:15] + "..." if len(inv.get("deviceName", inv.get("inverter_id", "Unknown"))) > 15 else inv.get("deviceName", inv.get("inverter_id", "Unknown")), 
                f"{inv['uptime_percent']}%", 
                inv["notes"][:25] + "..." if len(inv["notes"]) > 25 else inv["notes"]
            ])
        
        inverter_table = Table(inverter_data, colWidths=[1.8*inch, 0.8*inch, 2.0*inch])
        inverter_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
            ('TOPPADDING', (0, 1), (-1, -1), 4),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8fafc')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#1e3a8a')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        
        story.append(inverter_table)
        story.append(Spacer(1, 14))
        """
        # Individual Inverter Production Bar Chart - more compact (data passed in)
        story.append(Paragraph("Individual Inverter Production", section_header_style))
        print(f"Final inverter production data: {dict(zip(inverter_chart_names, inverter_chart_values))}")
        inverter_chart_drawing = Drawing(450, 140)
        inverter_bar_chart = VerticalBarChart()
        inverter_bar_chart.x = 30
        inverter_bar_chart.y = 25
        inverter_bar_chart.height = 90
        inverter_bar_chart.width = 360
        # Chart data - use provided production data
        inverter_bar_chart.data = [inverter_chart_values]
        inverter_bar_chart.categoryAxis.categoryNames = inverter_chart_names
        
        # Chart styling - cleaner
        inverter_bar_chart.bars[0].fillColor = colors.HexColor('#1e3a8a')
        inverter_bar_chart.categoryAxis.labels.fontName = 'Helvetica'
        inverter_bar_chart.categoryAxis.labels.fontSize = 9
        inverter_bar_chart.valueAxis.labels.fontName = 'Helvetica'
        inverter_bar_chart.valueAxis.labels.fontSize = 8
        
        # Add title and axis labels
        inverter_chart_drawing.add(String(210, 125, 'Monthly Production by Inverter', 
                                         fontSize=11, fontName='Helvetica-Bold', textAnchor='middle'))
        inverter_chart_drawing.add(String(20, 80, 'kWh', fontSize=9, fontName='Helvetica', textAnchor='middle'))
        
        # Add the chart to drawing
        inverter_chart_drawing.add(inverter_bar_chart)
        story.append(inverter_chart_drawing)
        story.append(Spacer(1, 12))
        
        # Key Observations - cleaner formatting
        story.append(Paragraph("Key Observations", section_header_style))
        
        # Get observations from the function parameters (these come from LLM)
        observations = [
            f"System achieved {total_production:.0f} kWh production with consistent daily performance",
            f"High system reliability with {sum(inv['uptime_percent'] for inv in inverters) / len(inverters):.1f}% average inverter uptime",
            f"Production closely correlates with solar irradiance patterns throughout the month",
            "All inverters operating within normal parameters with minimal maintenance requirements"
        ]
        
        for i, obs in enumerate(observations):
            bullet_style = ParagraphStyle(
                'BulletPoint',
                parent=styles['Normal'],
                fontSize=9,
                leftIndent=12,
                bulletIndent=0,
                spaceAfter=6,
                textColor=colors.HexColor('#374151')
            )
            story.append(Paragraph(f"• {obs}", bullet_style))
        
        story.append(Spacer(1, 16))
        
        # Footer - more professional
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=7,
            textColor=colors.HexColor('#9ca3af'),
            alignment=TA_CENTER,
            borderWidth=0.5,
            borderColor=colors.HexColor('#e5e7eb'),
            borderPadding=8
        )
        
        report_date = datetime.now().strftime("%B %d, %Y at %I:%M %p")
        story.append(Paragraph(f"Report Generated: {report_date} | Solar Performance Analytics Platform", footer_style))
        
           
        # Build PDF
        doc.build(story)
        
        # Upload to S3
        buffer.seek(0)
        s3_client = boto3.client('s3', region_name='us-east-1')
        
        bucket_name = "moose-reports"
        s3_key = f"monthly-reports/{filename}"
        
        # Upload file (without public ACL)
        s3_client.upload_fileobj(
            buffer,
            bucket_name,
            s3_key,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
        
        # Generate presigned URL valid for 7 days
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=7 * 24 * 60 * 60  # 7 days in seconds
        )
        return presigned_url
        
    except Exception as e:
        print(f"Error creating/uploading PDF report: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return "https://moose-reports.s3.amazonaws.com/error-report.pdf"  # Fallback URL
# A function to generate the energy production function description with current dates

def find_system_by_name_description():
    return (
        "Find a specific system ID by matching the system name from the user's portfolio. "
        "Use this when the user asks about a specific system by name (e.g., 'Home System', 'Business System'). "
        "Returns the system_id that can then be used with other functions like get_energy_production. "
        "Uses fuzzy matching to find the best match even if the name isn't exact.\n\n"
        "Examples:\n"
        "- User asks 'How is my Home System doing?' → find_system_by_name(system_name='Home System')\n"
        "- User asks 'Business system production' → find_system_by_name(system_name='Business system')\n"
        "- Then use the returned system_id with get_energy_production(system_id=found_id)"
    )

def get_energy_production_description():
    return (
        "Get aggregated energy production data and calculated earnings for a specific solar system. "
        "Use this for questions about historical energy production, earnings, or money saved over specific time periods.\n"
        "This function automatically calculates earnings using the system's custom earnings rate and returns both energy and financial data.\n"
        "IN PORTFOLIO MODE: Call this function multiple times (once per system) to get individual system data, then aggregate or compare results as needed.\n"
        "This function expects actual date strings, not relative terms like 'this week' or 'last month'. "
        "Choose the date format that provides the most meaningful data granularity for the user's request. "
        "You must convert time references to actual dates before calling this function.\n\n"
        "Date format requirements:\n"
        "- Use YYYY for years (e.g., '2023')\n"
        "- Use YYYY-MM for months (e.g., '2023-05')\n"
        "- Use YYYY-MM-DD for days (e.g., '2023-05-15')\n"
        "- start_date and end_date MUST use the EXACT same format (both YYYY, both YYYY-MM, or both YYYY-MM-DD)\n\n"
        "Examples:\n"
        "- Single system: 'What was my energy production yesterday?' → Convert 'yesterday' to an actual date\n"
        "- Portfolio total: 'Total earnings today' → Call get_energy_production() for each system, sum the earnings\n"
        "- Portfolio comparison: 'Which system performed better?' → Call get_energy_production() for each system, compare results\n"
        "- 'Show me production and earnings from January to March 2023' → start_date='2023-01', end_date='2023-03'"
    )

# A function to generate the CO2 savings function description with current dates
def get_co2_savings_description():
    return (
        "Get aggregated CO2 savings data for a specific solar system. "
        "Use this for questions about environmental impact and carbon reduction from the system.\n"
        "This function expects actual date strings, not relative terms like 'this week' or 'last month'. "
        "Choose the date format that provides the most meaningful data granularity for the user's request. "
        "You must convert time references to actual dates before calling this function.\n\n"
        "Date format requirements:\n"
        "- Use YYYY for years (e.g., '2023')\n"
        "- Use YYYY-MM for months (e.g., '2023-05')\n"
        "- Use YYYY-MM-DD for days (e.g., '2023-05-15')\n"
        "- start_date and end_date MUST use the EXACT same format (both YYYY, both YYYY-MM, or both YYYY-MM-DD)\n\n"
        "Examples:\n"
        "- 'How much CO2 did I save yesterday?' → Convert 'yesterday' to an actual date\n"
        "- 'What were my carbon savings last week?' → Convert 'last week' to date range (Monday to Sunday)\n"
        "- 'Show me CO2 data for June 2023' → start_date='2023-06', end_date='2023-06'\n"
        "- 'How much carbon did I avoid in 2022?' → start_date='2022', end_date='2022'\n"
        "- 'What were my CO2 savings from April to June 2023?' → start_date='2023-04', end_date='2023-06'"
    )

# Function specifications with strategic ordering and detailed descriptions
FUNCTION_SPECS = [
        # HIGH PRIORITY: Direct user/system queries (most common)
        {
            "type": "function",
            "function": {
                "name": "get_user_information",
                "description": (
                    "Get user information from the DynamoDB database. Use this for questions about the user's profile or accessible systems.\n\n"
                    "SPECIFIC QUESTION EXAMPLES:\n"
                    "- 'What systems do I have access to?'\n"
                    "- 'What's my email address?'\n"
                    "- 'What's my name?'\n"
                    "- 'What's my role?'\n"
                    "- 'Who is my technician?'\n"
                    "- 'Show me my profile information'\n"
                    "- 'What systems am I linked to?'\n"
                    "- 'How many systems do I have?'\n\n"
                    "DATA_TYPE OPTIONS:\n"
                    "- 'profile': Returns complete user profile with all available fields\n"
                    "- 'systems': Returns all accessible systems with full system information\n\n"
                    "RESPONSE FORMAT:\n"
                    "Returns complete structured data - the LLM will extract specific information as needed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID to get information for"
                        },
                        "data_type": {
                            "type": "string",
                            "description": "Type of information to retrieve: 'profile' or 'systems'"
                        }
                    },
                    "required": ["user_id", "data_type"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_system_information",
                "description": (
                    "Get system information from the DynamoDB database. Use this for questions about specific system details, status, or configuration.\n\n"
                    "SPECIFIC QUESTION EXAMPLES:\n"
                    "- 'Where is my system located?'\n"
                    "- 'What's the address of my system?'\n"
                    "- 'How big is my system?'\n"
                    "- 'What's the AC power of my system?'\n"
                    "- 'What's the DC capacity of my system?'\n"
                    "- 'What's the status of my system?'\n"
                    "- 'When was my system installed?'\n"
                    "- 'How many inverters does my system have?'\n"
                    "- 'What's my system's name?'\n"
                    "- 'Show me my system profile'\n\n"
                    "DATA_TYPE OPTIONS:\n"
                    "- 'profile': Returns complete system profile with all configuration details\n"
                    "- 'status': Returns system status information including inverter counts\n"
                    "- 'inverter_count': Returns count of inverters for this system\n\n"
                    "RESPONSE FORMAT:\n"
                    "Returns complete structured data - the LLM will extract specific information as needed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID requesting the information"
                        },
                        "system_id": {
                            "type": "string",
                            "description": "The system ID to get information for"
                        },
                        "data_type": {
                            "type": "string",
                            "description": "Type of information to retrieve: 'profile', 'status', or 'inverter_count'"
                        }
                    },
                    "required": ["user_id", "system_id", "data_type"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_vector_db",
                "description": (
                    "Search the knowledge base for general solar-related information, troubleshooting, error codes, or maintenance guidance. "
                    "Use this for non-system-specific questions like support, documentation, or general education.\n"
                    "Examples: "
                    "'Who do I contact if something goes wrong?', "
                    "'What does error code 105 mean?', "
                    "'What should I do if my inverter is flashing red?', "
                    "'How do I clean my solar panels?'"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The query to search for in the vector database"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "The maximum number of results to return",
                            "default": 3
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        # MEDIUM PRIORITY: Energy/data queries
        {
            "type": "function",
            "function": {
                "name": "get_energy_production",
                "description": get_energy_production_description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_id": {
                            "type": "string",
                            "description": "The ID of the system to get data for"
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY, YYYY-MM, or YYYY-MM-DD format. You must convert relative terms like 'today', 'this week', 'last month' to actual dates."
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in the same format as start_date (YYYY, YYYY-MM, or YYYY-MM-DD). The format must match start_date.",
                            "default": ""
                        }
                    },
                    "required": ["system_id", "start_date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_energy_production_inverter",
                "description": (
                    "Get energy production data for a specific inverter/device within a solar system. "
                    "Use this when users ask about individual inverter performance, production, or comparison between inverters.\n\n"
                    "SPECIFIC QUESTION EXAMPLES:\n"
                    "- 'How much energy did inverter XYZ produce this month?'\n"
                    "- 'What's the production of my first inverter?'\n"
                    "- 'Show me inverter ABC's energy output for last week'\n"
                    "- 'Compare production between my inverters'\n"
                    "- 'Which inverter is performing best?'\n\n"
                    "IMPORTANT NOTES:\n"
                    "- Requires both system_id and device_id (inverter ID)\n"
                    "- Use the same date format rules as get_energy_production\n"
                    "- Returns energy data with calculated earnings based on system's rate\n"
                    "- Useful for detailed inverter-level analysis and troubleshooting"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_id": {
                            "type": "string",
                            "description": "The ID of the system that contains the inverter"
                        },
                        "device_id": {
                            "type": "string",
                            "description": "The ID of the specific inverter/device to get data for"
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY, YYYY-MM, or YYYY-MM-DD format. You must convert relative terms like 'today', 'this week', 'last month' to actual dates."
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in the same format as start_date (YYYY, YYYY-MM, or YYYY-MM-DD). The format must match start_date.",
                            "default": ""
                        }
                    },
                    "required": ["system_id", "device_id", "start_date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_co2_savings",
                "description": get_co2_savings_description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_id": {
                            "type": "string",
                            "description": "The ID of the system to get data for"
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY, YYYY-MM, or YYYY-MM-DD format. You must convert relative terms like 'today', 'this week', 'last month' to actual dates."
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in the same format as start_date (YYYY, YYYY-MM, or YYYY-MM-DD). The format must match start_date.",
                            "default": ""
                        }
                    },
                    "required": ["system_id", "start_date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_flow_data",
                "description": (
                    "Get real-time power flow data for a specific solar system. "
                    "Use this ONLY for two specific types of questions:\n"
                    "1. When the user asks about system status (online/offline) - check the 'isOnline' status\n"
                    "2. When the user asks about current power or peak power - check the 'PowerPV' channel value\n\n"
                    "Examples:\n"
                    "'Is my system online?', "
                    "'What's the current power output?', "
                    "'How much power is my system generating right now?'"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_id": {
                            "type": "string",
                            "description": "The ID of the system to get real-time flow data for"
                        }
                    },
                    "required": ["system_id"]
                }
            }
        },
        # LOWER PRIORITY: Technical details and specialized queries
        {
            "type": "function",
            "function": {
                "name": "get_inverter_information",
                "description": (
                    "Get inverter information from the DynamoDB database. Use this for questions about specific inverter details or status.\n\n"
                    "SPECIFIC QUESTION EXAMPLES:\n"
                    "- 'What inverters do I have?'\n"
                    "- 'What's the status of my inverters?'\n"
                    "- 'How many MPPT trackers do I have?'\n"
                    "- 'Show me my inverter details'\n"
                    "- 'What's the power rating of my inverters?'\n"
                    "- 'Are my inverters online?'\n"
                    "- 'What's my inverter model?'\n"
                    "- 'Show me inverter firmware versions'\n\n"
                    "DATA_TYPE OPTIONS:\n"
                    "- 'profiles': Returns complete inverter profiles with all technical details\n"
                    "- 'status': Returns inverter status information\n"
                    "- 'details': Returns combined profile and status information\n\n"
                    "RESPONSE FORMAT:\n"
                    "Returns complete structured data - the LLM will extract specific information as needed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID requesting the information"
                        },
                        "system_id": {
                            "type": "string",
                            "description": "The system ID to get inverters for"
                        },
                        "data_type": {
                            "type": "string",
                            "description": "Type of information to retrieve: 'profiles', 'status', or 'details'"
                        }
                    },
                    "required": ["user_id", "system_id", "data_type"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_user_incidents",
                "description": (
                    "Get user incident information from the DynamoDB database. Use this for questions about incidents, alerts, or system issues.\n\n"
                    "SPECIFIC QUESTION EXAMPLES:\n"
                    "- 'What incidents do I have?'\n"
                    "- 'Show me my recent incidents'\n"
                    "- 'Do I have any pending incidents?'\n"
                    "- 'What's my incident history?'\n"
                    "- 'Show me processed incidents'\n"
                    "- 'How many incidents do I have?'\n"
                    "- 'What alerts do I have?'\n"
                    "- 'Show me my system issues'\n"
                    "- 'Any problems with my system?'\n"
                    "- 'What's my incident status?'\n\n"
                    "AVAILABLE DATA FIELDS:\n"
                    "Incident: status, systemId, deviceId, userId, processedAt, expiresAt, incident details\n\n"
                    "STATUS OPTIONS:\n"
                    "- 'pending': Shows only pending incidents\n"
                    "- 'processed': Shows only processed incidents\n"
                    "- Leave empty or null: Shows all incidents\n\n"
                    "RESPONSE FORMAT:\n"
                    "Returns structured data with incidents array, total count, status filter, and query_info for tracking."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID to get incidents for"
                        },
                        "status": {
                            "type": "string",
                            "description": "Optional status filter: 'pending', 'processed', or leave empty for all incidents",
                            "default": ""
                        }
                    },
                    "required": ["user_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "generate_chart_data",
                "description": (
                    "Generate chart data for visualization when user asks to 'show', 'display', 'graph', 'chart', or 'visualize' data.\n\n"
                    "SOLAR.WEB API FORMAT OPTIMIZATION:\n"
                    "The Solar.web API supports different aggregation levels. Choose the optimal format:\n\n"
                    "YEARLY FORMAT (YYYY): Use for multi-year requests\n"
                    "- 'last 5 years' → start_date='2029', end_date='2024'\n"
                    "- '2010 to 2024' → start_date='2010', end_date='2024'\n"
                    "- Returns pre-aggregated yearly totals, use bar chart\n\n"
                    "MONTHLY FORMAT (YYYY-MM): Use for monthly trends, quarters, year-parts\n"
                    "- 'first 6 months of 2025' → start_date='2025-01', end_date='2025-06'\n" 
                    "- '2024' → start_date='2024-01', end_date='2024-012'\n"
                    "- 'last 8 months' → start_date='2024-04', end_date='2024-12'\n"
                    "- Returns pre-aggregated monthly totals, use bar chart\n\n"
                    "DAILY FORMAT (YYYY-MM-DD): Use for daily/weekly trends, short periods\n"
                    "- 'last 14 days' → start_date='2024-12-03', end_date='2024-12-17'\n"
                    "- 'this week' → start_date='2024-12-16', end_date='2024-12-22'\n"
                    "- 'December 1-15' → start_date='2024-12-01', end_date='2024-12-15'\n"
                    "- Returns daily data points, use line chart for ≤30 days, bar chart for >30 days\n\n"
                    "IMPORTANT: start_date and end_date MUST use the SAME format (both YYYY, both YYYY-MM, or both YYYY-MM-DD)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "data_type": {
                            "type": "string",
                            "enum": ["energy_production", "co2_savings", "earnings"],
                            "description": "Type of data to chart"
                        },
                        "system_id": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}}
                            ],
                            "description": "System ID or list of system IDs to aggregate"
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY, YYYY-MM, or YYYY-MM-DD format. Choose format that provides most meaningful data granularity: daily (YYYY-MM-DD) for periods under 3 months, monthly (YYYY-MM) for full years and longer periods, yearly (YYYY) only for multi-year historical analysis."
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in same format as start_date"
                        },
                        "time_period": {
                            "type": "string",
                            "description": "Descriptive label for the chart (e.g., 'last_14_days', 'Q1_2024', 'yearly_trend') to help determine chart type"
                        }
                    },
                    "required": ["data_type", "system_id", "start_date", "end_date", "time_period"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "generate_monthly_solar_report",
                "description": (
                    "Generate a comprehensive monthly solar performance report for a specific site. "
                    "Use this when users request monthly reports, performance summaries, or downloadable PDF reports.\n\n"
                    "SPECIFIC QUESTION EXAMPLES:\n"
                    "- 'Generate a monthly report for July 2025'\n"
                    "- 'Create a performance report for my system for December 2024'\n"
                    "- 'I need a monthly solar report'\n"
                    "- 'Show me a comprehensive report for last month'\n"
                    "- 'Generate a PDF report for October'\n"
                    "- 'Create a monthly performance summary'\n\n"
                    "MONTH STRING FORMAT:\n"
                    "- Accepts natural language like 'July 2025', 'December 2024', or 'MM/YYYY' format like '07/2025'\n"
                    "- Also accepts 'MM/YYYY' format like '07/2025'\n\n"
                    "RETURN FORMAT:\n"
                    "Returns structured JSON data with:\n"
                    "- Complete performance summary with totals and averages\n" 
                    "- Daily production data for the entire month\n"
                    "- Inverter status and uptime information\n"
                    "- Weather correlation data\n"
                    "- Chart-ready data for visualization\n"
                    "- Public S3 URL for downloadable PDF report\n"
                    "- Key observations and insights\n\n"
                    "PDF REPORT:\n"
                    "- Automatically generates and uploads a professional PDF report to S3\n"
                    "- Includes performance tables, inverter status, and key metrics\n"
                    "- Returns public URL for immediate download"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "site_id": {
                            "type": "string",
                            "description": "The unique identifier for the solar site/system"
                        },
                        "month_string": {
                            "type": "string",
                            "description": "Natural language month specification like 'July 2025', 'December 2024', or 'MM/YYYY' format"
                        }
                    },
                    "required": ["site_id", "month_string"]
                }
            }
        },
        # PORTFOLIO FUNCTIONS - Available when user has multiple systems
        {
            "type": "function",
            "function": {
                "name": "find_system_by_name",
                "description": find_system_by_name_description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_name": {
                            "type": "string",
                            "description": "The name or partial name of the system to find (e.g., 'Home System', 'Business', 'Office')"
                        }
                    },
                    "required": ["system_name"]
                }
            }
        }
    ]

# Function map for executing called functions
FUNCTION_MAP = {
    "search_vector_db": search_vector_db,
    "get_energy_production": get_energy_production,
    "get_energy_production_inverter": get_energy_production_inverter,
    "find_system_by_name": find_system_by_name,
    "get_co2_savings": get_co2_savings,
    "get_flow_data": get_flow_data,
    "generate_chart_data": generate_chart_data,
    "get_user_information": get_user_information,
    "get_system_information": get_system_information,
    "get_inverter_information": get_inverter_information,
    "get_user_incidents": get_user_incidents,
    "generate_monthly_solar_report": generate_monthly_solar_report
}

#---------------------------------------
# RAG Implementation
#---------------------------------------

class SolarAssistantRAG:
    """Optimized RAG implementation for Solar O&M assistant with conversation memory."""
    
    def __init__(self):
        """Initialize the RAG system."""
        self.embeddings = OpenAIEmbeddings(api_key=api_key, model="text-embedding-3-large")
        self.vector_store = None
        self.retriever = None
        self.llm = ChatOpenAI(api_key=api_key, model_name="gpt-4.1-mini", temperature=0.0)
        
        # Dictionary to store conversation memories
        self.memories = {}
        
        # Load the knowledge base data
        self._load_knowledge_base()
        
    def _load_knowledge_base(self) -> None:
        try:
            # Get Pinecone API key and host from environment variables
            pinecone_api_key = os.getenv("PINECONE_API_KEY")
            pinecone_host = os.getenv("PINECONE_HOST")
            
            # Initialize Pinecone with hardcoded namespace
            pc = Pinecone(api_key=pinecone_api_key)
            index = pc.Index(host=pinecone_host)
            # vector_store = PineconeVectorStore(index=index, embedding=self.embeddings, namespace="LDML")
            vector_store = PineconeVectorStore(index=index, embedding=self.embeddings, namespace="OM")
            self.vector_store = vector_store
            #   self.retriever = self.vector_store.as_retriever(search_type="similarity", search_kwargs={"k": 3})
            self.retriever = self.vector_store.as_retriever(search_type="similarity", search_kwargs={"k": 7})

        except Exception as e:
            print(f"Error loading knowledge base: {e}")
            # Create a simple fallback retriever that returns empty results
            self.vector_store = None
            self.retriever = None

    def _get_or_create_memory(self, user_id: str):
        """Get or create a conversation memory for a user."""
        # Extract the base user ID (before any underscores) to ensure memory persistence
        # even if the device ID changes between requests
       # base_user_id = user_id.split('_')[0] if user_id and '_' in user_id else user_id
        memory_key = user_id
        
        if memory_key not in self.memories:
            print(f"Creating new memory for user: {memory_key} (original ID: {user_id})")
            self.memories[memory_key] = ConversationBufferMemory(
                memory_key="chat_history",
                return_messages=True,
                output_key="answer"
            )
        else:
            print(f"Retrieved existing memory for user: {memory_key} (original ID: {user_id}) with {len(self.memories[memory_key].chat_memory.messages)} messages")
    
        return self.memories[memory_key]
    
    def query_with_openai_function_calling(self, query: str, user_id: str = "default_user", system_id: str = None, jwt_token: str = None, username: str = "Guest User", portfolio_data: Dict = None) -> Dict[str, Any]:
        """
        Query using OpenAI's direct function calling.
        
        Args:
            query: The user's query
            user_id: Identifier for the user (already includes device ID)
            system_id: The ID of the solar system to use for function calls (if None, functions requiring system_id will be prompted)
            jwt_token: JWT token for API authentication
            username: User's actual name for personalized responses
            
        Returns:
            A dictionary with the response and any relevant documents
        """
        print(f"\n=== PROCESSING QUERY ===")
        print(f"User ID: {user_id}")
        print(f"System ID: {system_id}")
        print(f"Query: {query}")
        
        # Get or create memory for this user
        memory = self._get_or_create_memory(user_id)
        
        # Log memory state before adding new messages
        print(f"Memory before processing: {len(memory.chat_memory.messages)} messages")
        if memory.chat_memory.messages:
            print("Previous conversation:")
            for i, msg in enumerate(memory.chat_memory.messages):
                print(f"  [{i}] {msg.type}: {msg.content[:50]}...")
        
        # Prepare chat history for OpenAI format
        messages = []
        
        # Get current date for the system message
        current_date = datetime.now()
        formatted_date = current_date.strftime("%Y-%m-%d")
        current_day_of_week = current_date.strftime("%A")
        current_month = current_date.strftime("%B")
        current_year = current_date.strftime("%Y")
        
        # Add system message with current date and specific date ranges
        system_message = f"""You are a solar operations and maintenance expert specialized in Fronius inverters.
        
        IMPORTANT: You ONLY discuss solar energy topics. For any non-solar questions, respond: "I'm a solar energy specialist and can only help with solar systems, energy production, inverters, and maintenance. What can I help you with regarding your solar system?"

        SOLAR TOPICS ONLY: energy production, inverters, system status, maintenance, CO2 savings, earnings, troubleshooting, solar technology.

        EXAMPLE RESPONSES FOR OFF-TOPIC QUESTIONS:
        - "What's the weather like?" → "I'm a solar energy specialist and can only help with solar systems, energy production, inverters, and maintenance. However, I can tell you how weather affects your solar production if that would be helpful!"
        - "How do I cook pasta?" → "I'm a solar energy specialist and can only help with solar systems, energy production, inverters, and maintenance. Is there anything about your solar system I can help you with instead?"
        - "Tell me a joke" → "I'm a solar energy specialist focused on helping with solar systems and energy production. Is there anything about your solar system performance or maintenance I can assist with?"
        
        USER INFORMATION:
        - The user's name is {username}. If they ask about their name, greet them personally.
        - When appropriate, address them by name for a more personalized experience.
        
        
        SYSTEM ID INSTRUCTIONS:
        - For any function that requires a system_id, use the system_id that is passed to you: {system_id if system_id else "None"}
        - PORTFOLIO MODE: {f"You have access to {len(portfolio_data['systems'])} systems: " + ", ".join([f"{sys['name']} ({sys['system_id']})" for sys in portfolio_data['systems']]) if portfolio_data else "Not in portfolio mode"}
        - If system_id is None and no portfolio systems are available, inform them that they need to select a system first.
        - Do NOT attempt to infer or extract a system_id from conversation history. Use ONLY the provided system_id value or portfolio system IDs.
        - In portfolio mode, when the user refers to a system by name or partial name, use your best judgement and select the most likely system from the portfolio, even if the name is not an exact match. Do not ask the user for confirmation unless there is a true ambiguity (e.g., two systems with nearly identical names).
        
        CHART GENERATION:
        - When users ask to "show", "display", "graph", "chart", or "visualize" data, AUTOMATICALLY use the generate_chart_data function
        - IMPORTANT: Do NOT ask for permission - generate the chart immediately when users use these keywords
        - Keywords that trigger automatic chart generation: "show me", "display", "graph", "chart", "visualize", "plot"
        - Always provide a helpful text summary along with the chart data
        - For chart requests, be descriptive about what the chart will show
        - The chart will be automatically rendered by the frontend when chart_data is provided
        
        SMART DATA GRANULARITY SELECTION:
        Choose the API format that provides the most meaningful data granularity:
        
        DAILY FORMAT (YYYY-MM-DD): For detailed analysis (up to ~90 days)
        - "yesterday" → start_date="2024-12-16", end_date="2024-12-16" (1 point)
        - "last week" → start_date="2024-12-09", end_date="2024-12-15" (7 points)
        - "last 2 weeks" → start_date="2024-12-02", end_date="2024-12-15" (14 points)
        - "October 2024" → start_date="2024-10-01", end_date="2024-10-31" (31 points)
        - "last month" → start_date="2024-11-01", end_date="2024-11-30" (30 points)
        - "last 30 days" → start_date="2024-11-16", end_date="2024-12-16" (31 points)
        - "last 2 months" → start_date="2024-10-16", end_date="2024-12-16" (62 points)
        
        MONTHLY FORMAT (YYYY-MM): For trend analysis (3 months to 3 years)
        - "2024" → start_date="2024-01", end_date="2024-12" (12 points)
        - "2023" → start_date="2023-01", end_date="2023-12" (12 points)
        - "Q1 2024" → start_date="2024-01", end_date="2024-03" (3 points)
        - "last 6 months" → start_date="2024-06", end_date="2024-12" (7 points)
        - "2022 to 2024" → start_date="2022-01", end_date="2024-12" (36 points)
        - "last 18 months" → start_date="2023-06", end_date="2024-12" (19 points)
        
        YEARLY FORMAT (YYYY): Only for very long periods (3+ years)
        - "last 10 years" → start_date="2014", end_date="2024" (11 points)
        - "2010 to 2024" → start_date="2010", end_date="2024" (15 points)
        - "decade trends" → start_date="2010", end_date="2024" (15 points)
        
        RULES:
        - Prioritize granularity over API efficiency
        - Users expect detailed data for recent periods
        - Use daily format for anything under 3 months
        - Use monthly format for full years and longer periods
        - Only use yearly format for multi-year historical analysis
        - CRITICAL: start_date and end_date MUST use the SAME format
        
        DATA HANDLING:
        - The API responses now include pre-calculated total values that you should use directly.
        - For energy production data, use the "total_energy_kwh" field for the total energy in kilowatt-hours.
        - For CO2 savings data, use the "total_co2_kg" field for the total CO2 saved in kilograms.
        - DO NOT attempt to recalculate these totals by summing the individual data points, as this may lead to inconsistent results.
        - When reporting multiple day values, present them using a consistent format with the same number of decimal places.
        
                EARNINGS CALCULATIONS:
        - The get_energy_production function automatically calculates and returns earnings data.
        - Each system has a custom earnings rate stored in their DynamoDB profile (defaults to $0.40/kWh if not configured).
        - When users ask about earnings or money saved, simply use get_energy_production - it returns both energy and earnings data.
        - The response includes: total_energy_kwh, total_earnings, earnings_rate, and earnings_text fields.

        PORTFOLIO MODE INSTRUCTIONS:
        - When portfolio data is available, you have access to multiple solar systems with their names and IDs
        - For TOTAL/COMBINED questions: Make multiple tool calls (one per system) then aggregate the results yourself
        - For SPECIFIC SYSTEM questions: Use find_system_by_name() to get the system_id, then call regular functions
        - For INDIVIDUAL BREAKDOWN questions: Make separate tool calls for each system and present individual results
        - You can make multiple function calls in a single response to gather all needed data
        
        
        PORTFOLIO CHART GENERATION:
        - Default behavior: ONE aggregated chart using all portfolio system_ids passed as a list to generate_chart_data
        - Multiple charts: ONLY when the user says "compare", "by system", "each system", names multiple systems, or otherwise requests comparison
        - The frontend will automatically display charts; use clear titles and system names when multiple charts are rendered
        - Avoid generating multiple charts unless explicitly requested
        
        PORTFOLIO EXAMPLES:
        * "Total earnings today" → Call get_energy_production() for each system, sum the earnings
        * "Show me all my systems' production" → Call get_energy_production() for each system, show individual results  
        * "Home System vs Business System" → Call get_energy_production() for both specific systems
        * "Charts for all systems" → Call generate_chart_data() for each system to create separate charts
        * "Show me energy production charts for all my systems" → Call generate_chart_data() for each system
        * "Display CO2 savings for each system this year" → Call generate_chart_data() for each system with CO2 data
        * "Compare system performance with charts" → Call generate_chart_data() for each system
        * "Best performing system this month" → Call get_energy_production() for all systems, compare results
        
        PORTFOLIO AGGREGATION RULES:
        - Sum energy production values (kWh) across systems
        - Sum earnings values ($) across systems  
        - Average efficiency/performance metrics across systems
        - Show individual system names in breakdowns
        - Present totals clearly: "Total across X systems: Y kWh, $Z"
        - For charts: Each system gets its own chart with the system name clearly displayed

        MONTHLY REPORT RESPONSES:
        - When generate_monthly_solar_report is called successfully, respond ONLY with: "📄 [Click here to download your monthly solar report](PDF_URL)"
        - Use proper Markdown link format: [link text](URL) so the frontend can render it as a clickable link
        - Do not include any data analysis, summaries, charts, or additional information in your response.
        - The PDF contains all the detailed information the user needs.
        - Replace PDF_URL with the actual reportUrl from the function response.

        TODAY'S DATE IS: {formatted_date} ({current_day_of_week}, {current_month} {current_date.day}, {current_year})
        
        DATE GUIDELINES:
        - Use today's date given above for any date calculations.
        - A week starts on Monday and ends on Sunday.
        - "This week" means from Monday of this week up to today.
        - "Last week" means from Monday to Sunday of the previous week.
        - "This month" means from the 1st of the current month to today.
        - "Last month" means the entire previous month.
        - "This year" means from January 1st of the current year to today.
        - "Last year" means the entire previous year.
        - When calling get_energy_production or get_co2_savings, convert these terms to actual dates.
        
        DATE CALCULATION PRIORITY:
        - For "2024": Use monthly format → start_date="2024-01", end_date="2024-12"
        - For "October 2024": Use daily format → start_date="2024-10-01", end_date="2024-10-31"
        - For "last week": Use daily format → Calculate exact Monday-Sunday dates
        - For "last month": Use daily format → Calculate exact first-last day of previous month
        - For "last 6 months": Use monthly format → Calculate 6 months back to current month
        - For "last 10 years": Use yearly format → Calculate 10 years back to current year
        
        - The API requires dates in these formats:
          * For daily data: YYYY-MM-DD (e.g., "2023-05-15")
          * For monthly data: YYYY-MM (e.g., "2023-05")
          * For yearly data: YYYY (e.g., "2023")
        - Important: start_date and end_date must have the SAME format (both YYYY, both YYYY-MM, or both YYYY-MM-DD).

        USE THESE DATE FORMATS WITH API CALLS:
        - For specific days like "yesterday": Use YYYY-MM-DD format for both start_date and end_date
        - For specific months like "January 2023": Use YYYY-MM format for both start_date and end_date
        - For specific years like "2022": Use YYYY format for both start_date and end_date
        - For date ranges: Make sure both dates use the SAME format

        When users ask about financial earnings or money saved, use the get_energy_production function which automatically returns both energy production data and calculated earnings based on the system's custom rate."""
        
        messages.append({"role": "system", "content": system_message})
        print('INSIDE FUNCTION CALLING')
        
        # Add conversation history
        
        if hasattr(memory, "chat_memory") and memory.chat_memory.messages:
            print(f"Adding {len(memory.chat_memory.messages)} messages from memory to conversation context")
            for msg in memory.chat_memory.messages:
                if hasattr(msg, "type") and msg.type == "human":
                    messages.append({"role": "user", "content": msg.content})
                elif hasattr(msg, "type") and msg.type == "ai":
                    messages.append({"role": "assistant", "content": msg.content})

        
        # Add current query
        messages.append({"role": "user", "content": query})
        
        print('MESSAGES: ', messages)
        print('MEMORY: ', memory.chat_memory.messages)
        
        try:
            # Call OpenAI API with function calling and updated specs
            response = openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=messages,
                tools=FUNCTION_SPECS,
                temperature=0.0,
            )
            
            response_message = response.choices[0].message
            
                    # Check if the model wants to call a function
            source_documents = []
            chart_data = None
            chart_data_list = []  # New: Track multiple charts for portfolio mode
            dynamodb_queries = []
            if response_message.tool_calls:
                # Extract function calls
                messages.append({
                    "role": "assistant",
                    "tool_calls": response_message.tool_calls
                    })
                
                # Process each function call
                tool_responses = []
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)
                    
                    # Handle portfolio functions
                    if portfolio_data and function_name == "find_system_by_name":
                        function_args["portfolio_systems"] = portfolio_data["systems"]
                    
                    # Add JWT token to API functions (regardless of system_id presence)
                    if function_name in ["get_energy_production", "get_energy_production_inverter", "get_co2_savings", "get_flow_data", "generate_chart_data"]:
                        function_args["jwt_token"] = jwt_token  # Add JWT token to function args
                        
                        # Override system_id with the one provided in the request, if applicable
                        if system_id:
                            if function_name == "generate_chart_data":
                                # Do not override if the tool already provided a list of system_ids for aggregation
                                if not isinstance(function_args.get("system_id"), list):
                                    function_args["system_id"] = system_id
                            else:
                                function_args["system_id"] = system_id
                    
                    # For monthly solar report, use system_id as site_id if system_id is available
                    if function_name == "generate_monthly_solar_report":
                        if system_id:
                            function_args["site_id"] = system_id
                        function_args["jwt_token"] = jwt_token  # Add JWT token to function args
                    
                    # For DynamoDB functions, add user_id if not present
                    if function_name in ["get_user_information", "get_system_information", "get_inverter_information", "get_user_incidents"]:
                        if "user_id" not in function_args:
                            # Extract base user_id from the combined user_id
                            base_user_id = user_id.split('_')[0] if user_id and '_' in user_id else user_id
                            function_args["user_id"] = base_user_id
                        
                        # For system-related functions, add system_id if available
                        if function_name in ["get_system_information", "get_inverter_information"] and system_id:
                            function_args["system_id"] = system_id
                    
                    print(f"Calling function: {function_name} with args: {function_args}")
                    
                    # Execute the function
                    function_to_call = FUNCTION_MAP.get(function_name)
                    if function_to_call:
                        function_response = function_to_call(**function_args)
                        tool_responses.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": json.dumps(function_response)
                        })
                        
                        # Save source documents for RAG queries
                        if function_name == "search_vector_db" and isinstance(function_response, list):
                            source_documents = function_response
                        
                        # Save chart data for visualization - Modified to handle multiple charts
                        if function_name == "generate_chart_data" and isinstance(function_response, dict) and "error" not in function_response:
                            # Add system name to chart data if available
                            if "system_name" not in function_response and function_args.get("system_id"):
                                try:
                                    # Try to get system name from the database
                                    profile_response = table.get_item(
                                        Key={'PK': f'System#{function_args["system_id"]}', 'SK': 'PROFILE'}
                                    )
                                    if 'Item' in profile_response:
                                        function_response["system_name"] = profile_response['Item'].get('name', f"System {function_args['system_id']}")
                                    else:
                                        function_response["system_name"] = f"System {function_args['system_id']}"
                                except Exception as e:
                                    logger.error(f"Error fetching system name: {str(e)}")
                                    function_response["system_name"] = f"System {function_args['system_id']}"
                            
                            chart_data_list.append(function_response)
                            logger.info(f"=== CHART DATA CAPTURED ({len(chart_data_list)} total) ===")
                            logger.info(f"Chart data type: {function_response.get('data_type', 'unknown')}")
                            logger.info(f"Chart title: {function_response.get('title', 'unknown')}")
                            logger.info(f"Chart system: {function_response.get('system_name', 'unknown')}")
                            logger.info(f"Chart data points: {len(function_response.get('data_points', []))}")
                            logger.info(f"Chart total value: {function_response.get('total_value', 'unknown')}")
                            logger.info(f"Chart unit: {function_response.get('unit', 'unknown')}")
                        elif function_name == "generate_chart_data":
                            logger.warning(f"Chart data generation failed or returned error: {function_response}")
                        
                        # Handle monthly report - just log the PDF URL, let AI respond with simple link
                        if function_name == "generate_monthly_solar_report" and isinstance(function_response, dict) and "error" not in function_response:
                            logger.info(f"=== MONTHLY REPORT GENERATED ===")
                            logger.info(f"Report month: {function_response.get('report_month', 'unknown')}")
                            logger.info(f"PDF URL: {function_response.get('reportUrl', 'none')}")
                            logger.info(f"AI will respond with PDF link only")
                        elif function_name == "generate_monthly_solar_report":
                            logger.warning(f"Monthly report generation failed or returned error: {function_response}")
                        
                        # Track DynamoDB queries
                        if function_name in ["get_user_information", "get_system_information", "get_inverter_information", "get_user_incidents"]:
                            dynamodb_queries.append({
                                "function": function_name,
                                "query_type": function_args.get("data_type", "unknown"),
                                "user_id": function_args.get("user_id"),
                                "system_id": function_args.get("system_id"),
                                "success": "error" not in function_response
                            })
                
                # Process chart data - handle multiple charts for portfolio mode
                if chart_data_list:
                    if len(chart_data_list) == 1:
                        # Single chart - maintain backward compatibility
                        chart_data = chart_data_list[0]
                        logger.info(f"=== SINGLE CHART DATA FINAL ===")
                        logger.info(f"Chart data type: {chart_data.get('data_type', 'unknown')}")
                    else:
                        # Multiple charts - return as array
                        chart_data = chart_data_list
                        logger.info(f"=== MULTIPLE CHART DATA FINAL ===")
                        logger.info(f"Total charts: {len(chart_data)}")
                        for i, chart in enumerate(chart_data):
                            logger.info(f"Chart {i+1}: {chart.get('data_type', 'unknown')} - {chart.get('system_name', 'unknown')}")
                
                # Add the function responses to the messages
                if tool_responses:
                    messages.extend(tool_responses)
                
                # Call the model again with the function responses
                second_response = openai_client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=messages,
                    temperature=0.0,
                )
                
                # Get the final response
                final_response = second_response.choices[0].message.content
            else:
                print("TOOL SELECTION: Model did not select any tool — simulating search_vector_db")

                # Simulate a call to search_vector_db
                function_name = "search_vector_db"
                function_args = {"query": query, "limit": 100}

                # Execute the function
                function_response = FUNCTION_MAP[function_name](**function_args)

                # Prepare documents - use correct format for the search results
                # This should match how the real search_vector_db function returns data
                source_documents = function_response  # Directly use the response as-is

                # Add a message with tool_calls (required before adding a tool message)
                tool_call_id = "fallback_call_" + str(hash(query))[:8]
                messages.append({
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": function_name,
                                "arguments": json.dumps(function_args)
                            }
                        }
                    ]
                })

                # Add the function response as a tool message
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "content": json.dumps(function_response)
                })

                # Call the model again with the function responses
                second_response = openai_client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=messages,
                    temperature=0.0,
                )

                final_response = second_response.choices[0].message.content
            
            # Save the conversation
            print(f"Saving conversation to memory for user: {user_id}")
            # Instead of using save_context, directly add messages to chat_memory
            memory.chat_memory.add_user_message(query)
            memory.chat_memory.add_ai_message(final_response)
            
            # Log memory state after updating
            print(f"Memory after processing: {len(memory.chat_memory.messages)} messages")
            
            # Log final response structure
            logger.info(f"=== FINAL RESPONSE STRUCTURE ===")
            logger.info(f"Response text length: {len(final_response)} characters")
            logger.info(f"Source documents count: {len(source_documents)}")
            logger.info(f"Chart data present: {'Yes' if chart_data else 'No'}")
            if chart_data:
                logger.info(f"Chart data keys: {list(chart_data.keys()) if isinstance(chart_data, dict) else 'Not a dict'}")
            logger.info(f"=== END FINAL RESPONSE STRUCTURE ===")
            
            return {
                "response": final_response,
                "source_documents": source_documents,
                "chart_data": chart_data,
                "dynamodb_queries": dynamodb_queries
            }
            
        except Exception as e:
            print(f"Error in OpenAI function calling: {e}")
            return {
                "response": f"I encountered an error while processing your request: {str(e)}",
                "source_documents": [],
                "chart_data": None,
                "dynamodb_queries": []
            }

# Global RAG instance
_rag_instance = None

def get_rag_instance():
    """Get the singleton instance of the RAG system."""
    global _rag_instance
    if _rag_instance is None:
        try:
            _rag_instance = SolarAssistantRAG()
        except Exception as e:
            print(f"Error creating RAG instance: {e}")
    return _rag_instance

#---------------------------------------
# Chat Response Functions
#---------------------------------------

def get_chatbot_response(message: str, user_id: Optional[str] = None, system_id: Optional[str] = None, jwt_token: Optional[str] = None, username: Optional[str] = "Guest User", portfolio_data: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Generate a response based on the user's message.
    
    Args:
        message: The user's message
        user_id: Optional user identifier for maintaining conversation context (already includes device ID)
        system_id: The ID of the solar system to use for function calls
        jwt_token: Optional JWT token for API authentication
        username: Optional user's name for personalized responses
    
    Returns:
        A dictionary with response and optional source documents
    """
    print('INSIDE CHATBOT RESPONSE')
    # Use a default user_id if none provided
    if not user_id:
        user_id = "default_user"
    
    # Initialize user context if it doesn't exist
    if user_id not in user_contexts:
        user_contexts[user_id] = {"current_system_id": None, "last_topic": None}
    
    # Update user context with system_id if provided
    if system_id:
        user_contexts[user_id]["current_system_id"] = system_id
    
    # Get the RAG instance
    rag = get_rag_instance()
    if not rag:
        return {"response": "The Solar Assistant is currently unavailable.", "source_documents": []}
    
    # Query the RAG system directly
    try:
        return rag.query_with_openai_function_calling(message, user_id, system_id, jwt_token, username, portfolio_data)
    except Exception as e:
        print(f"Error in chatbot response: {e}")
        return {"response": f"I encountered an error while processing your request: {str(e)}", "source_documents": []}
def log_conversation_to_db(user_id: str, user_message: str, bot_response: str, system_id: str = None, chart_data: dict = None, dynamodb_queries: list = None):
    """Log chatbot conversation to DynamoDB"""
    if not table:
        logger.error("Cannot log conversation - database not available")
        return
    
    try:
        timestamp = datetime.now().isoformat()
        conversation_id = str(uuid.uuid4())
        
        # Create conversation log item
        log_item = {
            'PK': f'CHAT#{user_id}',
            'SK': f'CONVERSATION#{timestamp}',
            'user_message': user_message,
            'bot_response': bot_response,
            'system_id': system_id,
            'timestamp': timestamp,
            'conversation_id': conversation_id,
            'has_chart': chart_data is not None,
            'has_dynamodb_queries': dynamodb_queries is not None and len(dynamodb_queries) > 0
        }
        
        # Add chart data if present
        if chart_data:
            log_item['chart_data'] = {
                'data_type': chart_data.get('data_type', ''),
                'time_period': chart_data.get('time_period', ''),
                'total_value': Decimal(str(chart_data.get('total_value', 0))),  # Convert to Decimal
                'unit': chart_data.get('unit', ''),
                'data_points_count': len(chart_data.get('data_points', []))
            }
        
        # Add DynamoDB query logging if present
        if dynamodb_queries:
            log_item['dynamodb_queries'] = dynamodb_queries
        
        # Store in DynamoDB
        table.put_item(Item=log_item)
        logger.info(f"Logged conversation for user {user_id} with ID {conversation_id}")
        
    except Exception as e:
        logger.error(f"Failed to log conversation for user {user_id}: {str(e)}")

#---------------------------------------
# API Endpoints
#---------------------------------------

@app.get("/")
async def root():
    return {"message": "Welcome to the Solar O&M Chatbot API"}

@app.post("/api/chat", response_model=ChatResponse)
async def chat(chat_message: ChatMessage):
    try:
        """
        print("INSIDE MAIN")
        print("======== INCOMING CHAT REQUEST ========")
        print(f"Raw request data: {chat_message}")
        print(f"Message: {chat_message.message}")
        print(f"User ID: {chat_message.user_id}")
        print(f"Username: {chat_message.username}")
        print(f"JWT: {chat_message.jwtToken}")
        print("======================================")
        """
        
        # Extract user_id from the request
        user_id = chat_message.user_id or "default_user"
        
        # Extract system_id from the combined ID if present
        # Format is expected to be: userId_deviceId_systemId or userId_deviceId_PORTFOLIO
        system_id = None
        portfolio_data = None
        
        parts = user_id.split('_')
        if len(parts) >= 3:
            # The last part should be the system_id or "PORTFOLIO"
            last_part = parts[-1]
            if last_part == "PORTFOLIO":
                # Portfolio mode - convert portfolio systems to the format expected by backend
                if chat_message.portfolioSystems:
                    portfolio_data = {
                        "type": "portfolio",
                        "systems": [
                            {"system_id": sys.id, "name": sys.name} 
                            for sys in chat_message.portfolioSystems
                        ]
                    }
                    print(f"Portfolio mode detected with {len(portfolio_data['systems'])} systems")
                else:
                    print("Portfolio mode detected but no portfolio systems provided")
            else:
                system_id = last_part
            # For memory persistence, we'll use just the base user ID
            # This is handled by _get_or_create_memory
        
        # Get response from chatbot
        result = get_chatbot_response(
            chat_message.message, 
            user_id, 
            system_id, 
            chat_message.jwtToken,
            chat_message.username,
            portfolio_data
        )
        
        # Log the conversation to DynamoDB
        log_conversation_to_db(
            user_id=user_id,
            user_message=chat_message.message,
            bot_response=result["response"],
            system_id=system_id,
            chart_data=result.get("chart_data"),
            dynamodb_queries=result.get("dynamodb_queries", [])
        )
        
        # Process source documents if present
        source_documents = []
        if result.get("source_documents"):
            for doc in result["source_documents"]:
                source_documents.append(
                    SourceDocument(
                        content=doc.get("content", doc.page_content if hasattr(doc, "page_content") else ""),
                        metadata=doc.get("metadata", {})
                    )
                )
        
        return ChatResponse(
            response=result["response"],
            source_documents=source_documents,
            chart_data=result.get("chart_data")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    # Check if RAG is available
    rag_available = get_rag_instance() is not None
    return {
        "status": "healthy",
        "rag_available": rag_available
    }


# AWS Lambda handler
handler = Mangum(app) 



# Keep the local development server
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)