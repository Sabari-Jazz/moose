# Developer Onboarding Guide

Welcome to the Solar Operations & Maintenance (O&M) Platform! This guide will help you get up and running with the development environment.

## 🏗️ System Overview

The Solar O&M Platform is a comprehensive solution for monitoring and managing solar installations:

- **Mobile App**: React Native (Expo) for iOS/Android
- **Backend**: Python FastAPI services deployed as AWS Lambda functions
- **Database**: AWS DynamoDB (primary) + Supabase (legacy feedback)
- **External APIs**: SolarWeb API for solar data, OpenAI for chat functionality
- **Infrastructure**: AWS (Lambda, DynamoDB, SNS, SES, CloudWatch)

## 📋 Prerequisites

### Required Software
- **Node.js** (LTS version recommended)
- **Python 3.11+**
- **Git**
- **AWS CLI** (configured with your credentials)
- **Expo CLI**: `npm install -g @expo/cli`

### Development Tools (Recommended)
- **VS Code** with extensions:
  - Python
  - React Native Tools
  - AWS Toolkit
- **Postman** or similar API testing tool
- **iOS Simulator** (Mac) or **Android Studio** (for mobile testing)

## 🚀 Quick Start

### 1. Repository Setup
```bash
git clone <repository-url>
cd Jazz
```

### 2. Backend Setup
```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp ../.env.example .env
# Edit .env with your actual values (see Environment Variables section)
```

### 3. Mobile App Setup
```bash
cd expo-moose

# Install dependencies
npm install

# Set up environment variables
cp ../.env.example .env
# Edit .env with your mobile-specific variables
```

### 4. AWS Configuration
```bash
# Configure AWS CLI
aws configure
# Enter your AWS Access Key ID, Secret Access Key, and region (us-east-1)

# Verify DynamoDB access
aws dynamodb describe-table --table-name Moose-DDB --region us-east-1
```

## 🔧 Environment Variables

Copy `.env.example` to `.env` and configure the following critical variables:

### Required for Backend
- `AWS_REGION=us-east-1`
- `DYNAMODB_TABLE_NAME=Moose-DDB`
- `OPENAI_API_KEY=<your-openai-api-key>`
- `PINECONE_API_KEY=<your-pinecone-api-key>`
- `PINECONE_INDEX_NAME=moose-om`

### Required for SolarWeb Integration
- `SOLAR_WEB_ACCESS_KEY_ID=<your-key>`
- `SOLAR_WEB_ACCESS_KEY_VALUE=<your-value>`
- `SOLAR_WEB_USERID=<your-userid>`
- `SOLAR_WEB_PASSWORD=<your-password>`

### Required for Mobile App
- `SUPABASE_URL=<your-supabase-url>`
- `SUPABASE_ANON_KEY=<your-supabase-key>`
- `EXPO_PROJECT_ID=f8b79784-8f4b-42a9-aa3c-e8a901abba87`

## 🏃‍♂️ Running the Application

### Backend Development Server
```bash
cd backend/local_development
python app.py
# Server runs on http://localhost:8000
```

### Mobile App
```bash
cd expo-moose

# Start development server
npm start

# Run on specific platforms
npm run ios     # iOS simulator
npm run android # Android emulator
npm run web     # Web browser
```

## 🧪 Testing Your Setup

### 1. Backend Health Check
```bash
curl http://localhost:8000/health
# Should return: {"status": "healthy"}
```

### 2. DynamoDB Connection
```bash
# From backend directory
python -c "import boto3; print(boto3.resource('dynamodb', region_name='us-east-1').Table('Moose-DDB').table_status)"
```

### 3. Mobile App Connection
- Open the mobile app
- Try logging in with test credentials
- Verify dashboard loads with solar system data

## 📁 Project Structure

```
Jazz/
├── backend/                    # Python backend services
│   ├── lambda/                # AWS Lambda functions
│   ├── helper/                # Utility scripts
│   ├── local_development/     # Local dev server
│   └── requirements.txt       # Python dependencies
├── expo-moose/                # React Native mobile app
│   ├── app/                   # App screens and navigation
│   ├── components/            # Reusable UI components
│   ├── api/                   # API integration
│   ├── utils/                 # Utility functions
│   └── package.json           # Node.js dependencies
└── docs/                      # Documentation
```

## 🔍 Key Components to Understand

### Backend Services
- **Chat Service**: AI-powered chatbot with RAG (Retrieval Augmented Generation)
- **Solar Data Service**: Fetches and processes solar system data
- **User Management**: Authentication and user profile management
- **Notification Service**: Handles alerts and technician communications

### Mobile App Features
- **Dashboard**: Real-time solar system monitoring
- **Chat**: AI assistant for operations queries
- **Map View**: Geographic visualization of solar installations
- **Incident Management**: Report and track system issues

## 🆘 Common Issues & Solutions

### Backend Issues
**DynamoDB Access Denied**
```bash
# Check AWS credentials
aws sts get-caller-identity
# Verify IAM permissions for DynamoDB
```

**OpenAI API Errors**
- Verify API key is valid and has sufficient credits
- Check model name: should be "gpt-4.1-mini"

### Mobile App Issues
**Expo Build Fails**
```bash
# Clear cache and reinstall
expo r -c
npm install
```

**Authentication Issues**
- Verify Supabase credentials in .env
- Check AWS Cognito configuration

## 📚 Next Steps

1. **Read the Architecture Documentation**: `docs/architecture.md`
2. **Review API Documentation**: `docs/api/openapi.yaml`
3. **Understand the Testing Strategy**: `docs/testing.md`
4. **Set up your development workflow**: `CONTRIBUTING.md`

## 🤝 Getting Help

- **Code Questions**: Check existing documentation or ask the team
- **AWS Issues**: Refer to CloudWatch logs
- **Mobile Issues**: Use Expo development tools and React Native debugger

Welcome to the team! 🎉
