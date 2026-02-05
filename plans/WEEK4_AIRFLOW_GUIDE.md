# Week 4: Airflow Orchestration Implementation Guide

This guide covers the setup, configuration, and operation of Apache Airflow on AWS EC2 for the Fitted Wardrobe project.

## 1. Infrastructure: IAM & Security Groups

### 1.1 IAM Policy for EC2
To allow the EC2 instance to interact with S3 and DynamoDB without hardcoded credentials, we use an IAM Role. Attach the following policy to a new IAM Role (e.g., `WeatherApp-Airflow-Role`).

**Policy JSON:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::fitted-weather-data-*",
                "arn:aws:s3:::fitted-weather-data-*/*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:Query",
                "dynamodb:Scan"
            ],
            "Resource": "arn:aws:dynamodb:*:*:table/user_preferences"
        },
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        }
    ]
}
```

### 1.2 Security Group Configuration
Create a Security Group (e.g., `airflow-sg`) with the following inbound rules:

| Protocol | Port | Source | Description |
| :--- | :--- | :--- | :--- |
| SSH | 22 | YOUR_IP/32 | Secure shell access |
| HTTP | 8080 | YOUR_IP/32 | Airflow Webserver UI |
| TCP | 5432 | SG_ID | PostgreSQL (internal access only if using RDS) |

---

## 2. Airflow Installation Tutorial

### 2.1 System Prerequisites
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv postgresql postgresql-contrib libpq-dev
```

### 2.2 PostgreSQL Setup
Airflow requires a robust metadata database.
```bash
sudo -u postgres psql -c "CREATE DATABASE airflow_db;"
sudo -u postgres psql -c "CREATE USER airflow_user WITH PASSWORD 'your_strong_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE airflow_db TO airflow_user;"
```

### 2.3 Virtual Environment & Installation
```bash
mkdir ~/airflow
cd ~/airflow
python3 -m venv airflow_venv
source airflow_venv/bin/activate

# Use constraints file for reproducible installation
AIRFLOW_VERSION=2.10.2
PYTHON_VERSION="$(python3 --version | cut -d ' ' -f 2 | cut -d '.' -f 1-2)"
CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

pip install "apache-airflow[postgres,amazon]==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}"
```

### 2.4 Initialize Airflow
Update `airflow.cfg` or set environment variables before initializing:
```bash
export AIRFLOW_HOME=~/airflow
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow_user:your_strong_password@localhost/airflow_db
export AIRFLOW__CORE__EXECUTOR=LocalExecutor

airflow db init
airflow users create \
    --username admin \
    --firstname Jane \
    --lastname Doe \
    --role Admin \
    --email admin@example.com
```

---

## 3. Persistent Services with systemd

To ensure Airflow starts on boot and restarts on failure, create systemd unit files.

### 3.1 Webserver Service (`/etc/systemd/system/airflow-webserver.service`)
```ini
[Unit]
Description=Airflow webserver daemon
After=network.target postgresql.service

[Service]
User=ubuntu
Group=ubuntu
Type=simple
Environment="AIRFLOW_HOME=/home/ubuntu/airflow"
ExecStart=/home/ubuntu/airflow/airflow_venv/bin/airflow webserver
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

### 3.2 Scheduler Service (`/etc/systemd/system/airflow-scheduler.service`)
```ini
[Unit]
Description=Airflow scheduler daemon
After=network.target postgresql.service

[Service]
User=ubuntu
Group=ubuntu
Type=simple
Environment="AIRFLOW_HOME=/home/ubuntu/airflow"
ExecStart=/home/ubuntu/airflow/airflow_venv/bin/airflow scheduler
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

---

## 4. Day 2 Operations: Monitoring & Logs

### 4.1 CLI Cheat Sheet
- `airflow dags list`: List all DAGs.
- `airflow dags trigger weather_data_ingestion`: Manually start a DAG run.
- `airflow tasks test weather_data_ingestion ingest_task 2024-01-01`: Test a single task without side effects.
- `airflow db clean`: Purge old metadata to save space.

### 4.2 Logging
Airflow stores logs locally in `$AIRFLOW_HOME/logs`. For production, consider:
1. **CloudWatch Logs:** Use the CloudWatch agent to ship logs to AWS.
2. **Log Rotation:** Configure `logrotate` to prevent the disk from filling up.

---

## 5. Troubleshooting Guide

### 5.1 Webserver UI Unreachable (Port 8080)
- **Check Security Group:** Ensure port 8080 is open for your IP.
- **Check Service Status:** Run `sudo systemctl status airflow-webserver`.
- **Listen Address:** By default, Airflow might bind to `127.0.0.1`. In `airflow.cfg`, ensure `web_server_host = 0.0.0.0` to allow external access.

### 5.2 Tasks Stuck in "Scheduled" or "Queued"
- **Scheduler Down:** Ensure the scheduler is running: `sudo systemctl status airflow-scheduler`.
- **DAG Paused:** New DAGs are paused by default. Toggle the switch in the UI.
- **Executor Issue:** If using `LocalExecutor`, ensure PostgreSQL is reachable and the connection string is correct.

### 5.3 IAM Permissions Denied
- **Check Role Attachment:** Verify the EC2 instance has the `WeatherApp-Airflow-Role` attached.
- **Boto3/AWS CLI:** Test access manually from the EC2 terminal: `aws s3 ls s3://your-bucket-name`. If this fails, the role or policy is incorrect.

### 5.4 Database Connection Errors
- **Postgres Service:** Ensure PostgreSQL is running: `sudo systemctl status postgresql`.
- **HBA Config:** If PostgreSQL is on a different machine, check `/etc/postgresql/14/main/pg_hba.conf` to allow connections from the Airflow IP.
