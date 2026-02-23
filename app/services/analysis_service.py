import boto3
import json
import logging
import time
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class AthenaQueryService:
    """
    Service for querying weather data using AWS Athena.
    Provides SQL-based analytics on S3 data without loading into a database.
    """

    def __init__(self) -> None:
        self._athena_client: Any = None
        self._s3_client: Any = None
        self.database = os.environ.get('ATHENA_DATABASE', 'fitted_weather_db')
        self.table = os.environ.get('ATHENA_TABLE', 'weather_data')
        self.output_location = os.environ.get(
            'ATHENA_OUTPUT_LOCATION',
            f"s3://{os.environ.get('WEATHER_BUCKET_NAME')}/athena-results/",
        )

    @property
    def athena_client(self) -> Any:
        """Lazily create the Athena boto3 client on first use."""
        if self._athena_client is None:
            self._athena_client = boto3.client('athena')
        return self._athena_client

    @property
    def s3_client(self) -> Any:
        """Lazily create the S3 boto3 client on first use."""
        if self._s3_client is None:
            self._s3_client = boto3.client('s3')
        return self._s3_client
        
    def execute_query(self, query: str, wait: bool = True) -> str:
        """
        Execute an Athena query and return the query execution ID.
        
        Args:
            query: SQL query string
            wait: If True, wait for query to complete before returning
            
        Returns:
            Query execution ID
        """
        try:
            response = self.athena_client.start_query_execution(
                QueryString=query,
                QueryExecutionContext={'Database': self.database},
                ResultConfiguration={'OutputLocation': self.output_location}
            )
            
            query_execution_id = response['QueryExecutionId']
            logger.info("Started Athena query: %s", query_execution_id)
            logger.debug("Athena query SQL: %.500s", query.strip())

            if wait:
                self._wait_for_query(query_execution_id)

            return query_execution_id

        except Exception as e:
            logger.error("Error executing Athena query: %s", e, exc_info=True)
            raise
    
    def _wait_for_query(self, query_execution_id: str, max_wait: int = 60) -> str:
        """Wait for query to complete, checking every 1 second."""
        start_time = time.time()

        while time.time() - start_time < max_wait:
            response = self.athena_client.get_query_execution(
                QueryExecutionId=query_execution_id
            )

            status = response['QueryExecution']['Status']['State']

            if status == 'SUCCEEDED':
                elapsed = time.time() - start_time
                logger.info(
                    "Athena query %s succeeded in %.1fs", query_execution_id, elapsed
                )
                return status
            elif status in ['FAILED', 'CANCELLED']:
                elapsed = time.time() - start_time
                reason = response['QueryExecution']['Status'].get(
                    'StateChangeReason', 'Unknown'
                )
                logger.error(
                    "Athena query %s %s after %.1fs: %s",
                    query_execution_id,
                    status,
                    elapsed,
                    reason,
                )
                raise Exception(f"Query {status}: {reason}")

            time.sleep(1)

        raise TimeoutError(f"Query {query_execution_id} timed out after {max_wait}s")
    
    def get_query_results(self, query_execution_id: str) -> List[Dict[str, Any]]:
        """
        Get results from a completed query.
        
        Returns:
            List of result rows as dictionaries
        """
        try:
            results = []
            paginator = self.athena_client.get_paginator('get_query_results')
            
            for page in paginator.paginate(QueryExecutionId=query_execution_id):
                rows = page['ResultSet']['Rows']
                
                # First row is headers
                if not results:
                    headers = [col['VarCharValue'] for col in rows[0]['Data']]
                    rows = rows[1:]  # Skip header row
                else:
                    headers = [col['VarCharValue'] for col in page['ResultSet']['Rows'][0]['Data']]
                
                for row in rows:
                    row_data = {}
                    for i, col in enumerate(row['Data']):
                        value = col.get('VarCharValue', None)
                        row_data[headers[i]] = value
                    results.append(row_data)
            
            return results
            
        except Exception as e:
            logger.error("Error getting query results: %s", e, exc_info=True)
            raise
    
    def query_and_get_results(self, query: str) -> List[Dict[str, Any]]:
        """
        Execute a query and return results in one call.
        
        Args:
            query: SQL query string
            
        Returns:
            List of result rows as dictionaries
        """
        query_id = self.execute_query(query, wait=True)
        return self.get_query_results(query_id)


# Initialize the Athena service
athena_service = AthenaQueryService()


