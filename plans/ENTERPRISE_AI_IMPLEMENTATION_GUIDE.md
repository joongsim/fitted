# 🚀 Enterprise AI Implementation Guide: Weeks 4-5

This tutorial outlines the transition from a lightweight weather app to an enterprise-grade **Smart Wardrobe Platform**.

## Table of Contents
1. [Environment & Setup](#1-environment--setup)
2. [Phase 1: Databricks & dbt (The Brain)](#phase-1-databricks--dbt)
3. [Phase 2: PyTorch & MLflow (The Vision)](#phase-2-pytorch--mlflow)
4. [Phase 3: Pinecone & RAG (The Memory)](#phase-3-pinecone--rag)
5. [Phase 4: LangChain Agent (The Action)](#phase-4-langchain-agent)
6. [Phase 5: FastAPI & Airflow (The Conductor)](#phase-5-fastapi--airflow)

---

## 1. Environment & Setup

### Install Dependencies
Add the following to your `requirements.txt`:
```text
# AI & Data Stack
torch                   # Deep learning framework for vision tasks
torchvision             # Computer vision utilities and pre-trained models
pillow                  # Image processing library
pinecone-client         # Vector database for RAG (Retrieval Augmented Generation)
langchain               # Framework for LLM orchestration and agents
langchain-community     # Community-maintained integrations for LangChain
sentence-transformers    # SOTA models for generating text embeddings
duckduckgo-search       # Tool for web search within agents
mlflow                  # ML lifecycle management (tracking, versioning)
databricks-sql-connector # Connectivity to Databricks SQL warehouses
```

### Environment Variables
Ensure the following are set in your `.env` or AWS Lambda configuration:
- `DATABRICKS_HOST`: Your workspace URL.
- `DATABRICKS_TOKEN`: Your Personal Access Token.
- `DATABRICKS_HTTP_PATH`: Found in your SQL Warehouse settings.
- `PINECONE_API_KEY`: From your Pinecone console.

---

## Phase 1: Databricks & dbt

We use the **Medallion Architecture** to transform raw data into "Fashion Intelligence."

### Step 1.1: Staging (Silver Layer)
Create `dbt/fitted_dbt/models/staging/stg_weather.sql`:
```sql
{{ config(materialized='view') }} -- 1. Materialize as a view to save storage; it's computed on-the-fly.

SELECT
    location.name AS location_name,       -- 2. Extract and rename nested fields for clarity.
    curr.temp_c AS temperature,          -- 3. Standardize column names.
    curr.condition.text AS condition_text, -- 4. Traverse JSON-like structures from raw data.
    curr.humidity AS humidity,
    to_date(dt) AS observation_date      -- 5. Cast timestamp to date for easier partitioning/joining.
FROM {{ source('raw', 'weather_data') }} -- 6. Reference the raw source table defined in schema.yml.
```

### Step 1.2: Marts (Gold Layer)
Create `dbt/fitted_dbt/models/marts/fct_fashion_context.sql`:
```sql
{{ config(materialized='table') }} -- 1. Materialize as a table for high-performance querying by the app.

SELECT
    *,
    CASE 
        WHEN temperature < 5 THEN 'Heavy Winter'
        WHEN temperature BETWEEN 5 AND 15 THEN 'Light Layers'
        WHEN temperature BETWEEN 15 AND 25 THEN 'Casual Spring'
        ELSE 'Summer Breathable'
    END AS weather_category,             -- 2. Business Logic: Categorize weather for fashion logic.
    (humidity > 80 OR condition_text LIKE '%Rain%') AS requires_waterproof -- 3. Derived boolean flag.
FROM {{ ref('stg_weather') }}            -- 4. Reference the Silver layer (staging) model.
```

### Discussion: Tradeoffs
*   **Databricks vs. Local Postgres**: Databricks offers massive scalability for billions of rows but introduces higher cost and network latency for small apps.
*   **Views vs. Tables**: Silver layers often use **Views** to avoid data duplication, while Gold layers use **Tables** to ensure the FastAPI app gets sub-second response times.
*   **dbt vs. Manual SQL**: dbt provides version control, testing, and lineage documentation, which is essential for enterprise teams but overkill for a solo weekend project.

---

## Phase 2: PyTorch & MLflow

### Step 2.1: Implement Vision Service
Create `app/services/vision_service.py`:
```python
import torch
import torchvision.transforms as T
from torchvision.models import resnet18, ResNet18_Weights
from PIL import Image
import mlflow

class VisionService:
    def __init__(self):
        # 1. Load pre-trained ResNet18 weights (Standard for efficient image classification).
        self.weights = ResNet18_Weights.DEFAULT
        self.model = resnet18(weights=self.weights)
        self.model.eval() # 2. Set to evaluation mode (disables dropout/batchnorm for inference).
        self.transform = self.weights.transforms() # 3. Use the exact preprocessing the model was trained on.

    def classify_clothing(self, image_bytes):
        # 4. Start an MLflow run to track this specific classification event.
        with mlflow.start_run(run_name="clothing_classification"):
            img = Image.open(image_bytes).convert('RGB') # 5. Load and ensure 3-channel RGB format.
            batch = self.transform(img).unsqueeze(0)    # 6. Apply transforms and add batch dimension (1, C, H, W).
            
            with torch.no_grad(): # 7. Disable gradient calculation to save memory and speed up inference.
                prediction = self.model(batch).squeeze(0)
            
            class_id = prediction.argmax().item() # 8. Get index of the highest confidence score.
            category = self.weights.meta["categories"][class_id] # 9. Map index to human-readable label.
            
            mlflow.log_param("predicted_class", category) # 10. Log metadata to MLflow for auditing/debugging.
            return category
```

### Discussion: Tradeoffs
*   **ResNet18 vs. ResNet50/101**: ResNet18 is faster and has a smaller memory footprint (perfect for CPU-based Lambda), but deeper models (ResNet50+) offer higher accuracy at the cost of latency.
*   **MLflow Logging**: Tracking every inference allows you to detect "model drift" (e.g., if the model starts failing on summer clothes in winter), but it introduces extra API calls that can slightly slow down response times.
*   **Pre-trained vs. Fine-tuned**: Using `DEFAULT` weights is free and fast, but for a professional fashion app, you would eventually "fine-tune" the last layer on a specific clothing dataset (like DeepFashion).

---

## Phase 3: Pinecone & RAG

### Step 3.1: Initialize Pinecone
Create `app/services/rag_service.py`:
```python
from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

class RAGService:
    def __init__(self, api_key: str):
        # 1. Initialize Pinecone client for vector search.
        self.pc = Pinecone(api_key=api_key)
        self.index = self.pc.Index("wardrobe-index")
        # 2. Load 'all-MiniLM-L6-v2' (A lightweight but powerful text embedding model).
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')

    def add_item(self, item_id, description, metadata):
        # 3. Convert text description into a numerical vector (embedding).
        vector = self.encoder.encode(description).tolist()
        # 4. Upsert (Update or Insert) the vector into the cloud database.
        self.index.upsert(vectors=[(item_id, vector, metadata)])

    def query_wardrobe(self, weather_context, top_k=3):
        # 5. Convert query text (e.g. "rainy cold weather") into the same vector space.
        query_vector = self.encoder.encode(weather_context).tolist()
        # 6. Perform Cosine Similarity search to find the most relevant items.
        results = self.index.query(vector=query_vector, top_k=top_k, include_metadata=True)
        # 7. Extract and return the original metadata (colors, materials, etc.).
        return [res.metadata for res in results.matches]
```

### Discussion: Tradeoffs
*   **Vector Search vs. Keyword Search**: Keyword search ("Rain") fails if the description says "Waterproof." Vector search understands the *meaning*, making it far more robust for fashion.
*   **MiniLM vs. OpenAI Embeddings**: MiniLM runs locally (free, fast, private), while OpenAI's `text-embedding-3-small` is more powerful but costs money and requires an internet call.
*   **Pinecone vs. FAISS**: Pinecone is a managed service (SaaS), handling scaling and persistence for you. FAISS is a library that runs in memory, which is faster but loses all data if the server restarts.

---

## Phase 4: LangChain Agent

### Step 4.1: Agentic Search Service
Create `app/services/agent_service.py`:
```python
from langchain.agents import initialize_agent, Tool
from langchain.agents import AgentType
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_openai import ChatOpenAI

class ShoppingAgent:
    def __init__(self):
        # 1. Define the Search Tool (Allows the LLM to access the live internet).
        self.search = DuckDuckGoSearchRun()
        self.tools = [
            Tool(
                name="RetailSearch",
                func=self.search.run,
                description="Use this for finding clothes for sale online. Input should be a specific clothing item."
            )
        ]
        # 2. Initialize the LLM (GPT-4o-mini is chosen for its balance of speed and reasoning).
        self.llm = ChatOpenAI(model="gpt-4o-mini")
        # 3. Create a ReAct Agent (Reason + Act). 
        # It thinks step-by-step: "I need a raincoat -> I'll search RetailSearch -> I'll check results".
        self.agent = initialize_agent(
            self.tools, self.llm, 
            agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True # 4. Set to True to see the "thought process" in the logs.
        )

    def find_missing_item(self, item_name):
        # 5. Execute the agent's logic loop.
        return self.agent.run(f"Find a {item_name} for sale online. Return a few store links.")
```

### Discussion: Tradeoffs
*   **Agents vs. Hardcoded Logic**: Agents are flexible and can handle weird user requests, but they are "non-deterministic" (they might give different answers every time).
*   **GPT-4o vs. GPT-4o-mini**: The 'mini' model is 10x cheaper and much faster, usually sufficient for simple tool-use tasks like searching. Use the full GPT-4o only if the agent needs complex reasoning.
*   **Tool Sprawl**: Giving an agent too many tools (e.g., Search, Database, Email, Calculator) can confuse it, causing it to pick the wrong tool. Keep the toolset "lean."

---

## Phase 5: FastAPI & Airflow

### Step 5.1: Databricks SQL Connector
In `app/services/llm_service.py`:
```python
from databricks import sql

def get_gold_weather_context(location, host, path, token):
    # 1. Establish a secure connection to the Databricks SQL Warehouse.
    with sql.connect(server_hostname=host, http_path=path, access_token=token) as conn:
        with conn.cursor() as cursor:
            # 2. Fetch the pre-processed 'Gold' fashion data.
            # 3. NOTE: Using f-strings in SQL is dangerous (SQL Injection). 
            # In production, use parameterized queries: cursor.execute("... WHERE loc = ?", (location,))
            cursor.execute(f"SELECT * FROM gold.fashion_context WHERE location = '{location}'")
            return cursor.fetchone()
```

### Step 5.2: Airflow Orchestration
Update your DAG:
```python
# 1. BashOperator executes dbt commands directly on the server.
dbt_run = BashOperator(
    task_id='dbt_run_gold',
    # 2. Run ONLY the gold model to save time and compute.
    bash_command='cd /home/joose/fitted/dbt/fitted_dbt && dbt run --select marts.fct_fashion_context'
)
```

### Discussion: Tradeoffs
*   **Airflow vs. Cron**: Cron is simple but has no UI, no retry logic, and no dependency tracking. Airflow is a "heavy" platform but ensures that if Step A fails, Step B won't run.
*   **FastAPI vs. Flask**: FastAPI is natively asynchronous and generates automatic Swagger documentation, making it the modern standard for AI-driven backends.
*   **Centralized vs. Decentralized Data**: Fetching data from Databricks (Centralized) ensures the ML model and the App see the same "Truth," but it creates a dependency on the Databricks cluster being "Up."
