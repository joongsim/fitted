# Fitted Wardrobe Assistant

Welcome to the Fitted Wardrobe Assistant! This application provides personalized outfit suggestions based on your location's weather and your personal style preferences.

## Overview

This project uses an enterprise-grade AI and data stack to deliver a scalable and intelligent service:

- **FastAPI**: For the web application and high-performance API endpoints.
- **Databricks**: As the central Lakehouse platform for advanced analytics and ML storage.
- **dbt**: For modular, version-controlled data transformations within Databricks.
- **PyTorch**: For computer vision (clothing classification and feature extraction).
- **Pinecone**: High-performance vector database for RAG-based personal wardrobe management.
- **LangChain**: For agentic workflows, connecting LLMs to web search and vector retrieval.
- **Airflow**: To orchestrate complex multi-stage data and AI pipelines.
- **OpenRouter (LLM)**: Leveraging state-of-the-art models (like `gemini-3-flash`) for personalized styling.

See `plan.md` for the full roadmap and architecture diagrams.