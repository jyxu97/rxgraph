#!/bin/bash
# Run this from the project root once the EC2 quota is approved.
# It launches the instance, waits for it, copies files, and starts the app.
#
# Usage:
#   chmod +x deploy/launch.sh
#   ./deploy/launch.sh

set -e

REGION="us-west-2"
AMI=$(aws ssm get-parameter \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --region $REGION --query "Parameter.Value" --output text)

echo "Launching EC2 instance..."
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI" \
  --instance-type t3.micro \
  --key-name rxgraph-key \
  --security-group-ids sg-0b370656870f2d3cb \
  --region $REGION \
  --iam-instance-profile Name=rxgraph-ec2-profile \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=rxgraph}]' \
  --query "Instances[0].InstanceId" \
  --output text)

echo "Instance ID: $INSTANCE_ID"
echo "Waiting for instance to be running..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region $REGION

PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --region $REGION \
  --query "Reservations[0].Instances[0].PublicIpAddress" \
  --output text)

echo "Public IP: $PUBLIC_IP"
echo "Waiting for SSH to be ready..."
sleep 30

# Copy deployment files to EC2
scp -i ~/.ssh/rxgraph-key.pem -o StrictHostKeyChecking=no \
  .env docker-compose.ec2.yml \
  ec2-user@$PUBLIC_IP:/home/ec2-user/

# Install Docker and start the app
ssh -i ~/.ssh/rxgraph-key.pem -o StrictHostKeyChecking=no ec2-user@$PUBLIC_IP << 'ENDSSH'
  # Install Docker
  sudo dnf update -y -q
  sudo dnf install -y docker
  sudo systemctl enable docker
  sudo systemctl start docker
  sudo usermod -aG docker ec2-user
  sudo mkdir -p /usr/local/lib/docker/cli-plugins
  sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

  # Authenticate to ECR and start containers
  aws ecr get-login-password --region us-west-2 | \
    sudo docker login --username AWS --password-stdin \
    024757002684.dkr.ecr.us-west-2.amazonaws.com

  sudo docker compose -f docker-compose.ec2.yml up -d
ENDSSH

echo ""
echo "Deployment complete!"
echo "App: http://$PUBLIC_IP"
