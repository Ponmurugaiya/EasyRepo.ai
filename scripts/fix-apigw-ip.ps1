# fix-apigw-ip.ps1 — Update API Gateway integrations to the current ECS task IP.
# Run this any time after a backend deploy if the API starts returning 503.
# Usage: .\scripts\fix-apigw-ip.ps1

$API_ID      = "uq5kpo8lz1"
$INT_PROXY   = "bkybhnm"
$INT_DEFAULT = "78kmw1e"
$CLUSTER     = "easyrepo"
$SERVICE     = "easyrepo-api"
$REGION      = "us-east-1"

Write-Host "Fetching current ECS task IP..." -ForegroundColor Cyan

$taskArn = aws ecs list-tasks --cluster $CLUSTER --service-name $SERVICE --region $REGION --query "taskArns[0]" --output text
$eniId   = aws ecs describe-tasks --cluster $CLUSTER --tasks $taskArn --region $REGION --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value|[0]" --output text
$ip      = aws ec2 describe-network-interfaces --network-interface-ids $eniId --region $REGION --query "NetworkInterfaces[0].Association.PublicIp" --output text

Write-Host "Task IP: $ip" -ForegroundColor Yellow

aws apigatewayv2 update-integration --api-id $API_ID --integration-id $INT_PROXY   --integration-uri "http://${ip}:8000/{proxy}" --region $REGION --output none
aws apigatewayv2 update-integration --api-id $API_ID --integration-id $INT_DEFAULT --integration-uri "http://${ip}:8000/"        --region $REGION --output none

Write-Host "Done — both integrations updated to http://${ip}:8000" -ForegroundColor Green
