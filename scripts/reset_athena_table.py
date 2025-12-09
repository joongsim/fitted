#!/usr/bin/env python3
"""
Script to drop and recreate the Athena weather_data table.
Run this when you need to update the table schema.
"""

import boto3
import os
import time
import sys

def wait_for_query(athena_client, query_id, description):
    """Wait for an Athena query to complete."""
    print(f"Waiting for {description}...", end='', flush=True)
    
    while True:
        response = athena_client.get_query_execution(QueryExecutionId=query_id)
        state = response['QueryExecution']['Status']['State']
        
        if state == 'SUCCEEDED':
            print(" ✅ Success")
            return True
        elif state in ['FAILED', 'CANCELLED']:
            reason = response['QueryExecution']['Status'].get('StateChangeReason', 'Unknown')
            print(f" ❌ {state}: {reason}")
            return False
        
        print(".", end='', flush=True)
        time.sleep(1)

def main():
    bucket_name = os.environ.get('WEATHER_BUCKET_NAME')
    
    if not bucket_name:
        print("❌ WEATHER_BUCKET_NAME environment variable not set")
        print("   export WEATHER_BUCKET_NAME=fitted-weather-data-fitted-wardrobe-dev-903558039846")
        sys.exit(1)
    
    athena = boto3.client('athena')
    database_name = 'fitted_weather_db'
    output_location = f"s3://{bucket_name}/athena-results/"
    
    print("=" * 60)
    print("Athena Table Reset")
    print("=" * 60)
    print(f"Database: {database_name}")
    print(f"Bucket: {bucket_name}")
    print()
    
    # Step 1: Drop existing table
    print("[1/2] Dropping existing table...")
    drop_query = "DROP TABLE IF EXISTS fitted_weather_db.weather_data"
    
    try:
        response = athena.start_query_execution(
            QueryString=drop_query,
            QueryExecutionContext={'Database': database_name},
            ResultConfiguration={'OutputLocation': output_location}
        )
        
        query_id = response['QueryExecutionId']
        if not wait_for_query(athena, query_id, "DROP TABLE"):
            print("Failed to drop table, but continuing anyway...")
        
    except Exception as e:
        print(f"⚠️  Error dropping table (may not exist): {e}")
    
    # Step 2: Create new table
    print("\n[2/2] Creating new table with 'curr' schema...")
    
    create_query = f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {database_name}.weather_data (
        location STRUCT<
            name: STRING,
            region: STRING,
            country: STRING,
            lat: DOUBLE,
            lon: DOUBLE,
            tz_id: STRING,
            localtime_epoch: BIGINT,
            localtime: STRING
        >,
        curr STRUCT<
            last_updated_epoch: BIGINT,
            last_updated: STRING,
            temp_c: DOUBLE,
            temp_f: DOUBLE,
            is_day: INT,
            condition: STRUCT<
                text: STRING,
                icon: STRING,
                code: INT
            >,
            wind_mph: DOUBLE,
            wind_kph: DOUBLE,
            humidity: INT,
            cloud: INT,
            feelslike_c: DOUBLE,
            feelslike_f: DOUBLE,
            uv: DOUBLE
        >
    )
    PARTITIONED BY (
        dt STRING
    )
    ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
    WITH SERDEPROPERTIES (
        'mapping.curr' = 'current'
    )
    LOCATION 's3://{bucket_name}/raw/weather/'
    TBLPROPERTIES (
        'projection.enabled' = 'true',
        'projection.dt.type' = 'date',
        'projection.dt.format' = 'yyyy-MM-dd',
        'projection.dt.range' = '2024-01-01,NOW',
        'projection.dt.interval' = '1',
        'projection.dt.interval.unit' = 'DAYS',
        'storage.location.template' = 's3://{bucket_name}/raw/weather/dt=${{dt}}/'
    )
    """
    
    try:
        response = athena.start_query_execution(
            QueryString=create_query,
            QueryExecutionContext={'Database': database_name},
            ResultConfiguration={'OutputLocation': output_location}
        )
        
        query_id = response['QueryExecutionId']
        if not wait_for_query(athena, query_id, "CREATE TABLE"):
            print("❌ Failed to create table")
            sys.exit(1)
        
    except Exception as e:
        print(f"❌ Error creating table: {e}")
        sys.exit(1)
    
    # Step 3: Test query
    print("\n[3/3] Testing table with a simple query...")
    test_query = f"""
    SELECT COUNT(*) as record_count
    FROM {database_name}.weather_data
    LIMIT 1
    """
    
    try:
        response = athena.start_query_execution(
            QueryString=test_query,
            QueryExecutionContext={'Database': database_name},
            ResultConfiguration={'OutputLocation': output_location}
        )
        
        query_id = response['QueryExecutionId']
        if wait_for_query(athena, query_id, "Test query"):
            # Get results
            results = athena.get_query_results(QueryExecutionId=query_id)
            if len(results['ResultSet']['Rows']) > 1:
                count = results['ResultSet']['Rows'][1]['Data'][0].get('VarCharValue', '0')
                print(f"   Found {count} weather records")
        
    except Exception as e:
        print(f"⚠️  Test query error: {e}")
        print("   This is normal if you don't have data yet.")
    
    print("\n" + "=" * 60)
    print("✅ Table Reset Complete!")
    print("=" * 60)
    print("\nKey points:")
    print("  • Column 'curr' in Athena maps to 'current' in JSON files")
    print("  • SerDe mapping handles the translation automatically")
    print("  • All queries should use 'curr' (e.g., curr.temp_c)")
    print("\nYour API endpoints should now work correctly!")

if __name__ == '__main__':
    main()