def query_weather_by_temperature(min_temp: float = 15.0,
                                  date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Query weather data where temperature is above a threshold.
    
    Args:
        min_temp: Minimum temperature in Celsius
        date: Optional date filter (YYYY-MM-DD format)
        
    Returns:
        List of matching weather records
    """
    date_filter = f"AND dt = '{date}'" if date else ""
    
    query = f"""
    SELECT
        location.name as location,
        location.country as country,
        curr.temp_c as temperature_c,
        curr.condition.text as condition_text,
        curr.humidity as humidity,
        dt as date
    FROM {athena_service.table}
    WHERE curr.temp_c > {min_temp}
    {date_filter}
    ORDER BY curr.temp_c DESC
    LIMIT 100
    """
    
    try:
        results = athena_service.query_and_get_results(query)
        logger.info("Found %d locations with temp > %s°C", len(results), min_temp)
        return results
    except Exception as e:
        logger.error("Error querying weather data: %s", e, exc_info=True)
        raise


def get_location_weather_trend(location: str, days: int = 7) -> List[Dict[str, Any]]:
    """
    Get weather trend for a specific location over the past N days.
    
    Args:
        location: Location name
        days: Number of days to look back
        
    Returns:
        List of weather records ordered by date
    """
    # Calculate date range
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    query = f"""
    SELECT
        dt as date,
        location.name as location,
        AVG(curr.temp_c) as avg_temp_c,
        MAX(curr.temp_c) as max_temp_c,
        MIN(curr.temp_c) as min_temp_c,
        AVG(curr.humidity) as avg_humidity,
        COUNT(*) as num_readings
    FROM {athena_service.table}
    WHERE location.name LIKE '%{location}%'
    AND dt BETWEEN '{start_date}' AND '{end_date}'
    GROUP BY dt, location.name
    ORDER BY dt DESC
    """
    
    try:
        results = athena_service.query_and_get_results(query)
        logger.info("Retrieved %d days of weather data for %s", len(results), location)
        return results
    except Exception as e:
        logger.error("Error querying location trend: %s", e, exc_info=True)
        raise


def get_weather_analytics_summary(date: Optional[str] = None) -> Dict[str, Any]:
    """
    Get summary analytics for weather data.
    
    Args:
        date: Optional specific date (YYYY-MM-DD), defaults to today
        
    Returns:
        Dictionary with analytics summary
    """
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')
    
    query = f"""
    SELECT
        COUNT(DISTINCT location.name) as unique_locations,
        AVG(curr.temp_c) as avg_temperature,
        MAX(curr.temp_c) as max_temperature,
        MIN(curr.temp_c) as min_temperature,
        AVG(curr.humidity) as avg_humidity,
        COUNT(*) as total_readings
    FROM {athena_service.table}
    WHERE dt = '{date}'
    """
    
    try:
        results = athena_service.query_and_get_results(query)
        if results:
            summary = results[0]
            logger.info("Analytics summary for %s: %s", date, summary)
            return summary
        return {}
    except Exception as e:
        logger.error("Error getting analytics summary: %s", e, exc_info=True)
        raise


def get_weather_by_condition(condition: str, date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Query weather data by condition (e.g., 'Rain', 'Clear', 'Cloudy').
    
    Args:
        condition: Weather condition to search for
        date: Optional date filter
        
    Returns:
        List of matching locations
    """
    date_filter = f"AND dt = '{date}'" if date else ""
    
    query = f"""
    SELECT
        location.name as location,
        location.country as country,
        curr.temp_c as temperature_c,
        curr.condition.text as condition,
        curr.humidity as humidity,
        dt as date
    FROM {athena_service.table}
    WHERE LOWER(curr.condition.text) LIKE LOWER('%{condition}%')
    {date_filter}
    LIMIT 100
    """
    
    try:
        results = athena_service.query_and_get_results(query)
        logger.info("Found %d locations with condition: %s", len(results), condition)
        return results
    except Exception as e:
        logger.error("Error querying by condition: %s", e, exc_info=True)
        raise


# Legacy function for backward compatibility
def query_weather_file(bucket: str, key: str):
    """
    Legacy function - queries individual S3 file.
    Kept for backward compatibility but recommend using Athena functions.
    """
    s3 = boto3.client("s3", region_name="us-west-1")
    
    logger.warning("Using legacy file query for %s. Consider using Athena queries instead.", key)

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        data = json.loads(content)

        if data.get('current', {}).get('temp_c', 0) > 15:
            result = {
                "location": data.get('location'),
                "temperature_c": data.get('current', {}).get('temp_c'),
                "condition_text": data.get('current', {}).get('condition', {}).get('text'),
            }
            logger.info("Match found: %s", json.dumps(result))
        else:
            logger.info(
                "No match found (Temperature %s <= 15)",
                data.get('current', {}).get('temp_c'),
            )

    except Exception as e:
        logger.error("Error querying file: %s", e, exc_info=True)
        raise e
