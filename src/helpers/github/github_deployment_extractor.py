#!/usr/bin/env python3
"""
GitHub Deployment Data Extractor

This script extracts deployment data from GitHub repositories for a specified
date range. It takes a list of repository URLs and outputs a markdown table with
deployment information including associated PRs and Jira IDs.
"""

import subprocess
import json
import sys
import re
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple


def run_gh_command(command: str, allow_empty: bool = False) -> Optional[List[Dict]]:
    """
    Execute a gh CLI command and return parsed JSON output.

    Args:
        command: The gh CLI command to execute
        allow_empty: If True, return empty list on error instead of exiting

    Returns:
        Parsed JSON output or None if allow_empty and command failed
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        if not result.stdout.strip():
            return [] if allow_empty else None
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        if allow_empty:
            return []
        print(f"Error executing command: {command}", file=sys.stderr)
        print(f"Error: {e.stderr}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from command: {command}", file=sys.stderr)
        print(f"Error: {e}", file=sys.stderr)
        if allow_empty:
            return []
        sys.exit(1)


def extract_owner_repo(repo_url: str) -> str:
    """
    Extract owner/repo from GitHub URL.

    Args:
        repo_url: GitHub repository URL

    Returns:
        String in format "owner/repo"
    """
    match = re.search(r'github\.com/([^/]+)/([^/\.]+)', repo_url)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    else:
        print(f"Invalid GitHub URL: {repo_url}", file=sys.stderr)
        sys.exit(1)


def extract_jira_id(title: str) -> str:
    """
    Extract Jira ID from beginning of title or first 3 words.

    Args:
        title: PR or commit title

    Returns:
        Jira ID if found, otherwise first 3 words
    """
    # Case-insensitive match for XXX-YYYY at the beginning
    jira_match = re.match(r'^([a-zA-Z]+-\d+)', title.strip())
    if jira_match:
        return jira_match.group(1)

    # If no Jira ID, return first 3 words
    words = title.strip().split()
    return ' '.join(words[:3])


def format_datetime(iso_date: str) -> str:
    """
    Format ISO date to YYYY-MM-DD HH:MM:SS format.

    Args:
        iso_date: ISO 8601 date string

    Returns:
        Formatted date string or empty string if invalid
    """
    if not iso_date:
        return ''
    try:
        dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, AttributeError):
        return ''


def extract_first_n_words(text: str, n: int) -> str:
    """
    Extract first N words from text.

    Args:
        text: Text to extract from
        n: Number of words to extract

    Returns:
        First N words or full text if shorter
    """
    if not text:
        return ''
    words = text.strip().split()
    if len(words) <= n:
        return text.strip()
    return ' '.join(words[:n])


def sanitize_table_cell(text: str) -> str:
    """
    Sanitize text for use in markdown table cells.
    Removes newlines, pipes, and other characters that break tables.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized text with newlines replaced by spaces and pipes removed
    """
    if not text:
        return '-'
    # Replace newlines and carriage returns with spaces
    text = text.replace('\n', ' ').replace('\r', ' ')
    # Remove pipe characters (they break markdown tables)
    text = text.replace('|', '')
    # Collapse multiple spaces into single space
    text = ' '.join(text.split())
    return text.strip() or '-'


def get_deployments(owner_repo: str, start_date: str, end_date: str) -> List[Dict]:
    """
    Fetch deployments from a repository in the specified date range.

    Args:
        owner_repo: Repository in format "owner/repo"
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        List of deployment objects
    """
    # GitHub deployments API doesn't have direct date filtering,
    # so we'll fetch all and filter by created_at
    command = f'gh api repos/{owner_repo}/deployments --paginate'
    deployments = run_gh_command(command, allow_empty=True) or []
    
    # Filter by date range
    filtered = []
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    
    for deployment in deployments:
        created_at = deployment.get('created_at', '')
        if created_at:
            try:
                # GitHub API returns ISO 8601 format
                created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                # Check if within date range (inclusive)
                if start_dt.date() <= created_dt.date() <= end_dt.date():
                    filtered.append(deployment)
            except (ValueError, AttributeError):
                # Skip deployments with invalid dates
                continue
    
    return filtered


def get_deployment_statuses(owner_repo: str, deployment_id: int) -> List[Dict]:
    """
    Get all statuses for a deployment.

    Args:
        owner_repo: Repository in format "owner/repo"
        deployment_id: Deployment ID

    Returns:
        List of deployment status objects
    """
    command = f'gh api repos/{owner_repo}/deployments/{deployment_id}/statuses --paginate'
    return run_gh_command(command, allow_empty=True) or []


def get_deployment_artifact(owner_repo: str, environment: str, ref: str) -> Optional[Dict]:
    """
    Get deployment artifact/environment data from GitHub API.

    Args:
        owner_repo: Repository in format "owner/repo"
        environment: Environment name
        ref: Deployment ref (branch/tag)

    Returns:
        Deployment environment data with timing info or None
    """
    if not environment or not ref:
        return None
    
    command = f'gh api repos/{owner_repo}/deployments/environments/{environment} --jq ".latest_deployment"'
    return run_gh_command(command, allow_empty=True)


def get_latest_status(deployment_statuses: List[Dict]) -> Optional[Dict]:
    """
    Get the latest deployment status (most recent by updated_at).

    Args:
        deployment_statuses: List of deployment status objects

    Returns:
        Latest status object or None
    """
    if not deployment_statuses:
        return None
    
    # Sort by updated_at descending
    sorted_statuses = sorted(
        deployment_statuses,
        key=lambda x: x.get('updated_at', ''),
        reverse=True
    )
    return sorted_statuses[0]


def get_commit_info(owner_repo: str, commit_sha: str) -> Optional[Dict]:
    """
    Get commit information including creation date.

    Args:
        owner_repo: Repository in format "owner/repo"
        commit_sha: Commit SHA

    Returns:
        Commit object with date info or None if not found
    """
    if not commit_sha:
        return None
    
    command = f'gh api repos/{owner_repo}/commits/{commit_sha}'
    commit_data = run_gh_command(command, allow_empty=True)
    return commit_data


def find_pr_for_commit(owner_repo: str, commit_sha: str) -> Optional[Dict]:
    """
    Find the PR associated with a commit SHA.

    Args:
        owner_repo: Repository in format "owner/repo"
        commit_sha: Commit SHA

    Returns:
        PR object with number and title, or None if not found
    """
    # Use GitHub API to find PRs associated with commit
    # This endpoint returns PR objects that may include basic info
    command = f'gh api repos/{owner_repo}/commits/{commit_sha}/pulls'
    prs = run_gh_command(command, allow_empty=True) or []
    
    if not prs:
        return None
    
    # Get the first PR (usually there's only one)
    pr = prs[0]
    
    # The response might already include title, but fetch full details to be sure
    pr_number = pr.get('number')
    if not pr_number:
        return None
    
    # Fetch full PR details to ensure we have title and created_at
    pr_command = f'gh api repos/{owner_repo}/pulls/{pr_number}'
    full_pr = run_gh_command(pr_command, allow_empty=True)
    
    if full_pr:
        return full_pr
    
    # Fallback: return basic PR if full fetch fails
    if pr.get('title'):
        return pr
    
    return None


def extract_repo_name_from_url(repo_url: str) -> str:
    """
    Extract repository name from GitHub URL.

    Args:
        repo_url: GitHub repository URL

    Returns:
        Repository name
    """
    match = re.search(r'github\.com/[^/]+/([^/\.]+)', repo_url)
    if match:
        return match.group(1)
    else:
        # If already in owner/repo format, extract just repo name
        if '/' in repo_url and not repo_url.startswith('http'):
            return repo_url.split('/')[-1]
        return 'unknown'


def process_deployment(
    owner_repo: str,
    repo_name: str,
    deployment: Dict
) -> Optional[Dict]:
    """
    Process a single deployment and extract all required information.

    Args:
        owner_repo: Repository in format "owner/repo"
        repo_name: Repository name (extracted from URL)
        deployment: Deployment object from GitHub API

    Returns:
        Dictionary with deployment data or None if processing fails
    """
    deployment_id = deployment.get('id')
    environment = deployment.get('environment', 'unknown')
    commit_sha = deployment.get('sha', '')
    created_at = deployment.get('created_at', '')
    ref = deployment.get('ref', '')
    
    # Get deployment creator (user who triggered deployment)
    creator = deployment.get('creator', {})
    creator_login = creator.get('login', '-') if creator else '-'
    
    # Get deployment statuses to find actual start/end times
    statuses = get_deployment_statuses(owner_repo, deployment_id)
    latest_status = get_latest_status(statuses)
    
    status_state = latest_status.get('state', 'pending') if latest_status else 'pending'
    status_description = latest_status.get('description', '') if latest_status else ''
    status_created_at = latest_status.get('created_at', '') if latest_status else ''
    status_updated_at = latest_status.get('updated_at', '') if latest_status else ''
    
    # Fetch deployment artifact data for accurate timing
    artifact_data = get_deployment_artifact(owner_repo, environment, ref)
    artifact_id = '-'
    if artifact_data:
        artifact_id = str(artifact_data.get('id', '-'))
    
    # Use artifact data if available, otherwise fall back to status/deployment times
    deployment_start_time = status_created_at or created_at or ''
    deployment_end_time = status_updated_at or ''
    
    # Format deployment start date/time
    deployment_datetime = format_datetime(deployment_start_time)
    deployment_dt = None
    if deployment_start_time:
        try:
            deployment_dt = datetime.fromisoformat(deployment_start_time.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass

    # Deployment completion time (when status reached final state)
    completion_datetime = ''
    completion_dt = None
    if deployment_end_time and status_state in ['success', 'failure', 'error', 'inactive']:
        completion_datetime = format_datetime(deployment_end_time)
        try:
            completion_dt = datetime.fromisoformat(deployment_end_time.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    # Calculate deployment duration from artifact runtime or status times
    duration_seconds = None
    duration_str = '-'
    
    # Try to get runtime from artifact if available
    if artifact_data and artifact_data.get('runtime_ms'):
        duration_seconds = artifact_data.get('runtime_ms') / 1000  # Convert ms to seconds
    elif deployment_dt and completion_dt:
        duration_seconds = (completion_dt - deployment_dt).total_seconds()
    
    if duration_seconds is not None:
        if duration_seconds >= 3600:
            hours = int(duration_seconds // 3600)
            minutes = int((duration_seconds % 3600) // 60)
            duration_str = f"{hours}h {minutes}m"
        elif duration_seconds >= 60:
            minutes = int(duration_seconds // 60)
            seconds = int(duration_seconds % 60)
            duration_str = f"{minutes}m {seconds}s"
        else:
            duration_str = f"{int(duration_seconds)}s"
    elif status_state == 'pending':
        duration_str = 'In Progress'
    
    # Error summary: if status is error or failure, extract first 20 words
    error_summary = ''
    if status_state in ['error', 'failure']:
        if status_description:
            error_summary = extract_first_n_words(status_description, 20)
        else:
            error_summary = 'Deployment failed'
    
    # Find PR associated with commit
    pr_data = None
    if commit_sha:
        pr_data = find_pr_for_commit(owner_repo, commit_sha)
    
    # Extract PR info
    pr_id = '-'
    title = '-'
    jira_id = '-'
    
    if pr_data:
        pr_id = f"#{pr_data.get('number', '')}"
        title = pr_data.get('title', '-')
        jira_id = extract_jira_id(title)
    
    # Sanitize title and error_summary for markdown table
    title = sanitize_table_cell(title)
    error_summary = sanitize_table_cell(error_summary)
    
    return {
        'repo_name': repo_name,
        'deployment_id': str(deployment_id),
        'deployment_artifact_id': artifact_id,
        'deployment_datetime': deployment_datetime,
        'deployment_dt': deployment_dt,  # For sorting
        'completion_datetime': completion_datetime,
        'duration': duration_str,
        'commit_id': commit_sha[:7] if commit_sha else '-',  # Short commit SHA (first 7 chars)
        'pr_id': pr_id,
        'creator': creator_login,
        'title': title,
        'jira_id': jira_id,
        'environment': environment,
        'status': status_state,
        'error_summary': error_summary
    }


def generate_markdown_table(
    repo_urls: List[str],
    start_date: str,
    end_date: str,
    deployments_data: List[Dict]
) -> Tuple[str, str]:
    """
    Generate markdown table with deployment data.

    Args:
        repo_urls: List of repository URLs processed
        start_date: Start date
        end_date: End date
        deployments_data: List of deployment data dictionaries

    Returns:
        Tuple of (filename, content)
    """
    # Create filename with YYYYMMDD format
    start_date_formatted = start_date.replace('-', '')
    end_date_formatted = end_date.replace('-', '')
    
    filename = f"GithubDeployments-{start_date_formatted}-{end_date_formatted}.md"
    
    # Build markdown content
    header = [
        "Repo Name",
        "Deployment ID",
        "Deployment Artifact ID",
        "Deployment Date/Time",
        "Completion Time",
        "Duration",
        "Commit ID",
        "PR ID",
        "Triggered By",
        "Title",
        "Jira ID",
        "Environment",
        "Deployment Status",
        "Error Summary"
    ]
    
    repos_list = ', '.join([extract_repo_name_from_url(url) for url in repo_urls])
    lines = [
        f"# GitHub Deployments ({start_date} to {end_date})",
        "",
        f"**Repositories:** {repos_list}",
        "",
        f"| {' | '.join(header)} |",
        f"|{'|'.join(['---'] * len(header))}|"
    ]
    
    for deployment in deployments_data:
        row = [
            sanitize_table_cell(deployment.get('repo_name', '-')),
            sanitize_table_cell(deployment.get('deployment_id', '-')),
            sanitize_table_cell(deployment.get('deployment_artifact_id', '-')),
            sanitize_table_cell(deployment.get('deployment_datetime', '-')),
            sanitize_table_cell(deployment.get('completion_datetime', '-')),
            sanitize_table_cell(deployment.get('duration', '-')),
            sanitize_table_cell(deployment.get('commit_id', '-')),
            sanitize_table_cell(deployment.get('pr_id', '-')),
            sanitize_table_cell(deployment.get('creator', '-')),
            sanitize_table_cell(deployment.get('title', '-')),
            sanitize_table_cell(deployment.get('jira_id', '-')),
            sanitize_table_cell(deployment.get('environment', '-')),
            sanitize_table_cell(deployment.get('status', '-')),
            sanitize_table_cell(deployment.get('error_summary', '-'))
        ]
        lines.append(f"| {' | '.join(row)} |")
    
    lines.append("")  # Empty line at end
    
    content = '\n'.join(lines)
    
    return filename, content


def validate_date(date_str: str) -> bool:
    """
    Validate date string in YYYY-MM-DD format.

    Args:
        date_str: Date string to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Extract GitHub deployment data for specified repositories'
    )
    parser.add_argument(
        '--repo',
        action='append',
        required=True,
        help='Repository URL (can be specified multiple times). '
             'Example: --repo https://github.com/owner/repo1 --repo https://github.com/owner/repo2'
    )
    parser.add_argument(
        'start_date',
        help='Start date in YYYY-MM-DD format'
    )
    parser.add_argument(
        'end_date',
        help='End date in YYYY-MM-DD format'
    )
    parser.add_argument(
        '--output',
        help='Output file path (default: auto-generated as Github-Deployments-{repos}-{dates}.md)'
    )
    
    return parser.parse_args()


