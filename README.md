# Fitted Wardrobe Assistant

Welcome to the Fitted Wardrobe Assistant! This application provides personalized outfit suggestions based on your location's weather and your personal style preferences.

## Overview

This project uses a modern data stack to deliver a scalable and intelligent service:

- **FastAPI**: For the web application and API endpoints.
- **Databricks**: As the central data platform for storing weather data and user preferences.
- **dbt**: For transforming and modeling data within Databricks.
- **Airflow**: To orchestrate data ingestion and processing pipelines.
- **Large Language Model (LLM)**: Integrated via OpenRouter (using `openai/gpt-4o-mini`) to generate creative and relevant outfit suggestions.

See `plan.md` for the detailed project plan and architecture.