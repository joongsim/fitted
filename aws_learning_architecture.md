# AWS + Databricks Learning Architecture
## Optimized for Skills Development & Portfolio Building

---

## Your Situation: Perfect for Hybrid Learning! ✅

**Given:**
- Personal/learning project
- Databricks Community Edition (FREE)
- Want to learn both AWS and Databricks
- No production constraints
- Cost is a concern

**Verdict:** YES! Hybrid AWS + Databricks makes total sense here. You'll learn more with this setup than going all-in on either platform.

---

## Recommended Learning Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                        Internet                             │
└────────────┬────────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────────┐
│                      AWS Free Tier                           │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────┐      ┌────────────────────┐           │
│  │  API Gateway     │─────▶│  Lambda Functions  │           │
│  │  (HTTP API)      │      │  - FastAPI Handler │           │
│  │  FREE            │      │  - Weather API     │           │
│  └──────────────────┘      │  FREE (1M/month)   │           │
│                            └────────────────────┘           │
│                                                              │
│  ┌──────────────────────────────────────────────┐           │
│  │         EC2 t3.micro (FREE Tier)             │           │
│  │  ┌──────────────┐    ┌───────────────┐      │           │
│  │  │   Airflow    │    │  PostgreSQL   │      │           │
│  │  │  Standalone  │    │  (Metadata)   │      │           │
│  │  └──────────────┘    └───────────────┘      │           │
│  │  Uses 750 hrs/month free tier               │           │
│  └──────────────────────────────────────────────┘           │
│                                                              │
│  ┌──────────────────┐      ┌────────────────────┐           │
│  │  S3 Buckets      │      │  CloudWatch Logs   │           │
│  │  - DAG code      │      │  - Debugging       │           │
│  │  - Raw data      │      │  FREE (5GB)        │           │
│  │  FREE (5GB)      │      └────────────────────┘           │
│  └──────────────────┘                                        │
│                                                              │
│  ┌──────────────────┐      ┌────────────────────┐           │
│  │  EventBridge     │      │  Secrets Manager*  │           │
│  │  (Scheduling)    │      │  - API Keys        │           │
│  │  FREE            │      │  $0.40/secret/mo   │           │
│  └──────────────────┘      └────────────────────┘           │
│                                                              │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   │ dbt CLI via Airflow
                   │
┌──────────────────▼───────────────────────────────────────────┐
│              Databricks Community Edition                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────┐             │
│  │  Delta Lake Tables                         │             │
│  │  - raw_weather_data                        │             │
│  │  - transformed_outfit_recommendations      │             │
│  │  - aggregated_weather_trends               │             │
│  └────────────────────────────────────────────┘             │
│                                                              │
│  ┌────────────────────────────────────────────┐             │
│  │  dbt Models                                │             │
│  │  - Staging models                          │             │
│  │  - Fact/Dimension models                   │             │
│  │  - Data quality tests                      │             │
│  └────────────────────────────────────────────┘             │
│                                                              │
│  FREE (15GB storage, limited compute)                       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## What You'll Learn

### AWS Skills
✅ **Lambda** - Serverless compute, event-driven architecture
✅ **API Gateway** - REST/HTTP APIs, request/response transformation
✅ **EC2** - Virtual machines, Linux administration, SSH
✅ **S3** - Object storage, bucket policies, versioning
✅ **CloudWatch** - Logging, monitoring, alarms
✅ **EventBridge** - Event-driven scheduling
✅ **IAM** - Roles, policies, security best practices
✅ **Secrets Manager** - Secure credential management
✅ **VPC** (optional) - Networking, security groups

### Databricks Skills
✅ **Delta Lake** - ACID transactions, time travel
✅ **Databricks SQL** - Analytics, queries
✅ **dbt on Databricks** - Data transformations
✅ **Unity Catalog** (Community Edition basics)
✅ **Notebooks** - Interactive development
✅ **Databricks CLI** - Automation

### Data Engineering Skills
✅ **Airflow** - Workflow orchestration, DAG design
✅ **dbt** - Transformations, testing, documentation
✅ **ELT Pattern** - Extract, Load, Transform
✅ **Data Modeling** - Star schema, slowly changing dimensions
✅ **API Integration** - Weather APIs, LLM APIs
✅ **CI/CD** - Infrastructure as Code (optional)

---

## Detailed Component Breakdown

### 1. FastAPI on Lambda (Primary Learning: Serverless)

**Setup:**
```python
# Use Mangum to adapt FastAPI for Lambda
from mangum import Mangum
from fastapi import FastAPI

app = FastAPI()

# Your existing routes
@app.get("/")
def read_root():
    return {"message": "Welcome!"}

# Lambda handler
handler = Mangum(app)
```

