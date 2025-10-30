#!/usr/bin/env python3
"""
GitHub PR Metrics Extractor
Fetches PR metrics for a given repository and date range using GitHub CLI.
"""

import subprocess
import json
import sys
import re
from datetime import datetime


def run_gh_command(command):
    """Execute a gh CLI command and return parsed JSON output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {command}", file=sys.stderr)
        print(f"Error: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from command: {command}", file=sys.stderr)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def extract_repo_name(repo_url):
    """Extract repository name from GitHub URL."""
    # Handle both https://github.com/owner/repo and https://github.com/owner/repo.git
    match = re.search(r'github\.com/[^/]+/([^/\.]+)', repo_url)
    if match:
        return match.group(1)
    else:
        print(f"Invalid GitHub URL: {repo_url}", file=sys.stderr)
        sys.exit(1)


def extract_owner_repo(repo_url):
    """Extract owner/repo from GitHub URL."""
    match = re.search(r'github\.com/([^/]+)/([^/\.]+)', repo_url)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    else:
        print(f"Invalid GitHub URL: {repo_url}", file=sys.stderr)
        sys.exit(1)


def extract_jira_id_or_words(title):
    """Extract Jira ID from beginning of title or first 3 words."""
    # Case-insensitive match for XXX-YYYY at the beginning
    jira_match = re.match(r'^([a-zA-Z]+-\d+)', title.strip())
    if jira_match:
        return jira_match.group(1)

    # If no Jira ID, return first 3 words
    words = title.strip().split()
    return ' '.join(words[:3])


def get_merged_prs(owner_repo, start_date, end_date):
    """Fetch PRs merged to main in the specified date range."""
    # Query PRs merged in the date range
    command = f'gh pr list --repo {owner_repo} --state merged --base main --search "merged:{start_date}..{end_date}" --json number,title,mergedBy,mergedAt,createdAt,comments,reviews,mergeCommit --limit 1000'

    return run_gh_command(command)


def count_comments(pr_data):
    """Count total comments (conversation + review comments)."""
    conversation_comments = len(pr_data.get('comments', []))

    # Count review comments
    review_comments = 0
    for review in pr_data.get('reviews', []):
        # Each review can have multiple comments
        review_comments += 1  # The review itself

    # Need to get review comments separately as they're not in the reviews array
    return conversation_comments + review_comments


def get_pr_comment_count(owner_repo, pr_number):
    """Get detailed comment count for a PR including review comments."""
    # Get conversation comments
    comments_cmd = f'gh api repos/{owner_repo}/issues/{pr_number}/comments --jq "length"'
    try:
        result = subprocess.run(comments_cmd, shell=True, capture_output=True, text=True, check=True)
        conversation_count = int(result.stdout.strip())
    except:
        conversation_count = 0

    # Get review comments
    review_comments_cmd = f'gh api repos/{owner_repo}/pulls/{pr_number}/comments --jq "length"'
    try:
        result = subprocess.run(review_comments_cmd, shell=True, capture_output=True, text=True, check=True)
        review_count = int(result.stdout.strip())
    except:
        review_count = 0

    return conversation_count + review_count


def get_commit_info(owner_repo, commit_sha):
    """Get the author and date of a commit."""
    command = f'gh api repos/{owner_repo}/commits/{commit_sha} --jq "{{author: .commit.author.name, date: .commit.author.date}}"'
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout.strip())
        return data.get('author', 'Unknown'), data.get('date', '')
    except:
        return "Unknown", ""


def format_date(iso_date):
    """Format ISO date to DD-Mmm-YY format."""
    if not iso_date:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
        return dt.strftime('%d-%b-%y')
    except:
        return "Unknown"


def generate_markdown(repo_name, start_date, end_date, pr_data):
    """Generate markdown table with PR metrics."""
    # Create filename with YYYYMMDD format
    start_date_formatted = start_date.replace('-', '')
    end_date_formatted = end_date.replace('-', '')
    filename = f"Github-{repo_name}-{start_date_formatted}-{end_date_formatted}.md"

    # Build markdown content
    lines = [
        f"# GitHub PR Metrics - {repo_name} ({start_date} to {end_date})",
        "",
        "| Repo Name | Commit ID | Commit Author | Commit Date | Merged By | Comment Count | PR ID | PR Creation Date | PR Merged Date | Jira ID/First 3 Words |",
        "|-----------|-----------|---------------|-------------|-----------|---------------|-------|------------------|----------------|----------------------|"
    ]

    for pr in pr_data:
        lines.append(pr)

    lines.append("")  # Empty line at end

    # Write to file
    with open(filename, 'w') as f:
        f.write('\n'.join(lines))

    return filename


def validate_date(date_str):
    """Validate date string in YYYY-MM-DD format."""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def main():
    """Main function to orchestrate the PR metrics extraction."""
    if len(sys.argv) != 4:
        print("Usage: python github_pr_metrics.py <github_repo_url> <start_date> <end_date>")
        print("Example: python github_pr_metrics.py https://github.com/owner/repo 2025-01-01 2025-01-31")
        sys.exit(1)

    repo_url = sys.argv[1]
    start_date = sys.argv[2]
    end_date = sys.argv[3]

    # Validate date formats
    if not validate_date(start_date):
        print("Error: Start date must be in YYYY-MM-DD format", file=sys.stderr)
        sys.exit(1)
    if not validate_date(end_date):
        print("Error: End date must be in YYYY-MM-DD format", file=sys.stderr)
        sys.exit(1)

    # Extract repo information
    repo_name = extract_repo_name(repo_url)
    owner_repo = extract_owner_repo(repo_url)

    print(f"Fetching PRs for {owner_repo} merged between {start_date} and {end_date}...")

    # Get merged PRs
    prs = get_merged_prs(owner_repo, start_date, end_date)

    if not prs:
        print(f"No PRs found merged to main between {start_date} and {end_date}")
        sys.exit(0)

    print(f"Found {len(prs)} PRs. Processing...")

    # Sort PRs by merged date descending (most recent first)
    prs.sort(key=lambda x: x.get('mergedAt', ''), reverse=True)

    # Process each PR
    table_rows = []
    for pr in prs:
        pr_number = pr['number']
        title = pr['title']
        merged_by = pr['mergedBy']['login'] if pr.get('mergedBy') else "Unknown"
        merge_commit = pr['mergeCommit']['oid'] if pr.get('mergeCommit') else "Unknown"
        created_at = pr.get('createdAt', '')
        merged_at = pr.get('mergedAt', '')

        # Get commit author and date
        if merge_commit != "Unknown":
            commit_author, commit_date = get_commit_info(owner_repo, merge_commit)
        else:
            commit_author = "Unknown"
            commit_date = ""

        # Get comment count
        comment_count = get_pr_comment_count(owner_repo, pr_number)

        # Extract Jira ID or first 3 words
        jira_or_words = extract_jira_id_or_words(title)

        # Format dates
        creation_date = format_date(created_at)
        merged_date = format_date(merged_at)
        commit_date_formatted = format_date(commit_date)

        # Format table row
        row = f"| {repo_name} | {merge_commit[:7]} | {commit_author} | {commit_date_formatted} | {merged_by} | {comment_count} | #{pr_number} | {creation_date} | {merged_date} | {jira_or_words} |"
        table_rows.append(row)

    # Generate markdown file
    output_file = generate_markdown(repo_name, start_date, end_date, table_rows)

    print(f"Successfully generated {output_file}")


if __name__ == "__main__":
    main()
