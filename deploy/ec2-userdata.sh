#!/bin/bash
# EC2 user-data script — runs once on first boot as root.
# Installs Docker, authenticates to ECR, pulls images, starts the app.

set -e

# Install Docker
dnf update -y
dnf install -y docker
systemctl enable docker
systemctl start docker

# Install Docker Compose plugin
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Allow ec2-user to run docker without sudo
usermod -aG docker ec2-user

# Authenticate to ECR
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin \
  024757002684.dkr.ecr.us-west-2.amazonaws.com

# Create app directory
mkdir -p /app
cd /app

# Download docker-compose.ec2.yml from S3 (uploaded in deploy step)
# .env is copied via scp before this script runs — see deploy/README below.
