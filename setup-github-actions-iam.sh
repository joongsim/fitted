#!/bin/bash

# Script to update GitHub Actions IAM role with required permissions
# Run this script to add the necessary permissions to your github_actions role

ROLE_NAME="github_actions"
POLICY_NAME="GitHubActionsDeploymentPolicy"
POLICY_FILE="github-actions-iam-policy.json"

echo "Updating IAM role: $ROLE_NAME"
echo "Adding/updating policy: $POLICY_NAME"

# Attach the policy to the role
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "$POLICY_NAME" \
  --policy-document file://"$POLICY_FILE"

if [ $? -eq 0 ]; then
  echo "✅ Successfully updated IAM role with deployment permissions"
  echo "You can now re-run your GitHub Actions workflow"
else
  echo "❌ Failed to update IAM role"
  echo "Make sure you have permissions to update IAM roles"
  exit 1
fi