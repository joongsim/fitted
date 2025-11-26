# Git Workflow: Merging vs. Pull Requests

When moving code from a development branch (e.g., `dev` or `feature/weather-api`) into your production branch (`main`), you have a few options.

## The Golden Rule: Use Pull Requests (PRs)

**Never merge directly into `main` locally.**

### Why?
1.  **Code Review:** A PR allows your team (or future you) to review the changes before they go live.
2.  **CI/CD Triggers:** Most CI/CD pipelines (like the GitHub Actions one we set up) run tests automatically when a PR is opened. This prevents broken code from ever reaching `main`.
3.  **History:** PRs create a clear audit trail of *why* changes were made.

## The Recommended Workflow

### 1. Create a Feature Branch
Start from `main` and create a new branch for your work.
```bash
git checkout main
git pull origin main  # Ensure you have the latest
git checkout -b feature/add-weather-api
```

### 2. Do Your Work
Make changes, commit them.
```bash
git add .
git commit -m "feat: implement weather api integration"
```

### 3. Push to GitHub
```bash
git push -u origin feature/add-weather-api
```

### 4. Open a Pull Request
Go to your repository on GitHub. You'll see a "Compare & pull request" button.
*   **Base:** `main`
*   **Compare:** `feature/add-weather-api`

### 5. Merge (on GitHub)
Once tests pass and you've reviewed the code, click **"Squash and merge"** or **"Merge pull request"** on GitHub.
*   **Squash and merge:** Combines all your little commits into one clean commit on `main`. (Recommended for cleaner history).

### 6. Update Local Main
Now that `main` is updated on GitHub, update your local machine.
```bash
git checkout main
git pull origin main
```

## What about `git merge` vs `git pull`?

*   **`git pull`**: This is actually `git fetch` + `git merge`. It downloads changes from GitHub and tries to merge them into your current branch. Use this to keep your local `main` up to date.
*   **`git merge`**: Combines two branches. You rarely do this manually for `main` if you are using PRs.

## Summary
1.  Branch off `main`.
2.  Push branch to GitHub.
3.  Open PR.
4.  Merge PR on GitHub.
5.  `git pull` on local `main`.