# CI/CD Guide: GitHub Actions vs. AWS CodeBuild

This guide compares two popular options for automating your AWS Lambda deployments and provides setup instructions for both.

## 1. Comparison

| Feature | GitHub Actions | AWS CodeBuild |
| :--- | :--- | :--- |
| **Integration** | Built directly into GitHub. Seamless for code hosted there. | Native AWS service. Integrates deeply with CodePipeline, IAM, and VPCs. |
| **Setup** | Extremely easy. Just add a YAML file to `.github/workflows`. | Moderate. Requires setting up a Project, IAM Roles, and potentially CodePipeline. |
| **Cost** | Free tier (2000 mins/month). Pay-per-minute after. | Free tier (100 mins/month). Pay-per-minute. |
| **Security** | Secrets stored in GitHub. OIDC recommended for AWS access. | IAM Roles used directly. No long-term keys needed. |
| **Speed** | Fast startup. | Can have cold starts (unless using reserved capacity). |
| **Best For** | Most teams, especially if code is on GitHub. | Enterprise requirements, complex VPC networking, or if using AWS CodeCommit. |

**Recommendation:** Start with **GitHub Actions** for its simplicity and developer experience. Switch to CodeBuild if you have strict enterprise compliance needs.

---

## 2. GitHub Actions Setup

### Step 1: Create IAM Role for GitHub
Instead of using long-lived Access Keys (unsafe), use **OpenID Connect (OIDC)**.
1.  Go to AWS IAM > Identity providers > Add provider > OpenID Connect.
2.  URL: `https://token.actions.githubusercontent.com`
3.  Audience: `sts.amazonaws.com`
4.  **GitHub Organization:** Enter your GitHub username (if it's a personal repo) or your Organization name.
    *   *Example:* If your repo is `joose/fitted`, enter `joose`.
5.  **Repository:** Enter the repository name (e.g., `fitted`).
6.  Create a Role that trusts this provider and has permissions to deploy your stack (CloudFormation, Lambda, S3, IAM).

### Step 2: Create Workflow File
Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to AWS Lambda

on:
  push:
    branches: [ main ]

permissions:
  id-token: write # Required for OIDC
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.13'
          
      - name: Install SAM CLI
        uses: aws-actions/setup-sam@v2
        
      - name: Configure AWS Credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          role-to-assume: arn:aws:iam::YOUR_ACCOUNT_ID:role/GitHubActionRole
          aws-region: us-west-1
          
      - name: Build and Deploy
        run: |
          sam build
          sam deploy --no-confirm-changeset --no-fail-on-empty-changeset
```

---

## 3. AWS CodeBuild Setup

### Step 1: Create Build Project
1.  Go to AWS Console > CodeBuild > Create build project.
2.  **Source:** Connect to your GitHub repository.
3.  **Environment:** Managed Image > Ubuntu > Standard > 7.0.
4.  **Service Role:** Create a new service role (ensure it has admin permissions for the first deploy, then scope it down).

### Step 2: Create Buildspec File
Create `buildspec.yml` in your project root:

```yaml
version: 0.2

phases:
  install:
    runtime-versions:
      python: 3.13
    commands:
      - pip install --upgrade pip
      - pip install pytest
  
  pre_build:
    commands:
      - echo "Running tests..."
      - export PYTHONPATH=.
      - pytest tests/

  build:
    commands:
      - echo "Building SAM application..."
      - sam build

  post_build:
    commands:
      - echo "Deploying to AWS..."
      - sam deploy --no-confirm-changeset --no-fail-on-empty-changeset

artifacts:
  files:
    - template.yaml
    - .aws-sam/**/*
```

### Step 3: Run Build
Click "Start Build" in the AWS Console.