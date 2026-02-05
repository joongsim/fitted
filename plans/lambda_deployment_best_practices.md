# Best Practices for Deploying AWS Lambda Functions

Deploying serverless applications requires a shift from traditional server management to managing **infrastructure as code** and **release pipelines**.

## 1. Infrastructure as Code (IaC)
Never deploy by zipping files on your laptop and uploading them to the AWS Console.
*   **Why:** It's unrepeatable, error-prone, and has no history.
*   **Best Practice:** Use frameworks like **AWS SAM** (which we are using), **Serverless Framework**, or **Terraform**.
*   **How:** Define your function, permissions, and triggers in `template.yaml`.

## 2. Version Control & CI/CD
Your Git repository is the source of truth.
*   **Branching Strategy:**
    *   `main`: Production-ready code.
    *   `dev` or `feature/*`: Active development.
*   **The Pipeline:**
    1.  **Commit** to `main`.
    2.  **CI (Continuous Integration):** GitHub Actions / AWS CodeBuild runs `pytest`.
    3.  **Build:** `sam build` creates the deployment package.
    4.  **Deploy:** `sam deploy` updates the CloudFormation stack.

## 3. Lambda Versions and Aliases
AWS Lambda supports versioning (immutable snapshots of your code).
*   **Versions:** `v1`, `v2`, `v3`. Once published, they cannot change.
*   **Aliases:** Pointers to versions, e.g., `PROD` -> `v3`, `DEV` -> `v4`.
*   **Best Practice:** Always point your API Gateway to an **Alias** (e.g., `live`), not `$LATEST`. This allows you to swap the underlying version instantly without changing the API configuration.

## 4. Safe Deployments (Canary & Linear)
Don't switch 100% of traffic to the new version instantly.
*   **Canary Deployment:** Send 10% of traffic to the new version for 5 minutes. If no errors, switch the rest.
*   **Linear Deployment:** Shift 10% of traffic every minute.
*   **How:** AWS CodeDeploy handles this automatically if configured in SAM:
    ```yaml
    AutoPublishAlias: live
    DeploymentPreference:
      Type: Canary10Percent5Minutes
    ```

## 5. Environment Variables
Never commit secrets to Git.
*   **Bad:** `API_KEY = "secret"` in `main.py`.
*   **Better:** `os.environ.get("API_KEY")` and setting it in the Lambda console.
*   **Best:** Use **AWS Systems Manager Parameter Store** or **Secrets Manager**.
    *   In `template.yaml`:
        ```yaml
        Environment:
          Variables:
            API_KEY: {{resolve:ssm:/my-app/prod/api-key}}
        ```

## 6. Monitoring & Rollbacks
*   **Alarms:** Set up CloudWatch Alarms for `Errors` and `Duration`.
*   **Auto-Rollback:** If the Canary deployment triggers an alarm (e.g., error rate spikes), CodeDeploy should automatically roll back to the previous version.

## Summary Checklist
- [ ] Code is in Git.
- [ ] Tests pass before deploy.
- [ ] Infrastructure is defined in SAM/Terraform.
- [ ] Secrets are in Parameter Store/Secrets Manager.
- [ ] API Gateway points to an Alias, not `$LATEST`.
- [ ] Alarms are configured to catch bad deployments.