# Weather-Based Outfit Suggestion App Plan

This document outlines the plan for creating a scalable, weather-based outfit suggestion application.

## Technologies
- Python (FastAPI)
- Databricks
- Airflow
- dbt

## High-Level Architecture

```mermaid
graph TD
    subgraph User Interface
        A[FastAPI App]
    end

    subgraph Data Ingestion & Orchestration
        B[Airflow]
    end

    subgraph Data Platform
        C[Databricks]
    end

    subgraph Data Transformation
        D[dbt]
    end

    subgraph External Services
        E[Weather API]
        F[LLM Provider]
    end

    A -- "User Request" --> B
    B -- "Triggers Weather Data Job" --> E
    E -- "Weather Data" --> C
    B -- "Triggers dbt transformation" --> D
    D -- "Transforms data in" --> C
    A -- "Gets User Preferences & Weather" --> C
    A -- "Sends prompt to LLM" --> F
    F -- "Outfit Suggestion" --> A
    A -- "Saves preferences/history" --> C
    A -- "Displays Suggestion" --> User
```

## TODO

- [ ] Set up the project structure
- [ ] Develop the FastAPI application
- [ ] Integrate with a weather API
- [ ] Set up the Databricks environment
- [ ] Develop dbt models
- [ ] Create Airflow DAGs
- [ ] Integrate with a Large Language Model (LLM)
- [ ] Implement user preference storage
