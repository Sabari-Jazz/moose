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

### 1. Mobile App Connection
- Open the mobile app
- Try logging in with test credentials
- Verify dashboard loads with solar system data

