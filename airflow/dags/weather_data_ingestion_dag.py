"""
Weather Data Ingestion DAG
--------------------------
This DAG is a production-ready template for orchestrating weather data ingestion.
It demonstrates key Airflow concepts:
- DAG Definition: The root object that organizes tasks.
- Operators: PythonOperator for custom logic, S3CreateObjectOperator (via hook) for storage.
- XComs: Cross-task communication to pass the list of locations.
- Idempotency: Using execution_date to ensure the same run produces the same result.
- Task Dependencies: Using the bitshift operator (>>) to define the workflow.
"""

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from datetime import datetime, timedelta
import json
import os

# 1. DAG Configuration (Default Args)
# These arguments are applied to all tasks in the DAG.
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

# 2. Python Callables (Task Logic)

def get_locations_to_fetch(**context):
    """
    CONCEPT: XCom (Cross-Communication)
    This task 'returns' a list of locations. Airflow automatically stores this return 
    value in XCom, which downstream tasks can pull.
    In a real system, this might query a DynamoDB table or a config file.
    """
    locations = ["tokyo", "london", "new-york", "san-francisco", "paris"]
    print(f"Retrieved {len(locations)} locations: {locations}")
    return locations

def fetch_and_store_weather(location, **context):
    """
    CONCEPT: Airflow Hooks & Connections
    We use S3Hook to interact with AWS. This hook uses the 'aws_default' connection 
    configured in the Airflow UI (which can use IAM roles).
    
    CONCEPT: Macros & Templates
    The 'execution_date' (or 'ds' for date string) is passed via context. 
    This allows us to partition data in S3 by date, ensuring idempotency.
    """
    # In a real implementation, you would import and call your app's service logic here.
    # For this guide, we simulate the fetch and store.
    
    ds = context['ds'] # execution date as YYYY-MM-DD
    bucket_name = os.environ.get("WEATHER_BUCKET_NAME", "fitted-weather-data-placeholder")
    s3_key = f"raw/weather/dt={ds}/location={location}/data.json"
    
    # Mock weather data
    weather_data = {
        "location": location,
        "date": ds,
        "temp_c": 20.5,
        "condition": "Clear"
    }
    
    # Use S3Hook for storage
    hook = S3Hook(aws_conn_id='aws_default')
    hook.load_string(
        string_data=json.dumps(weather_data),
        key=s3_key,
        bucket_name=bucket_name,
        replace=True
    )
    print(f"Stored weather data for {location} in s3://{bucket_name}/{s3_key}")

# 3. DAG Definition
with DAG(
    'weather_data_ingestion_v1',
    default_args=default_args,
    description='Automated weather data ingestion from API to S3',
    schedule_interval='@daily',  # Run once a day at midnight
    start_date=datetime(2024, 1, 1),
    catchup=False,               # If True, Airflow would run for all days since 2024-01-01
    tags=['weather', 'data_lake'],
) as dag:

    # Task 1: Get Locations
    # PythonOperator executes a Python function.
    get_locs = PythonOperator(
        task_id='get_locations',
        python_callable=get_locations_to_fetch,
    )

    # Task 2: Fetch and Store Weather (Dynamic Task Mapping simulation)
    # In Airflow 2.3+, we could use .expand(), but for this guide, we'll keep it simple.
    # Note: In a real DAG, you'd use a loop or Dynamic Task Mapping.
    
    def process_all_locations(**context):
        locations = context['ti'].xcom_pull(task_ids='get_locations')
        for loc in locations:
            fetch_and_store_weather(loc, **context)

    fetch_weather = PythonOperator(
        task_id='fetch_and_store_weather_all',
        python_callable=process_all_locations,
    )

    # 4. Task Dependencies
    # The '>>' operator means 'get_locs' must finish before 'fetch_weather' starts.
    get_locs >> fetch_weather
