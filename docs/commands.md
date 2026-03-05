# Fitted — Command Reference

## Local Development

```bash
# Set up / activate virtualenv
uv venv && source .venv/bin/activate
uv sync

# Run backend (port 8000)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run frontend (port 5001)
uvicorn frontend.app:app --reload --host 0.0.0.0 --port 5001

# Run tests
PYTHONPATH=. pytest tests/ -v

# Single test file
PYTHONPATH=. pytest tests/test_weather_service.py -v

# Format code
black .
```

## SSH into EC2

```bash
ssh -i ~/.ssh/<key>.pem ec2-user@<EC2_PUBLIC_IP>
```

> Find the EC2 public IP in the AWS console or:
> ```bash
> aws ec2 describe-instances --region us-west-1 \
>   --filters "Name=tag:aws:cloudformation:stack-name,Values=fitted-infra" \
>   --query "Reservations[].Instances[].PublicIpAddress" --output text
> ```

## EC2 — Service Management

```bash
# View service status
sudo systemctl status fitted-backend
sudo systemctl status fitted-frontend
sudo systemctl status caddy

# Restart services
sudo systemctl restart fitted-backend
sudo systemctl restart fitted-frontend
sudo systemctl restart caddy

# View logs (live tail)
sudo journalctl -u fitted-backend -f
sudo journalctl -u fitted-frontend -f
sudo journalctl -u caddy -f

# View last N lines
sudo journalctl -u fitted-backend -n 100 --no-pager
```

## EC2 — Manual Deploy

```bash
# SSH in, then run (deploys current branch by default):
cd /home/ec2-user/fitted
./scripts/deploy.sh

# Deploy a specific branch:
./scripts/deploy.sh feat/my-branch
```

## Database

```bash
# Run migrations (IRREVERSIBLE — confirm before running)
PYTHONPATH=. python scripts/db_migrate.py

# Connect to RDS via psql (from EC2 or with tunnel)
psql "$DATABASE_URL"

# Or explicitly:
psql "postgresql://fitted:<password>@fitted-infra-fitteddb-iqqx1aiplhxv.cle6qaquk5at.us-west-1.rds.amazonaws.com:5432/fitted"
```

## Data Scripts

```bash
# Ingest Poshmark items into dev catalog
PYTHONPATH=. python scripts/ingest_poshmark_dev_catalog.py

# Backfill CLIP embeddings for catalog items
PYTHONPATH=. python scripts/backfill_catalog_embeddings.py

# Backfill CLIP embeddings for wardrobe items
PYTHONPATH=. python scripts/backfill_wardrobe_embeddings.py
```

## ML Training

```bash
# Pre-train ItemTower on catalog CLIP embeddings
PYTHONPATH=. python scripts/pretrain_item_tower.py

# Train full two-tower model
PYTHONPATH=. python scripts/train_two_towers.py
```

## AWS CLI

```bash
# --- SSM ---
# Run a command on the EC2 instance
aws ssm send-command \
  --region us-west-1 \
  --instance-ids <INSTANCE_ID> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["sudo systemctl restart fitted-backend"]'

# Check SSM command result
aws ssm get-command-invocation \
  --region us-west-1 \
  --command-id <COMMAND_ID> \
  --instance-id <INSTANCE_ID>

# --- SSM Parameter Store ---
# List fitted parameters
aws ssm get-parameters-by-path --path /fitted/ --region us-west-1 --with-decryption

# Get a single parameter
aws ssm get-parameter --name /fitted/database-url --with-decryption --region us-west-1

# Put / update a parameter
aws ssm put-parameter --name /fitted/my-key --value "my-value" \
  --type SecureString --overwrite --region us-west-1

# --- CloudFormation ---
# List stacks
aws cloudformation list-stacks --region us-west-1 --query "StackSummaries[?StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" --output table

# Describe SAM stack outputs
aws cloudformation describe-stacks --stack-name fitted-wardrobe-dev --region us-west-1 \
  --query "Stacks[0].Outputs" --output table

# --- S3 ---
# List weather data bucket
aws s3 ls s3://fitted-weather-data-fitted-wardrobe-dev-903558039846/ --region us-west-1

# Sync / copy files
aws s3 cp local-file.json s3://fitted-weather-data-fitted-wardrobe-dev-903558039846/path/

# --- EC2 ---
# Get public IP of EC2 instance
aws ec2 describe-instances --region us-west-1 \
  --filters "Name=tag:aws:cloudformation:stack-name,Values=fitted-infra" \
  --query "Reservations[].Instances[].PublicIpAddress" --output text

# Get instance ID
aws ec2 describe-instances --region us-west-1 \
  --filters "Name=tag:aws:cloudformation:stack-name,Values=fitted-infra" \
  --query "Reservations[].Instances[].InstanceId" --output text

# Get your current public IP (for SG rules)
curl -4 -s ifconfig.me
```

## AWS SAM (Lambda Deploy)

```bash
# Build Lambda packages
sam build

# Deploy to staging (dev branch)
sam deploy --stack-name fitted-wardrobe-dev --region us-west-1

# Deploy with guided prompts (first time)
sam deploy --guided
```

## CloudFormation (Infra Stack)

```bash
# Deploy EC2 + RDS infra stack
aws cloudformation deploy \
  --stack-name fitted-infra \
  --template-file infra/cloudformation.yaml \
  --capabilities CAPABILITY_IAM \
  --region us-west-1 \
  --parameter-overrides \
    SSHKeyName=<key-name> \
    SSHAllowedIPv4=$(curl -4 -s ifconfig.me)/32 \
    WeatherBucketName=fitted-weather-data-fitted-wardrobe-dev-903558039846

# Delete infra stack (DESTRUCTIVE)
aws cloudformation delete-stack --stack-name fitted-infra --region us-west-1
```

## Athena

```bash
# List Athena databases
aws athena list-databases --catalog-name AwsDataCatalog --region us-west-1

# Run a query (results go to S3)
aws athena start-query-execution \
  --query-string "SELECT * FROM fitted_weather_db.weather_raw LIMIT 10" \
  --query-execution-context Database=fitted_weather_db \
  --result-configuration OutputLocation=s3://fitted-weather-data-fitted-wardrobe-dev-903558039846/athena-results/ \
  --region us-west-1
```

## Git Workflow

```bash
# Start new feature
git checkout dev && git pull origin dev
git checkout -b feat/<short-name>

# Push and open PR into dev
git push -u origin feat/<short-name>
gh pr create --base dev --title "feat: ..." --body "..."

# Check CI status
gh run list --limit 5
gh run view <run-id>
```
