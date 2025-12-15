# AWS Athena Setup Guide

This guide explains how to set up AWS Athena for querying weather data stored in S3.

## Overview

AWS Athena allows you to run SQL queries directly on data stored in S3 without loading it into a database. This is perfect for analyzing historical weather data.

## Architecture

```
S3 Weather Data (Bronze Layer)
    ↓
AWS Glue Data Catalog (Schema Discovery)
    ↓
Athena (SQL Queries)
    ↓
Results (Analytics)
```

## Prerequisites

1. AWS credentials configured
2. Weather data stored in S3 (via the `/suggest-outfit` endpoint)
3. `WEATHER_BUCKET_NAME` environment variable set

## Setup Steps

### Option 1: Automated Setup (Recommended)

Run the setup script:

```bash
# Set environment variable
export WEATHER_BUCKET_NAME=your-bucket-name

# Run setup script
python scripts/setup_athena.py
```

This script will:
- Create Athena database (`fitted_weather_db`)
- Create weather data table with partitioning
- Set up Glue Crawler (optional)
- Run a test query

### Option 2: Manual Setup

If you prefer manual setup or the script fails:

#### 1. Create Athena Database

```sql
CREATE DATABASE IF NOT EXISTS fitted_weather_db
COMMENT 'Fitted weather data analytics database';
```

#### 2. Create Table with Partitions

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS fitted_weather_db.weather_data (
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
LOCATION 's3://your-bucket-name/raw/weather/'
TBLPROPERTIES (
    'projection.enabled' = 'true',
    'projection.dt.type' = 'date',
    'projection.dt.format' = 'yyyy-MM-dd',
    'projection.dt.range' = '2024-01-01,NOW',
    'projection.dt.interval' = '1',
    'projection.dt.interval.unit' = 'DAYS',
    'storage.location.template' = 's3://your-bucket-name/raw/weather/dt=${dt}/'
);
```

**Note:** The table is partitioned by date only. The S3 structure includes location folders (`/location=city/`), but these are just part of the file path, not partition keys. This allows flexible querying without partition constraints.
```

## Using the Analytics API

Once Athena is set up, you can use the new analytics endpoints:

### 1. Query by Temperature

```bash
GET /analytics/temperature?min_temp=20&date=2024-01-15
```

Returns all locations where temperature > 20°C on the specified date.

### 2. Location Weather Trend

```bash
GET /analytics/location/london?days=7
```

Returns 7-day weather trend for London with daily averages and min/max.

### 3. Summary Analytics

```bash
GET /analytics/summary?date=2024-01-15
```

Returns summary statistics for the specified date (unique locations, avg temp, etc.)

### 4. Query by Weather Condition

```bash
GET /analytics/condition/rain?date=2024-01-15
```

Returns all locations experiencing rain on the specified date.

## Example Queries

### Find Warm Locations

```python
from app.services import analysis_service

# Find locations with temp > 25°C
results = analysis_service.query_weather_by_temperature(min_temp=25.0)
print(f"Found {len(results)} warm locations")
```

### Get Location Trend

```python
# Get 7-day trend for Tokyo
trend = analysis_service.get_location_weather_trend('Tokyo', days=7)
for day in trend:
    print(f"{day['date']}: {day['avg_temp_c']}°C")
```

### Daily Summary

```python
# Get today's summary
summary = analysis_service.get_weather_analytics_summary()
print(f"Unique locations: {summary['unique_locations']}")
print(f"Avg temperature: {summary['avg_temperature']}°C")
```

## Custom SQL Queries

You can also write custom SQL queries:

```python
from app.services.analysis_service import athena_service

# Custom query
query = """
SELECT
    location.country,
    COUNT(DISTINCT location.name) as city_count,
    AVG(curr.temp_c) as avg_temp
FROM fitted_weather_db.weather_data
WHERE dt = '2024-01-15'
GROUP BY location.country
ORDER BY avg_temp DESC
"""

results = athena_service.query_and_get_results(query)
```

## Cost Optimization

Athena charges $5 per TB of data scanned. To minimize costs:

1. **Use Partitions**: Always filter by `dt` (date) in your WHERE clause
2. **Limit Results**: Use `LIMIT` clauses
3. **Parquet Format**: Consider converting JSON → Parquet (10x cheaper)
4. **Specific Columns**: Only SELECT columns you need

### Example Cost-Optimized Query

```sql
-- BAD: Scans all data
SELECT * FROM weather_data;

-- GOOD: Uses partition + limit
SELECT
    location.name,
    curr.temp_c
FROM weather_data
WHERE dt = '2024-01-15'  -- Partition filter
LIMIT 100;
```

## Schema Notes

The weather data uses `curr` instead of `current` to avoid Athena's reserved keyword restrictions. All queries reference `curr.temp_c`, `curr.condition`, etc.

## Troubleshooting

### "Table not found" Error

- Ensure database and table were created successfully
- Check that `WEATHER_BUCKET_NAME` matches your deployment
- Verify S3 bucket has data in the correct structure

### "No data returned" Error

- Check that you have weather data in S3
- Make API calls to `/suggest-outfit` to generate data
- Verify partition structure matches table definition

### Permission Errors

- Ensure Lambda has Athena IAM permissions (see `template.yaml`)
- Verify S3 bucket permissions allow Athena access
- Check Glue Crawler role has necessary permissions

## Environment Variables

Set these in your Lambda function or locally:

```bash
WEATHER_BUCKET_NAME=fitted-weather-data-stack-123456
ATHENA_DATABASE=fitted_weather_db  # Optional, defaults to this
ATHENA_TABLE=weather_data          # Optional, defaults to this
ATHENA_OUTPUT_LOCATION=s3://bucket/athena-results/  # Optional
```

## Next Steps

1. Generate weather data by making API calls
2. Run analytics queries to test setup
3. Build dashboards using query results
4. Set up scheduled queries for daily reports
5. Integrate with QuickSight for visualizations (optional)

## Resources

- [AWS Athena Documentation](https://docs.aws.amazon.com/athena/)
- [Athena Pricing](https://aws.amazon.com/athena/pricing/)
- [Partition Projection](https://docs.aws.amazon.com/athena/latest/ug/partition-projection.html)