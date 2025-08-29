# Backend Release Checklist

This document provides a comprehensive checklist for releasing backend services to production. Follow this process to ensure safe, reliable deployments.

## 🎯 Pre-Release Preparation

### Code Quality Checks

- [ ] **All tests passing**
  ```bash
  cd backend
  pytest --cov=. --cov-report=term --cov-fail-under=80
  ```

- [ ] **Code review completed**
  - [ ] At least 2 reviewers approved
  - [ ] Security review completed (if applicable)
  - [ ] Performance impact assessed

- [ ] **Linting and formatting**
  ```bash
  black backend/
  flake8 backend/
  mypy backend/
  ```

- [ ] **Dependencies updated and secure**
  ```bash
  pip-audit
  pip list --outdated
  ```

### Environment Preparation

- [ ] **Environment variables verified**
  - [ ] All required variables present in production
  - [ ] Secrets rotated if necessary
  - [ ] Configuration values validated

- [ ] **AWS Resources Ready**
  - [ ] DynamoDB table accessible
  - [ ] Lambda execution roles configured
  - [ ] SNS topics configured
  - [ ] CloudWatch log groups created

- [ ] **External Dependencies Verified**
  - [ ] SolarWeb API credentials valid
  - [ ] OpenAI API key has sufficient credits
  - [ ] Pinecone index accessible

## 🚀 Deployment Process

### 1. Pre-Deployment Validation

- [ ] **Backup Current State**
  ```bash
  # Document current Lambda versions
  aws lambda list-functions --region us-east-1 > pre-deployment-functions.json
  
  # Backup DynamoDB if making schema changes
  aws dynamodb create-backup --table-name Moose-DDB --backup-name pre-release-$(date +%Y%m%d)
  ```

- [ ] **Staging Deployment Test**
  - [ ] Deploy to staging environment first
  - [ ] Run integration tests against staging
  - [ ] Verify all endpoints respond correctly
  - [ ] Test critical user journeys

### 2. Production Deployment

#### Lambda Function Deployment

**For each Lambda function:**

- [ ] **Chat Service Lambda**
  ```bash
  cd backend
  
  # Create deployment package
  zip -r chat-service-deployment.zip lambda/chat_service_lambda.py requirements.txt
  
  # Update function code
  aws lambda update-function-code \
    --function-name chat-service \
    --zip-file fileb://chat-service-deployment.zip \
    --region us-east-1
  
  # Update environment variables if needed
  aws lambda update-function-configuration \
    --function-name chat-service \
    --environment Variables='{
      "OPENAI_API_KEY": "'$OPENAI_API_KEY'",
      "DYNAMODB_TABLE_NAME": "Moose-DDB",
      "PINECONE_API_KEY": "'$PINECONE_API_KEY'",
      "PINECONE_INDEX_NAME": "moose-om"
    }' \
    --region us-east-1
  
  # Wait for deployment to complete
  aws lambda wait function-updated --function-name chat-service --region us-east-1
  ```

- [ ] **Solar Data Lambda**
  ```bash
  # Create deployment package
  zip -r solar-data-deployment.zip lambda/solar_data_lambda.py requirements.txt
  
  # Update function code
  aws lambda update-function-code \
    --function-name solar-data-service \
    --zip-file fileb://solar-data-deployment.zip \
    --region us-east-1
  
  # Update configuration
  aws lambda update-function-configuration \
    --function-name solar-data-service \
    --environment Variables='{
      "DYNAMODB_TABLE_NAME": "Moose-DDB",
      "SOLAR_WEB_ACCESS_KEY_ID": "'$SOLAR_WEB_ACCESS_KEY_ID'",
      "SOLAR_WEB_ACCESS_KEY_VALUE": "'$SOLAR_WEB_ACCESS_KEY_VALUE'",
      "SOLAR_WEB_USERID": "'$SOLAR_WEB_USERID'",
      "SOLAR_WEB_PASSWORD": "'$SOLAR_WEB_PASSWORD'"
    }' \
    --region us-east-1
  
  aws lambda wait function-updated --function-name solar-data-service --region us-east-1
  ```

- [ ] **User Management Lambda**
  ```bash
  # Create deployment package
  zip -r user-mgmt-deployment.zip lambda/user_management_lambda.py requirements.txt
  
  # Update function code
  aws lambda update-function-code \
    --function-name user-management \
    --zip-file fileb://user-mgmt-deployment.zip \
    --region us-east-1
  
  # Update configuration
  aws lambda update-function-configuration \
    --function-name user-management \
    --environment Variables='{
      "DYNAMODB_TABLE_NAME": "Moose-DDB",
      "SUPABASE_URL": "'$SUPABASE_URL'",
      "SUPABASE_ANON_KEY": "'$SUPABASE_ANON_KEY'"
    }' \
    --region us-east-1
  
  aws lambda wait function-updated --function-name user-management --region us-east-1
  ```

