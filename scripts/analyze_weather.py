import boto3


def query_weather_file(bucket, key):
    s3 = boto3.client("s3")

    # SQL query to extract specific fields
    query = """
        SELECT s.location, s.temperature_c, s.condition_text
        FROM S3Object s
        WHERE s.current.temp_c > 15
        """

    print(f"Querying {key}...")

    response = s3.select_object_content(
        Bucket=bucket,
        Key=key,
        ExpressionType="SQL",
        Expression=query,
        InputSerialization={"JSON": {"Type": "DOCUMENT"}},
        OutputSerialization={"JSON": {"RecordDelimiter": "\n"}},
    )

    for event in response["Payload"]:
        if "Records" in event:
            print(f"Match Found: {event['Records']['Payload'].decode('utf-8')}")
