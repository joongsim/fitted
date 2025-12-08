# GitHub Actions Deployment Fix

## Problems Encountered

### 1. API Gateway Tagging Permission Error (RESOLVED)
```
User: arn:aws:sts::903558039846:assumed-role/github_actions/GitHubActions
is not authorized to perform: apigateway:TagResource
```

### 2. Stack in ROLLBACK_COMPLETE State (NOW HANDLED AUTOMATICALLY)
```
Stack is in ROLLBACK_COMPLETE state and can not be updated.
```

This occurs when a previous deployment failed and CloudFormation rolled back. The updated deployment workflow now automatically detects and deletes stacks in this state before redeploying.

## Solution: Update IAM Role Permissions (REQUIRED)

The deployment workflow has been updated to automatically handle ROLLBACK_COMPLETE stacks, but you MUST update the IAM permissions first.

#### Steps:

1. **Run the setup script:**
   ```bash
   chmod +x setup-github-actions-iam.sh
   ./setup-github-actions-iam.sh
   ```

2. **Or manually update the IAM role in AWS Console:**
   - Go to IAM → Roles → `github_actions`
   - Click "Add inline policy"
   - Use the JSON from `github-actions-iam-policy.json`
   - Name it `GitHubActionsDeploymentPolicy`
   - Save the policy

3. **Or use AWS CLI:**
   ```bash
   aws iam put-role-policy \
     --role-name github_actions \
     --policy-name GitHubActionsDeploymentPolicy \
     --policy-document file://github-actions-iam-policy.json
   ```

#### What this fixes:
- ✅ Adds `apigateway:TagResource` permission for API Gateway
- ✅ Adds `cloudformation:DeleteStack` for handling failed stacks
- ✅ Adds `cloudformation:ListStacks` for checking stack status
- ✅ Grants all necessary SAM deployment permissions
- ✅ Allows Lambda, S3, and IAM role management for deployments

## What's Been Updated

### 1. GitHub Actions Workflow (`.github/workflows/deploy.yml`)
The workflow now:
- ✅ Checks if the stack is in ROLLBACK_COMPLETE state
- ✅ Automatically deletes the stack if needed
- ✅ Waits for deletion to complete
- ✅ Proceeds with fresh deployment

### 2. IAM Policy (`github-actions-iam-policy.json`)
The policy now includes:
- ✅ All API Gateway permissions including TagResource
- ✅ CloudFormation delete and list permissions
- ✅ All necessary deployment permissions

### 3. Template (`template.yaml`)
- ⚠️ Tags temporarily removed (will be restored after IAM update)

## Next Steps

1. **Update the IAM role** (choose one method):
   
   **Option A: Using the setup script**
   ```bash
   chmod +x setup-github-actions-iam.sh
   ./setup-github-actions-iam.sh
   ```
   
   **Option B: Using AWS CLI directly**
   ```bash
   aws iam put-role-policy \
     --role-name github_actions \
     --policy-name GitHubActionsDeploymentPolicy \
     --policy-document file://github-actions-iam-policy.json
   ```
   
   **Option C: Via AWS Console**
   - Go to IAM → Roles → `github_actions`
   - Click "Add inline policy"
   - Use JSON from `github-actions-iam-policy.json`
   - Name it `GitHubActionsDeploymentPolicy`

2. **Push your changes to trigger the workflow**
   ```bash
   git add .
   git commit -m "Fix deployment permissions and handle ROLLBACK_COMPLETE"
   git push
   ```

The workflow will now:
1. Detect the ROLLBACK_COMPLETE stack
2. Delete it automatically
3. Deploy a fresh stack successfully

## Verification

After applying Solution 1, verify the permissions:
```bash
aws iam get-role-policy \
  --role-name github_actions \
  --policy-name GitHubActionsDeploymentPolicy
```

Then re-trigger your GitHub Actions workflow by pushing to your branch.

## Additional Notes

- The provided IAM policy follows the principle of least privilege
- Resources are scoped to your account (903558039846) and region (us-west-1)
- The policy allows deployment of stacks named `fitted-wardrobe-*`
- All permissions are limited to resources with the `fitted-` prefix where possible