- [ ] **Notification Services**
  ```bash
  # Update polling functions
  for function in device-status-polling solar-data-polling notify-user; do
    zip -r ${function}-deployment.zip lambda/${function}.py requirements.txt
    
    aws lambda update-function-code \
      --function-name $function \
      --zip-file fileb://${function}-deployment.zip \
      --region us-east-1
    
    aws lambda wait function-updated --function-name $function --region us-east-1
  done
  ```

### 3. Post-Deployment Verification

- [ ] **Health Checks**
  ```bash
  # Test each Lambda function
  aws lambda invoke \
    --function-name chat-service \
    --payload '{"httpMethod": "GET", "path": "/health"}' \
    --region us-east-1 \
    response.json
  
  cat response.json  # Should show {"status": "healthy"}
  
  # Test solar data service
  aws lambda invoke \
    --function-name solar-data-service \
    --payload '{"httpMethod": "GET", "path": "/health"}' \
    --region us-east-1 \
    response.json
  
  # Test user management
  aws lambda invoke \
    --function-name user-management \
    --payload '{"httpMethod": "GET", "path": "/health"}' \
    --region us-east-1 \
    response.json
  ```

- [ ] **Integration Tests**
  ```bash
  # Run integration tests against production
  pytest tests/integration/ --env=production
  ```

- [ ] **API Endpoint Verification**
  ```bash
  # Test critical endpoints
  curl -X GET "https://your-api-gateway-url/health" \
    -H "Authorization: Bearer $TEST_JWT_TOKEN"
  
  curl -X POST "https://your-api-gateway-url/api/chat" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TEST_JWT_TOKEN" \
    -d '{"username": "test", "message": "health check", "user_id": "test"}'
  ```

- [ ] **Database Connectivity**
  ```bash
  # Verify DynamoDB access
  aws dynamodb describe-table --table-name Moose-DDB --region us-east-1
  
  # Test basic operations
  aws dynamodb get-item \
    --table-name Moose-DDB \
    --key '{"PK": {"S": "System#test"}, "SK": {"S": "STATUS"}}' \
    --region us-east-1
  ```

- [ ] **External API Connectivity**
  ```bash
  # Test SolarWeb API (using production credentials)
  # This should be done carefully to avoid rate limits
  
  # Test OpenAI API
  curl -X POST "https://api.openai.com/v1/chat/completions" \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "test"}], "max_tokens": 5}'
  ```

## 📊 Monitoring and Validation

### CloudWatch Monitoring

- [ ] **Set up alerts for new deployment**
  ```bash
  # Create error rate alarm
  aws cloudwatch put-metric-alarm \
    --alarm-name "Lambda-High-Error-Rate-Post-Deployment" \
    --alarm-description "High error rate after deployment" \
    --metric-name Errors \
    --namespace AWS/Lambda \
    --statistic Sum \
    --period 300 \
    --threshold 5 \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 2 \
    --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:deployment-alerts
  ```

- [ ] **Monitor key metrics for 30 minutes**
  - [ ] Error rates < 1%
  - [ ] Average duration within normal range
  - [ ] No throttling events
  - [ ] Memory usage stable

### Performance Validation

- [ ] **Response Time Testing**
  ```bash
  # Test API response times
  for i in {1..10}; do
    time curl -X POST "https://your-api-gateway-url/api/chat" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $TEST_JWT_TOKEN" \
      -d '{"username": "test", "message": "performance test", "user_id": "test"}'
    sleep 1
  done
  ```

- [ ] **Load Testing (if significant changes)**
  ```bash
  # Use artillery or similar tool
  artillery quick --count 50 --num 10 https://your-api-gateway-url/health
  ```

### Data Integrity Checks

- [ ] **Verify data flow**
  - [ ] Solar data polling working
  - [ ] Chat conversations being logged
  - [ ] User activities recorded
  - [ ] Notifications sending properly

- [ ] **Database consistency**
  ```bash
  # Check recent data entries
  aws dynamodb scan \
    --table-name Moose-DDB \
    --filter-expression "attribute_exists(#ts) AND #ts > :recent" \
    --expression-attribute-names '{"#ts": "timestamp"}' \
    --expression-attribute-values '{":recent": {"S": "'$(date -d '1 hour ago' --iso-8601)'"}}' \
    --region us-east-1 \
    --max-items 10
  ```

## 🔄 Rollback Procedures

### Immediate Rollback (if issues detected)

- [ ] **Identify last known good version**
  ```bash
  # List function versions
  aws lambda list-versions-by-function --function-name chat-service --region us-east-1
  ```

