from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime

def ingest_weather_data():
    # Placeholder function to simulate weather data ingestion
    print("Ingesting weather data...")

def process_weather_data():
    # Placeholder function to simulate weather data processing
    print("Processing weather data...")


with DAG(
    dag_id='weather_data_ingestion',
    start_date=datetime(2023, 1, 1),
    schedule_interval='@daily',
    catchup=False,
    tags=['weather', 'data_ingestion'],
) as dag:
    
    ingest_task = PythonOperator(
        task_id='ingest_weather_data',
        python_callable=ingest_weather_data,
    )
    
    process_task = PythonOperator(
        task_id='process_weather_data',
        python_callable=process_weather_data,
    )

    ingest_task >> process_task