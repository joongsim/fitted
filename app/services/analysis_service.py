import boto3
import json


def query_weather_file(bucket, key):
    s3 = boto3.client("s3", region_name="us-west-1")

    print(f"Querying {key}...")

    try:
        # Fallback to GetObject since S3 Select is giving MethodNotAllowed
        response = s3.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        data = json.loads(content)
        
        # Client-side filtering (equivalent to the SQL query)
        # WHERE s.current.temp_c > 15
        if data.get('current', {}).get('temp_c', 0) > 15:
            result = {
                "location": data.get('location'),
                "temperature_c": data.get('current', {}).get('temp_c'),
                "condition_text": data.get('current', {}).get('condition', {}).get('text')
            }
            print(f"Match Found: {json.dumps(result)}")
        else:
            print(f"No match found (Temperature {data.get('current', {}).get('temp_c')} <= 15)")
            
    except Exception as e:
        print(f"Error querying file: {e}")
        raise e