- [ ] **Rollback Lambda functions**
  ```bash
  # Rollback to previous version
  aws lambda update-function-code \
    --function-name chat-service \
    --s3-bucket your-deployment-bucket \
    --s3-key previous-versions/chat-service-v1.2.3.zip \
    --region us-east-1
  
  # Or rollback environment variables
  aws lambda update-function-configuration \
    --function-name chat-service \
    --environment Variables='{"PREVIOUS_CONFIG": "values"}' \
    --region us-east-1
  ```

- [ ] **Verify rollback success**
  ```bash
  # Test rolled back version
  aws lambda invoke \
    --function-name chat-service \
    --payload '{"httpMethod": "GET", "path": "/health"}' \
    --region us-east-1 \
    response.json
  ```

### Database Rollback (if schema changes)

- [ ] **Restore from backup**
  ```bash
  # Only if necessary - this is a last resort
  aws dynamodb restore-table-from-backup \
    --target-table-name Moose-DDB-Restored \
    --backup-arn arn:aws:dynamodb:us-east-1:ACCOUNT:table/Moose-DDB/backup/BACKUP-ID \
    --region us-east-1
  ```

## 📝 Post-Release Tasks

### Documentation Updates

- [ ] **Update deployment logs**
  - [ ] Record deployment timestamp
  - [ ] Note any issues encountered
  - [ ] Document resolution steps

- [ ] **Update version tracking**
  ```bash
  # Tag the release
  git tag -a v1.2.3 -m "Backend release v1.2.3"
  git push origin v1.2.3
  
  # Update changelog
  echo "## v1.2.3 - $(date)" >> CHANGELOG.md
  echo "### Changes" >> CHANGELOG.md
  echo "- Feature: New chat functionality" >> CHANGELOG.md
  echo "- Fix: Resolved authentication issue" >> CHANGELOG.md
  ```

### Communication

- [ ] **Notify stakeholders**
  - [ ] Development team
  - [ ] QA team
  - [ ] Product management
  - [ ] Customer support (if user-facing changes)

- [ ] **Update status page** (if applicable)
  - [ ] Mark maintenance window as complete
  - [ ] Update system status

### Monitoring Setup

- [ ] **Extended monitoring period**
  - [ ] Monitor for 24 hours post-deployment
  - [ ] Check error rates and performance metrics
  - [ ] Review user feedback and support tickets

- [ ] **Schedule follow-up review**
  - [ ] 1 week post-deployment review
  - [ ] Document lessons learned
  - [ ] Update deployment procedures if needed

## 🚨 Emergency Procedures

### Critical Issues

**If critical issues are discovered:**

1. **Immediate Response**
   - [ ] Alert the team immediately
   - [ ] Stop the deployment if in progress
   - [ ] Begin rollback procedures

2. **Communication**
   - [ ] Notify stakeholders within 15 minutes
   - [ ] Update status page
   - [ ] Prepare incident report

3. **Resolution**
   - [ ] Execute rollback plan
   - [ ] Verify system stability
   - [ ] Conduct post-incident review

### Contact Information

**Emergency Contacts:**
- Development Lead: [Contact Info]
- DevOps Engineer: [Contact Info]
- AWS Support: AWS Support Center
- On-call Engineer: [Contact Info]

## 📊 Release Metrics

### Success Criteria

- [ ] **Zero critical errors** in first hour
- [ ] **< 1% error rate** in first 24 hours
- [ ] **Response times** within 10% of baseline
- [ ] **All health checks** passing
- [ ] **No user-reported issues** in first hour

### Metrics to Track

- **Technical Metrics:**
  - Lambda function error rates
  - API response times
  - Database query performance
  - External API call success rates

- **Business Metrics:**
  - User session success rates
  - Chat service usage
  - Solar data update frequency
  - Notification delivery rates

### Post-Release Report Template

```markdown
# Backend Release Report - v1.2.3

## Release Summary
- **Release Date**: YYYY-MM-DD HH:MM UTC
- **Duration**: X minutes
- **Components Updated**: List of services
- **Issues Encountered**: None/List issues

## Metrics
- **Error Rate**: X%
- **Average Response Time**: Xms
- **Deployment Duration**: X minutes
- **Rollback Required**: Yes/No

## Lessons Learned
- What went well
- What could be improved
- Action items for next release

## Next Steps
- Scheduled follow-up tasks
- Monitoring plan
- Future improvements
```

---

**Remember**: Always have a rollback plan ready and don't hesitate to use it if issues arise. It's better to rollback and investigate than to leave users with a broken experience.

This checklist should be customized based on your specific deployment environment and requirements. Update it regularly based on lessons learned from each deployment.
