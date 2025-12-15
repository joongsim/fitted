#!/usr/bin/env python3
"""
Setup script for AWS Athena and Glue Crawler.
This script creates the necessary Athena database and Glue Crawler
to enable SQL queries on S3 weather data.

Usage:
    python scripts/setup_athena.py
"""

import boto3
import os
import sys
import time

def setup_athena_database():
    """Create Athena database if it doesn't exist."""
    athena = boto3.client('athena')
    bucket_name = os.environ.get('WEATHER_BUCKET_NAME')
    
    if not bucket_name:
        print("❌ WEATHER_BUCKET_NAME environment variable not set")
        return False
    
    database_name = 'fitted_weather_db'
    output_location = f"s3://{bucket_name}/athena-results/"
    
    # Create database
    create_db_query = f"""
    CREATE DATABASE IF NOT EXISTS {database_name}
    COMMENT 'Fitted weather data analytics database'
    """
    
    try:
        print(f"Creating Athena database: {database_name}")
        response = athena.start_query_execution(
            QueryString=create_db_query,
            ResultConfiguration={'OutputLocation': output_location}
        )
        
        query_id = response['QueryExecutionId']
        
        # Wait for query to complete
        while True:
            status = athena.get_query_execution(QueryExecutionId=query_id)
            state = status['QueryExecution']['Status']['State']
            
            if state == 'SUCCEEDED':
                print(f"✅ Database {database_name} created successfully")
                break
            elif state in ['FAILED', 'CANCELLED']:
                reason = status['QueryExecution']['Status'].get('StateChangeReason', 'Unknown')
                print(f"❌ Database creation {state}: {reason}")
                return False
            
            time.sleep(1)
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating database: {e}")
        return False


