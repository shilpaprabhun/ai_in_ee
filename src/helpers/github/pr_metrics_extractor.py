#!/usr/bin/env python3
"""
GitHub Pull Request Metrics Extractor

This script extracts comprehensive PR metrics from GitHub repositories for a
specified date range. It analyzes PRs to provide insights about team behavior,
including review cycles, merge times, collaboration patterns, and quality metrics.
"""

import subprocess
import json
import sys
import re
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import defaultdict


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
        if '/' in repo_url and not repo_url.startswith('http'):
            return repo_url.split('/')[-1]
        return 'unknown'


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


def parse_datetime(iso_date: str) -> Optional[datetime]:
    """
    Parse ISO date string to datetime object.

    Args:
        iso_date: ISO 8601 date string

    Returns:
        Datetime object or None if invalid
    """
    if not iso_date:
        return None
    try:
        return datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration string (e.g., "2h 30m", "45m", "30s")
    """
    if seconds is None or seconds < 0:
        return '-'
    
    if seconds >= 86400:  # 1 day
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        return f"{days}d {hours}h"
    elif seconds >= 3600:  # 1 hour
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"
    elif seconds >= 60:  # 1 minute
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        return f"{int(seconds)}s"


def sanitize_table_cell(text: str) -> str:
    """
    Sanitize text for use in markdown table cells.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized text with newlines replaced by spaces and pipes removed
    """
    if not text:
        return '-'
    text = str(text)
    text = text.replace('\n', ' ').replace('\r', ' ')
    text = text.replace('|', '')
    text = ' '.join(text.split())
    return text.strip() or '-'


def extract_jira_id(title: str) -> str:
    """
    Extract Jira ID from beginning of title.

    Args:
        title: PR title

    Returns:
        Jira ID if found, otherwise empty string
    """
    if not title:
        return ''
    jira_match = re.match(r'^([a-zA-Z]+-\d+)', title.strip())
    if jira_match:
        return jira_match.group(1)
    return ''


def get_prs(owner_repo: str, start_date: str, end_date: str, state: str = 'all') -> List[Dict]:
    """
    Fetch PRs from a repository in the specified date range.

    Args:
        owner_repo: Repository in format "owner/repo"
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        state: PR state (open, closed, all)

    Returns:
        List of PR objects
    """
    # GitHub API supports date filtering with query params
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    
    # Fetch PRs - always use API state filter, fetch all if state is 'all'
    if state == 'all':
        # Fetch both open and closed PRs separately, then combine
        command_open = f'gh api repos/{owner_repo}/pulls?state=open --paginate'
        command_closed = f'gh api repos/{owner_repo}/pulls?state=closed --paginate'
        
        prs_open = run_gh_command(command_open, allow_empty=True) or []
        prs_closed = run_gh_command(command_closed, allow_empty=True) or []
        prs = prs_open + prs_closed
    else:
        # Use state filter in API call
        command = f'gh api repos/{owner_repo}/pulls?state={state} --paginate'
        prs = run_gh_command(command, allow_empty=True) or []
    
    # Filter by date range (created_at)
    filtered = []
    for pr in prs:
        created_at = pr.get('created_at', '')
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                if start_dt.date() <= created_dt.date() <= end_dt.date():
                    filtered.append(pr)
            except (ValueError, AttributeError):
                continue
    
    return filtered


def get_pr_reviews(owner_repo: str, pr_number: int) -> List[Dict]:
    """
    Get all reviews for a PR.

    Args:
        owner_repo: Repository in format "owner/repo"
        pr_number: PR number

    Returns:
        List of review objects
    """
    command = f'gh api repos/{owner_repo}/pulls/{pr_number}/reviews --paginate'
    return run_gh_command(command, allow_empty=True) or []


def get_pr_comments(owner_repo: str, pr_number: int) -> List[Dict]:
    """
    Get all review comments for a PR.

    Args:
        owner_repo: Repository in format "owner/repo"
        pr_number: PR number

    Returns:
        List of review comment objects
    """
    command = f'gh api repos/{owner_repo}/pulls/{pr_number}/comments --paginate'
    return run_gh_command(command, allow_empty=True) or []


def get_pr_commits(owner_repo: str, pr_number: int) -> List[Dict]:
    """
    Get all commits for a PR.

    Args:
        owner_repo: Repository in format "owner/repo"
        pr_number: PR number

    Returns:
        List of commit objects
    """
    command = f'gh api repos/{owner_repo}/pulls/{pr_number}/commits --paginate'
    return run_gh_command(command, allow_empty=True) or []


def get_pr_detail(owner_repo: str, pr_number: int) -> Dict:
    """
    Get detailed PR information including additions/deletions.

    Args:
        owner_repo: Repository in format "owner/repo"
        pr_number: PR number

    Returns:
        PR detail object with additions/deletions
    """
    command = f'gh api repos/{owner_repo}/pulls/{pr_number}'
    return run_gh_command(command, allow_empty=True) or {}


def get_pr_files(owner_repo: str, pr_number: int) -> List[Dict]:
    """
    Get all files changed in a PR.

    Args:
        owner_repo: Repository in format "owner/repo"
        pr_number: PR number

    Returns:
        List of file change objects
    """
    command = f'gh api repos/{owner_repo}/pulls/{pr_number}/files --paginate'
    return run_gh_command(command, allow_empty=True) or []


def get_pr_issue_comments(owner_repo: str, pr_number: int) -> List[Dict]:
    """
    Get all issue comments (general PR comments) for a PR.

    Args:
        owner_repo: Repository in format "owner/repo"
        pr_number: PR number

    Returns:
        List of issue comment objects
    """
    command = f'gh api repos/{owner_repo}/issues/{pr_number}/comments --paginate'
    return run_gh_command(command, allow_empty=True) or []


def get_check_runs(owner_repo: str, ref: str) -> List[Dict]:
    """
    Get check runs (CI/CD status) for a commit ref.

    Args:
        owner_repo: Repository in format "owner/repo"
        ref: Commit SHA or ref

    Returns:
        List of check run objects
    """
    command = f'gh api repos/{owner_repo}/commits/{ref}/check-runs --paginate'
    result = run_gh_command(command, allow_empty=True)
    
    if isinstance(result, dict) and 'check_runs' in result:
        return result['check_runs']
    elif isinstance(result, list):
        return result
    
    return []


def calculate_time_to_first_review(
    pr_created_at: datetime,
    reviews: List[Dict]
) -> Optional[float]:
    """
    Calculate time from PR creation to first review.

    Args:
        pr_created_at: PR creation datetime
        reviews: List of review objects

    Returns:
        Time in seconds, or None if no reviews
    """
    if not reviews:
        return None
    
    # Filter out reviews by the PR author
    pr_author = None  # Will be set from PR data
    meaningful_reviews = [
        r for r in reviews
        if r.get('state') not in ['PENDING', 'DISMISSED']
    ]
    
    if not meaningful_reviews:
        return None
    
    # Sort by submitted_at
    sorted_reviews = sorted(
        meaningful_reviews,
        key=lambda x: x.get('submitted_at', '')
    )
    
    first_review = sorted_reviews[0]
    first_review_at = parse_datetime(first_review.get('submitted_at', ''))
    
    if not first_review_at:
        return None
    
    delta = (first_review_at - pr_created_at).total_seconds()
    return max(0, delta)


def calculate_review_cycles(reviews: List[Dict]) -> int:
    """
    Calculate number of review cycles (approvals -> changes -> re-review).

    Args:
        reviews: List of review objects

    Returns:
        Number of review cycles
    """
    if not reviews:
        return 0
    
    # Filter meaningful reviews
    meaningful_reviews = [
        r for r in reviews
        if r.get('state') in ['APPROVED', 'CHANGES_REQUESTED', 'COMMENTED']
    ]
    
    if not meaningful_reviews:
        return 0
    
    # Count distinct review sessions (group by reviewer and state transitions)
    cycles = 0
    last_state_by_reviewer = {}
    
    for review in sorted(meaningful_reviews, key=lambda x: x.get('submitted_at', '')):
        reviewer = review.get('user', {}).get('login', '')
        state = review.get('state', '')
        
        if reviewer in last_state_by_reviewer:
            last_state = last_state_by_reviewer[reviewer]
            # New cycle if state changed from APPROVED to CHANGES_REQUESTED
            # or if reviewer came back after changes
            if (last_state == 'APPROVED' and state == 'CHANGES_REQUESTED') or \
               (last_state == 'CHANGES_REQUESTED' and state in ['APPROVED', 'COMMENTED']):
                cycles += 1
        else:
            # First review from this reviewer
            if state in ['APPROVED', 'CHANGES_REQUESTED']:
                cycles += 1
        
        last_state_by_reviewer[reviewer] = state
    
    return max(1, cycles)  # At least 1 cycle if there are reviews


def calculate_pr_metrics(owner_repo: str, pr: Dict) -> Dict:
    """
    Calculate comprehensive metrics for a single PR.

    Args:
        owner_repo: Repository in format "owner/repo"
        pr: PR object from GitHub API

    Returns:
        Dictionary with PR metrics
    """
    pr_number = pr.get('number')
    pr_created_at = parse_datetime(pr.get('created_at', ''))
    pr_merged_at = parse_datetime(pr.get('merged_at', ''))
    pr_closed_at = parse_datetime(pr.get('closed_at', ''))
    
    # Basic PR info
    author = pr.get('user', {}).get('login', 'unknown')
    state = pr.get('state', 'unknown')
    is_draft = pr.get('draft', False)
    is_merged = bool(pr_merged_at)
    
    # Fetch additional data
    reviews = get_pr_reviews(owner_repo, pr_number)
    comments = get_pr_comments(owner_repo, pr_number)
    issue_comments = get_pr_issue_comments(owner_repo, pr_number)
    commits = get_pr_commits(owner_repo, pr_number)
    files = get_pr_files(owner_repo, pr_number)
    
    # Fetch PR detail to get accurate additions/deletions
    pr_detail = get_pr_detail(owner_repo, pr_number)
    
    # Calculate time metrics
    time_to_merge = None
    if pr_merged_at and pr_created_at:
        time_to_merge = (pr_merged_at - pr_created_at).total_seconds()
    
    time_to_first_review = None
    if pr_created_at:
        time_to_first_review = calculate_time_to_first_review(pr_created_at, reviews)
    
    # Review metrics
    reviewers = set()
    approvers = set()
    change_requests = 0
    review_comments_count = len(comments)
    
    for review in reviews:
        reviewer = review.get('user', {}).get('login', '')
        review_state = review.get('state', '')
        
        if reviewer and review_state != 'PENDING':
            reviewers.add(reviewer)
        
        if review_state == 'APPROVED':
            approvers.add(reviewer)
        elif review_state == 'CHANGES_REQUESTED':
            change_requests += 1
    
    approvals_count = len(approvers)
    review_cycles = calculate_review_cycles(reviews)
    
    # Code metrics - use PR detail endpoint for accurate additions/deletions
    # PR detail endpoint includes these fields reliably
    additions = pr_detail.get('additions', 0) or 0
    deletions = pr_detail.get('deletions', 0) or 0
    
    # Fallback to summing from files if PR detail doesn't have them
    if additions == 0 and deletions == 0 and files:
        additions = sum(file.get('additions', 0) for file in files)
        deletions = sum(file.get('deletions', 0) for file in files)
    
    total_changes = additions + deletions
    files_changed = len(files)
    
    # Extract file names
    file_names = [file.get('filename', '') for file in files if file.get('filename')]
    files_list = ', '.join(file_names) if file_names else '-'
    
    commits_count = len(commits)
    
    # CI/CD metrics
    head_sha = pr.get('head', {}).get('sha', '')
    check_runs = get_check_runs(owner_repo, head_sha) if head_sha else []
    
    ci_failures = 0
    ci_total = len(check_runs)
    for check_run in check_runs:
        conclusion = check_run.get('conclusion', '')
        if conclusion in ['failure', 'cancelled', 'timed_out']:
            ci_failures += 1
    
    # Communication metrics
    total_comments = review_comments_count + len(issue_comments)
    
    # Collect all comment texts (excluding bot comments)
    all_comment_texts = []
    
    for comment in comments:
        comment_body = comment.get('body', '').strip()
        comment_author = comment.get('user', {}).get('login', 'unknown')
        # Filter out bot comments (usernames containing [bot])
        if comment_body and '[bot]' not in comment_author:
            comment_text = f"{comment_author}: {comment_body}"
            all_comment_texts.append(comment_text)
    
    for comment in issue_comments:
        comment_body = comment.get('body', '').strip()
        comment_author = comment.get('user', {}).get('login', 'unknown')
        # Filter out bot comments (usernames containing [bot])
        if comment_body and '[bot]' not in comment_author:
            comment_text = f"{comment_author}: {comment_body}"
            all_comment_texts.append(comment_text)
    
    # Format comments as a numbered list
    if all_comment_texts:
        numbered_comments = []
        for idx, comment_text in enumerate(all_comment_texts, start=1):
            numbered_comments.append(f"{idx}. {comment_text}")
        comments_list = ' | '.join(numbered_comments)
        # Limit total length to avoid huge cells
        if len(comments_list) > 500:
            comments_list = comments_list[:497] + '...'
    else:
        comments_list = '-'
    
    # Labels
    labels = [label.get('name', '') for label in pr.get('labels', [])]
    
    # Extract Jira ID
    title = pr.get('title', '')
    jira_id = extract_jira_id(title)
    
    # Day of week and hour
    created_day = None
    created_hour = None
    merged_day = None
    merged_hour = None
    
    if pr_created_at:
        created_day = pr_created_at.strftime('%A')
        created_hour = pr_created_at.hour
    
    if pr_merged_at:
        merged_day = pr_merged_at.strftime('%A')
        merged_hour = pr_merged_at.hour
    
    return {
        'pr_number': pr_number,
        'title': title,
        'author': author,
        'state': state,
        'is_draft': is_draft,
        'is_merged': is_merged,
        'created_at': format_datetime(pr.get('created_at', '')),
        'created_dt': pr_created_at,
        'merged_at': format_datetime(pr.get('merged_at', '')),
        'merged_dt': pr_merged_at,
        'closed_at': format_datetime(pr.get('closed_at', '')),
        'time_to_merge_seconds': time_to_merge,
        'time_to_merge': format_duration(time_to_merge) if time_to_merge else '-',
        'time_to_first_review_seconds': time_to_first_review,
        'time_to_first_review': format_duration(time_to_first_review) if time_to_first_review else '-',
        'additions': additions,
        'deletions': deletions,
        'total_changes': total_changes,
        'files_changed': files_changed,
        'files_list': files_list,
        'commits_count': commits_count,
        'reviewers_count': len(reviewers),
        'reviewers': ', '.join(sorted(reviewers)) if reviewers else '-',
        'approvals': approvals_count,
        'approvers': ', '.join(sorted(approvers)) if approvers else '-',
        'change_requests': change_requests,
        'review_cycles': review_cycles,
        'review_comments': review_comments_count,
        'total_comments': total_comments,
        'comments_list': comments_list,
        'ci_total': ci_total,
        'ci_failures': ci_failures,
        'ci_success_rate': round((ci_total - ci_failures) / ci_total * 100, 1) if ci_total > 0 else 100.0,
        'labels': ', '.join(labels) if labels else '-',
        'jira_id': jira_id,
        'created_day': created_day or '-',
        'created_hour': created_hour if created_hour is not None else '-',
        'merged_day': merged_day or '-',
        'merged_hour': merged_hour if merged_hour is not None else '-',
    }


def calculate_summary_statistics(pr_metrics: List[Dict]) -> Dict:
    """
    Calculate summary statistics from PR metrics.

    Args:
        pr_metrics: List of PR metric dictionaries

    Returns:
        Dictionary with summary statistics
    """
    if not pr_metrics:
        return {}
    
    merged_prs = [m for m in pr_metrics if m.get('is_merged')]
    closed_prs = [m for m in pr_metrics if m.get('state') == 'closed' and not m.get('is_merged')]
    
    # Time metrics (only for merged PRs)
    merge_times = [
        m.get('time_to_merge_seconds')
        for m in merged_prs
        if m.get('time_to_merge_seconds') is not None
    ]
    
    first_review_times = [
        m.get('time_to_first_review_seconds')
        for m in pr_metrics
        if m.get('time_to_first_review_seconds') is not None
    ]
    
    # Size metrics
    total_changes = [m.get('total_changes', 0) for m in pr_metrics]
    files_changed = [m.get('files_changed', 0) for m in pr_metrics]
    
    # Review metrics
    reviewers_counts = [m.get('reviewers_count', 0) for m in pr_metrics]
    review_cycles = [m.get('review_cycles', 0) for m in pr_metrics]
    
    def percentile(values: List[float], p: float) -> Optional[float]:
        """Calculate percentile."""
        if not values:
            return None
        sorted_vals = sorted(values)
        index = int(len(sorted_vals) * p / 100)
        return sorted_vals[min(index, len(sorted_vals) - 1)]
    
    return {
        'total_prs': len(pr_metrics),
        'merged_prs': len(merged_prs),
        'closed_prs': len(closed_prs),
        'open_prs': len([m for m in pr_metrics if m.get('state') == 'open']),
        'draft_prs': len([m for m in pr_metrics if m.get('is_draft')]),
        'merge_rate': round(len(merged_prs) / len(pr_metrics) * 100, 1) if pr_metrics else 0,
        'avg_time_to_merge': format_duration(sum(merge_times) / len(merge_times)) if merge_times else '-',
        'median_time_to_merge': format_duration(percentile(merge_times, 50)) if merge_times else '-',
        'p75_time_to_merge': format_duration(percentile(merge_times, 75)) if merge_times else '-',
        'p95_time_to_merge': format_duration(percentile(merge_times, 95)) if merge_times else '-',
        'avg_time_to_first_review': format_duration(sum(first_review_times) / len(first_review_times)) if first_review_times else '-',
        'median_time_to_first_review': format_duration(percentile(first_review_times, 50)) if first_review_times else '-',
        'avg_pr_size': int(sum(total_changes) / len(total_changes)) if total_changes else 0,
        'median_pr_size': int(percentile(total_changes, 50)) if total_changes else 0,
        'avg_files_changed': round(sum(files_changed) / len(files_changed), 1) if files_changed else 0,
        'avg_reviewers': round(sum(reviewers_counts) / len(reviewers_counts), 1) if reviewers_counts else 0,
        'avg_review_cycles': round(sum(review_cycles) / len(review_cycles), 1) if review_cycles else 0,
    }


def generate_markdown_report(
    repo_urls: List[str],
    start_date: str,
    end_date: str,
    pr_metrics: List[Dict],
    summary_stats: Optional[Dict] = None
) -> Tuple[str, str]:
    """
    Generate markdown report with PR metrics.

    Args:
        repo_urls: List of repository URLs processed
        start_date: Start date
        end_date: End date
        pr_metrics: List of PR metric dictionaries
        summary_stats: Summary statistics dictionary (optional, not used)

    Returns:
        Tuple of (filename, content)
    """
    start_date_formatted = start_date.replace('-', '')
    end_date_formatted = end_date.replace('-', '')
    
    repo_names = [extract_repo_name_from_url(url) for url in repo_urls]
    repo_name = repo_names[0] if repo_names else 'unknown'
    filename = f"Github-PR-Metrics-{repo_name}-{start_date_formatted}-{end_date_formatted}.md"
    
    repos_list = ', '.join(repo_names)
    
    lines = [
        f"# GitHub Pull Request Metrics ({start_date} to {end_date})",
        "",
        f"**Repositories:** {repos_list}",
        "",
        "## Definitions",
        "",
        "**Time to Merge**: The duration from when a PR was created until it was merged. This includes all time spent in review, waiting for CI, and any iterations.",
        "",
        "**Time to First Review**: The duration from when a PR was created until the first review comment or approval was submitted by a reviewer (excluding the PR author).",
        "",
        "**CI Status**: Shows the total number of CI check runs and how many failed. For example, '8 (0 failed)' means 8 CI checks ran and 0 failed.",
        "",
        "## PR Metrics",
        "",
    ]
    
    # Detailed PR table
    header = [
        "PR #",
        "Title",
        "Author",
        "State",
        "Created",
        "Merged",
        "Time to Merge",
        "Time to First Review",
        "Size (+/-)",
        "Files",
        "Commits",
        "Reviewers",
        "Approvals",
        "Change Requests",
        "Review Cycles",
        "Review Comments",
        "Total Comments",
        "Comments",
        "CI Status",
        "Jira ID",
        "Labels"
    ]
    
    lines.append(f"| {' | '.join(header)} |")
    lines.append(f"|{'|'.join(['---'] * len(header))}|")
    
    # Sort by created date descending
    sorted_prs = sorted(
        pr_metrics,
        key=lambda x: x.get('created_dt') or datetime.min,
        reverse=True
    )
    
    for pr in sorted_prs:
        size_str = f"+{pr.get('additions', 0)}/-{pr.get('deletions', 0)}"
        ci_status = f"{pr.get('ci_total', 0)} ({pr.get('ci_failures', 0)} failed)"
        
        row = [
            sanitize_table_cell(f"#{pr.get('pr_number', '')}"),
            sanitize_table_cell(pr.get('title', '-')),
            sanitize_table_cell(pr.get('author', '-')),
            sanitize_table_cell(pr.get('state', '-')),
            sanitize_table_cell(pr.get('created_at', '-')),
            sanitize_table_cell(pr.get('merged_at', '-')),
            sanitize_table_cell(pr.get('time_to_merge', '-')),
            sanitize_table_cell(pr.get('time_to_first_review', '-')),
            sanitize_table_cell(size_str),
            sanitize_table_cell(pr.get('files_list', '-')),
            sanitize_table_cell(pr.get('commits_count', 0)),
            sanitize_table_cell(pr.get('reviewers', '-')),
            sanitize_table_cell(pr.get('approvers', '-')),
            sanitize_table_cell(pr.get('change_requests', 0)),
            sanitize_table_cell(pr.get('review_cycles', 0)),
            sanitize_table_cell(pr.get('review_comments', 0)),
            sanitize_table_cell(pr.get('total_comments', 0)),
            sanitize_table_cell(pr.get('comments_list', '-')),
            sanitize_table_cell(ci_status),
            sanitize_table_cell(pr.get('jira_id', '-')),
            sanitize_table_cell(pr.get('labels', '-'))
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
        description='Extract GitHub PR metrics for specified repositories'
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
        '--state',
        choices=['all', 'open', 'closed'],
        default='all',
        help='PR state filter (default: all)'
    )
    parser.add_argument(
        '--output',
        help='Output file path (default: auto-generated)'
    )
    
    return parser.parse_args()


def main():
    """Main function to orchestrate the PR metrics extraction."""
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
    print(f"Date range: {args.start_date} to {args.end_date}")
    print(f"PR state filter: {args.state}\n")
    
    all_pr_metrics = []
    
    # Process each repository
    for repo_url in args.repo:
        owner_repo = extract_owner_repo(repo_url)
        repo_name = extract_repo_name_from_url(repo_url)
        print(f"\nProcessing {repo_name} ({owner_repo})...")
        
        # Get PRs
        prs = get_prs(owner_repo, args.start_date, args.end_date, args.state)
        
        if not prs:
            print(f"  No PRs found in date range")
            continue
        
        print(f"  Found {len(prs)} PRs")
        
        # Process each PR
        for i, pr in enumerate(prs, 1):
            print(f"  Processing PR {i}/{len(prs)}: #{pr.get('number')} - {pr.get('title', '')[:50]}")
            try:
                pr_metric = calculate_pr_metrics(owner_repo, pr)
                all_pr_metrics.append(pr_metric)
            except Exception as e:
                print(f"    Error processing PR #{pr.get('number')}: {e}", file=sys.stderr)
                continue
    
    if not all_pr_metrics:
        print("\nNo PRs found for the specified criteria")
        sys.exit(0)
    
    print(f"\nTotal PRs processed: {len(all_pr_metrics)}")
    
    # Generate markdown (no summary statistics)
    filename, content = generate_markdown_report(
        args.repo,
        args.start_date,
        args.end_date,
        all_pr_metrics
    )
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(filename)
    
    # Create output directory if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to file
    with open(output_path, 'w') as f:
        f.write(content)
    
    print(f"\nSuccessfully generated {output_path}")


if __name__ == "__main__":
    main()