def main():
    """Main function to orchestrate the deployment extraction."""
    args = parse_arguments()
    
    # Validate date formats
    if not validate_date(args.start_date):
        print("Error: Start date must be in YYYY-MM-DD format", file=sys.stderr)
        sys.exit(1)
    if not validate_date(args.end_date):
        print("Error: End date must be in YYYY-MM-DD format", file=sys.stderr)
        sys.exit(1)
    
    # Validate repo URLs
    if not args.repo:
        print("Error: At least one repository URL must be specified with --repo", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing {len(args.repo)} repository/repositories")
    print(f"Date range: {args.start_date} to {args.end_date}\n")
    
    all_deployments = []
    
    # Process each repository
    for repo_url in args.repo:
        owner_repo = extract_owner_repo(repo_url)
        repo_name = extract_repo_name_from_url(repo_url)
        print(f"\nProcessing {repo_name} ({owner_repo})...")
        
        # Get deployments
        deployments = get_deployments(owner_repo, args.start_date, args.end_date)
        
        if not deployments:
            print(f"  No deployments found in date range")
            continue
        
        print(f"  Found {len(deployments)} deployments")
        
        # Process each deployment
        for deployment in deployments:
            deployment_data = process_deployment(
                owner_repo,
                repo_name,
                deployment
            )
            
            if deployment_data:
                all_deployments.append(deployment_data)
    
    if not all_deployments:
        print("\nNo deployments found for the specified criteria")
        sys.exit(0)
    
    # Sort by repo name (ascending), then by deployment date/time descending (most recent first)
    # Use negative timestamp for descending date order within each repo
    all_deployments.sort(
        key=lambda x: (
            x.get('repo_name', ''),
            -(x.get('deployment_dt') or datetime.min).timestamp() if x.get('deployment_dt') else 0
        )
    )
    
    print(f"\nTotal deployments found: {len(all_deployments)}")
    
    # Generate markdown
    filename, content = generate_markdown_table(
        args.repo,
        args.start_date,
        args.end_date,
        all_deployments
    )
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        # Default output to current directory or data directory
        output_path = Path(filename)
    
    # Create output directory if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to file
    with open(output_path, 'w') as f:
        f.write(content)
    
    print(f"Successfully generated {output_path}")


if __name__ == "__main__":
    main()