**What You'll Learn:**
- Serverless architecture patterns
- Cold start optimization
- Lambda layers for dependencies
- API Gateway integration
- Cost optimization (pay-per-request)

**Free Tier:**
- 1 million requests/month FREE
- 400,000 GB-seconds compute FREE
- Your API will likely always be free

---

### 2. Airflow on EC2 t3.micro (Primary Learning: Orchestration)

**Setup:**
- Single t3.micro instance (1 vCPU, 1GB RAM)
- SQLite/PostgreSQL metadata database (local)
- Airflow Standalone mode (webserver + scheduler)
- 750 hours/month FREE (entire month if 1 instance)

**What You'll Learn:**
- Airflow installation and configuration
- DAG development and testing
- Task dependencies and sensors
- Connection management
- Executor patterns (LocalExecutor)
- EC2 instance management
- Linux system administration

**Cost:**
- FREE for first 12 months (750 hrs/month)
- After: ~$7-10/month if kept running
- Alternative: Stop when not using, pay only for storage (~$2/month)

---

### 3. Databricks Community Edition (Primary Learning: Data Platform)

**What You Get (FREE):**
- 15GB cloud storage
- Limited compute (2 DBUs/day for notebooks)
- Single user
- Delta Lake support
- dbt-databricks compatible
- Notebooks, SQL editor

**What You'll Learn:**
- Delta Lake architecture
- dbt transformations on cloud data warehouse
- SQL optimization
- Data quality testing
- Documentation generation
- Medallion architecture (Bronze/Silver/Gold)

**Limitations:**
- Cannot run continuously
- Need to manually trigger from Airflow
- Limited concurrent jobs
- **Perfect for learning!**

---

### 4. S3 Storage (Primary Learning: Object Storage)

**Free Tier:**
- 5GB storage FREE for 12 months
- 20,000 GET requests
- 2,000 PUT requests

**What You'll Learn:**
- Bucket policies and permissions
- S3 event notifications
- Versioning and lifecycle policies
- Data organization patterns
- Pre-signed URLs
- S3 as a data lake

**Use Cases in Your Project:**
- Store DAG files for Airflow
- Raw weather data landing zone
- dbt artifacts storage
- API response caching

---

## Cost Breakdown (Monthly)

### First 12 Months (AWS Free Tier Active)

| Service | Free Tier | Expected Usage | Cost |
|---------|-----------|----------------|------|
| Lambda | 1M requests | 10k requests | $0 |
| API Gateway | N/A | 10k requests | $0.01 |
| EC2 t3.micro | 750 hours | 730 hours | $0 |
| S3 | 5GB storage | 2GB | $0 |
| CloudWatch | 5GB logs | 1GB | $0 |
| EventBridge | Unlimited | 100 rules | $0 |
| Secrets Manager | N/A | 3 secrets | $1.20 |
| **TOTAL** | | | **~$1.21/month** |

### After 12 Months

| Service | Cost |
|---------|------|
| Lambda | $0 (within free tier) |
| API Gateway | $0.01 |
| EC2 t3.micro | $7.50 (if always on) |
| S3 | $0.05 |
| CloudWatch | $0.10 |
| Secrets Manager | $1.20 |
| **TOTAL (always on)** | **~$8.86/month** |
| **TOTAL (stop EC2)** | **~$3.36/month** |

**Databricks:** FREE forever (Community Edition)

---

## Learning Path & Implementation Roadmap

### Phase 1: AWS Basics (Week 1-2)
**Goal:** Get comfortable with core AWS services

**Tasks:**
- [ ] Create AWS account and set up billing alerts
- [ ] Deploy FastAPI to Lambda with API Gateway
- [ ] Set up S3 bucket for data storage
- [ ] Configure Secrets Manager for API keys
- [ ] Set up CloudWatch logging

**Skills:** Lambda, API Gateway, S3, IAM basics

---

### Phase 2: Airflow Setup (Week 3-4)
**Goal:** Get orchestration running

**Tasks:**
- [ ] Launch EC2 t3.micro instance
- [ ] Install Airflow in standalone mode
- [ ] Configure S3 for DAG storage (optional)
- [ ] Create first DAG: weather data ingestion
- [ ] Set up EventBridge for scheduling (optional)
- [ ] Configure Airflow connections to Databricks

**Skills:** EC2, Airflow, Linux administration

---

### Phase 3: Databricks Integration (Week 5-6)
**Goal:** Connect data pipeline to Databricks

**Tasks:**
- [ ] Set up Databricks Community Edition account
- [ ] Configure dbt project for Databricks
- [ ] Create Delta tables for raw data
- [ ] Build staging models in dbt
- [ ] Add data quality tests
- [ ] Automate dbt runs from Airflow