def setup_athena_table():
    """Create Athena table for weather data."""
    athena = boto3.client('athena')
    bucket_name = os.environ.get('WEATHER_BUCKET_NAME')
    database_name = 'fitted_weather_db'
    table_name = 'weather_data'
    output_location = f"s3://{bucket_name}/athena-results/"
    
    # Create external table with partitions
    # Note: Field named 'curr' instead of 'current' to avoid reserved keyword
    create_table_query = f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {database_name}.{table_name} (
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
        print(f"Creating Athena table: {table_name}")
        response = athena.start_query_execution(
            QueryString=create_table_query,
            QueryExecutionContext={'Database': database_name},
            ResultConfiguration={'OutputLocation': output_location}
        )
        
        query_id = response['QueryExecutionId']
        
        # Wait for query to complete
        while True:
            status = athena.get_query_execution(QueryExecutionId=query_id)
            state = status['QueryExecution']['Status']['State']
            
            if state == 'SUCCEEDED':
                print(f"✅ Table {table_name} created successfully")
                break
            elif state in ['FAILED', 'CANCELLED']:
                reason = status['QueryExecution']['Status'].get('StateChangeReason', 'Unknown')
                print(f"❌ Table creation {state}: {reason}")
                return False
            
            time.sleep(1)
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating table: {e}")
        return False


def setup_glue_crawler():
    """Create Glue Crawler to discover weather data schema."""
    glue = boto3.client('glue')
    bucket_name = os.environ.get('WEATHER_BUCKET_NAME')
    
    if not bucket_name:
        print("❌ WEATHER_BUCKET_NAME environment variable not set")
        return False
    
    crawler_name = 'fitted-weather-crawler'
    database_name = 'fitted_weather_db'
    
    # Get AWS account ID and region for IAM role ARN
    sts = boto3.client('sts')
    account_id = sts.get_caller_identity()['Account']
    
    # Try to find the Glue Crawler role created by CloudFormation
    iam = boto3.client('iam')
    cloudformation = boto3.client('cloudformation')
    
    # Get stack name from environment or use default
    stack_name = os.environ.get('STACK_NAME', 'fitted-wardrobe-dev')
    
    try:
        # Get role ARN from CloudFormation stack outputs
        response = cloudformation.describe_stacks(StackName=stack_name)
        outputs = response['Stacks'][0]['Outputs']
        role_arn = None
        for output in outputs:
            if output['OutputKey'] == 'GlueCrawlerRoleArn':
                role_arn = output['OutputValue']
                break
        
        if not role_arn:
            # Fallback: try to find role by prefix
            roles = iam.list_roles()
            for role in roles['Roles']:
                if 'GlueCrawlerRole' in role['RoleName']:
                    role_arn = role['Arn']
                    break
        
        if not role_arn:
            print("⚠️  Could not find Glue Crawler role. Using default.")
            role_arn = f"arn:aws:iam::{account_id}:role/FittedGlueCrawlerRole-{stack_name}"
            
    except Exception as e:
        print(f"⚠️  Error finding role: {e}")
        role_arn = f"arn:aws:iam::{account_id}:role/FittedGlueCrawlerRole-{stack_name}"
    
    crawler_config = {
        'Name': crawler_name,
        'Role': role_arn,
        'DatabaseName': database_name,
        'Description': 'Crawler for Fitted weather data in S3',
        'Targets': {
            'S3Targets': [
                {
                    'Path': f"s3://{bucket_name}/raw/weather/",
                    'Exclusions': []
                }
            ]
        },
        'Schedule': 'cron(0 2 * * ? *)',  # Run daily at 2 AM UTC
        'SchemaChangePolicy': {
            'UpdateBehavior': 'UPDATE_IN_DATABASE',
            'DeleteBehavior': 'LOG'
        },
        'RecrawlPolicy': {
            'RecrawlBehavior': 'CRAWL_EVERYTHING'
        },
        'Configuration': '{"Version":1.0,"Grouping":{"TableGroupingPolicy":"CombineCompatibleSchemas"}}'
    }
    
    try:
        # Check if crawler exists
        try:
            glue.get_crawler(Name=crawler_name)
            print(f"Crawler {crawler_name} already exists. Updating...")
            glue.update_crawler(**crawler_config)
            print(f"✅ Crawler {crawler_name} updated successfully")
        except glue.exceptions.EntityNotFoundException:
            print(f"Creating Glue Crawler: {crawler_name}")
            glue.create_crawler(**crawler_config)
            print(f"✅ Crawler {crawler_name} created successfully")
        
        return True
        
    except Exception as e:
        print(f"❌ Error with Glue Crawler: {e}")
        print(f"Note: Make sure the IAM role exists: {role_arn}")
        return False


def test_athena_query():
    """Test Athena setup with a simple query."""
    athena = boto3.client('athena')
    bucket_name = os.environ.get('WEATHER_BUCKET_NAME')
    database_name = 'fitted_weather_db'
    table_name = 'weather_data'
    output_location = f"s3://{bucket_name}/athena-results/"
    
    test_query = f"""
    SELECT COUNT(*) as record_count
    FROM {database_name}.{table_name}
    LIMIT 10
    """
    
    try:
        print("\nTesting Athena query...")
        response = athena.start_query_execution(
            QueryString=test_query,
            QueryExecutionContext={'Database': database_name},
            ResultConfiguration={'OutputLocation': output_location}
        )
        
        query_id = response['QueryExecutionId']
        
        # Wait for query
        while True:
            status = athena.get_query_execution(QueryExecutionId=query_id)
            state = status['QueryExecution']['Status']['State']
            
            if state == 'SUCCEEDED':
                print("✅ Test query succeeded")
                
                # Get results
                results = athena.get_query_results(QueryExecutionId=query_id)
                if len(results['ResultSet']['Rows']) > 1:
                    count = results['ResultSet']['Rows'][1]['Data'][0]['VarCharValue']
                    print(f"   Found {count} weather records")
                break
            elif state in ['FAILED', 'CANCELLED']:
                reason = status['QueryExecution']['Status'].get('StateChangeReason', 'Unknown')
                print(f"⚠️  Test query {state}: {reason}")
                print("   This is expected if no data exists yet.")
                break
            
            time.sleep(1)
        
    except Exception as e:
        print(f"⚠️  Error testing query: {e}")


def main():
    """Run the setup process."""
    print("=" * 60)
    print("AWS Athena & Glue Setup for Fitted Weather Analytics")
    print("=" * 60)
    
    # Check AWS credentials
    try:
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        print(f"\nAWS Account: {identity['Account']}")
        print(f"IAM User/Role: {identity['Arn']}\n")
    except Exception as e:
        print(f"❌ AWS credentials not configured: {e}")
        sys.exit(1)
    
    # Step 1: Create database
    print("\n[1/4] Creating Athena Database...")
    if not setup_athena_database():
        print("❌ Failed to create database")
        sys.exit(1)
    
    # Step 2: Create table
    print("\n[2/4] Creating Athena Table...")
    if not setup_athena_table():
        print("❌ Failed to create table")
        sys.exit(1)
    
    # Step 3: Create Glue Crawler (optional, can fail if role doesn't exist)
    print("\n[3/4] Setting up Glue Crawler...")
    if not setup_glue_crawler():
        print("⚠️  Glue Crawler setup failed (this is optional)")
        print("    You can run Athena queries without the crawler.")
    
    # Step 4: Test query
    print("\n[4/4] Testing Athena Query...")
    test_athena_query()
    
    print("\n" + "=" * 60)
    print("✅ Setup Complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Add weather data to S3 by making API calls")
    print("2. Use analysis_service.py functions to query data")
    print("3. (Optional) Run Glue Crawler to update schema")
    print("\nEnvironment variables needed:")
    print("  - WEATHER_BUCKET_NAME (already set)")
    print("  - ATHENA_DATABASE=fitted_weather_db (optional, has default)")
    print("  - ATHENA_TABLE=weather_data (optional, has default)")


if __name__ == '__main__':
    main()