**Skills:** Databricks, Delta Lake, dbt

---

### Phase 4: End-to-End Pipeline (Week 7-8)
**Goal:** Connect all components

**Tasks:**
- [ ] Weather API → Lambda → S3 (raw data)
- [ ] Airflow DAG → Load S3 to Databricks
- [ ] dbt transformations (Bronze → Silver → Gold)
- [ ] Expose transformed data via API
- [ ] Add monitoring and alerts

**Skills:** ELT patterns, data modeling

---

### Phase 5: Advanced Features (Week 9-12)
**Goal:** Portfolio-worthy additions

**Tasks:**
- [ ] Infrastructure as Code (Terraform)
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Data quality dashboards
- [ ] Cost optimization analysis
- [ ] Documentation and README

**Skills:** IaC, DevOps, technical writing

---

## Alternative: Even More Cost-Optimized

If you want to learn without spending anything:

### Ultra-Budget Option (FREE)

```
┌─────────────────────────────────────────┐
│     GitHub Actions (FREE CI/CD)         │
│  - Scheduled workflows                  │
│  - Python environment                   │
│  - Can run dbt, API calls               │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│   Databricks Community Edition (FREE)   │
│  - Store all data                       │
│  - Run dbt transformations              │
│  - SQL analytics                        │
└──────────────────────────────────────────┘
```

**What Changes:**
- Replace Airflow with GitHub Actions workflows
- Replace EC2 with scheduled GitHub Actions
- Replace Lambda with FastAPI in GitHub Codespaces (free)
- Still learn 80% of the concepts

**Cost:** $0/month
**Tradeoff:** Less "real-world" but still valuable

---

## Recommended Approach: Start Small, Add Complexity

### Month 1: Proof of Concept
**Deploy:**
- Lambda + API Gateway (FastAPI)
- Databricks Community (dbt models)
- Manual triggering (no Airflow yet)

**Cost:** $1/month
**Learning:** 40% of concepts

### Month 2: Add Orchestration
**Add:**
- EC2 t3.micro with Airflow
- S3 for data storage
- Automated scheduling

**Cost:** $1/month (free tier)
**Learning:** 75% of concepts

### Month 3+: Production-Ready
**Add:**
- Monitoring and alerts
- Infrastructure as Code
- CI/CD pipelines
- Documentation

**Cost:** $1-3/month
**Learning:** 100% of concepts

---

## Portfolio Value

This architecture demonstrates knowledge of:

✅ **Multi-cloud integration** (AWS + Databricks)
✅ **Modern data stack** (Airflow + dbt + Delta Lake)
✅ **API development** (FastAPI, REST APIs)
✅ **Infrastructure** (EC2, Lambda, serverless)
✅ **Cost optimization** (Free tier usage)
✅ **Real-world patterns** (ELT, medallion architecture)
✅ **Practical problem-solving** (weather-based outfit suggestions)

**Impressive on resume for:**
- Data Engineering roles
- Cloud Engineering roles
- Backend Engineering roles
- DevOps roles

---

## Learning Resources

### AWS
- [AWS Free Tier](https://aws.amazon.com/free/)
- [AWS Well-Architected Labs](https://wellarchitectedlabs.com/)
- [AWS Skill Builder](https://skillbuilder.aws/)

### Databricks
- [Databricks Academy](https://www.databricks.com/learn/training)
- [Databricks Community](https://community.databricks.com/)
- [Delta Lake Documentation](https://docs.delta.io/)

### dbt
- [dbt Learn](https://courses.getdbt.com/)
- [dbt Best Practices](https://docs.getdbt.com/guides/best-practices)

### Airflow
- [Airflow Documentation](https://airflow.apache.org/docs/)
- [Astronomer Guides](https://www.astronomer.io/guides/)

---

## My Recommendation

**Start with the phased approach:**

1. **Week 1-2:** Lambda + API Gateway + Databricks only
   - Cost: ~$1/month
   - Get the API working end-to-end
   - Build 2-3 dbt models

2. **Week 3-4:** Add Airflow on EC2
   - Still ~$1/month (free tier)
   - Automate the data pipeline
   - Learn orchestration

3. **Month 2-3:** Add advanced features
   - Infrastructure as Code
   - Monitoring
   - Documentation

**Why this works:**
- ✅ Minimal financial risk
- ✅ Learn incrementally
- ✅ Build portfolio project
- ✅ Gain both AWS and Databricks skills
- ✅ Real-world architecture patterns

**The hybrid approach makes complete sense for learning** - you get exposure to both platforms without the cost/complexity of production systems.

Want me to help you get started with the implementation? I can create the Terraform configs, Lambda deployment scripts, or Airflow DAG setups!