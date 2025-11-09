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


def extract_workflow_run_id_from_statuses(deployment_statuses: List[Dict]) -> Optional[str]:
    """
    Extract the GitHub Actions workflow run ID from deployment statuses.

    Args:
        deployment_statuses: List of deployment status objects

    Returns:
        Workflow run ID as string if found, otherwise None
    """
    if not deployment_statuses:
        return None
    # Check each status for a target_url containing /actions/runs/{run_id}
    for status in deployment_statuses:
        target_url = status.get('target_url') or status.get('log_url') or ''
        match = re.search(r'/actions/runs/(\d+)', target_url)
        if match:
            return match.group(1)
    return None


def get_workflow_run_data(owner_repo: str, run_id: str) -> Optional[Dict]:
    """
    Fetch GitHub Actions workflow run details for timing information.

    Args:
        owner_repo: Repository in format "owner/repo"
        run_id: Workflow run ID

    Returns:
        Workflow run data dict or None
    """
    if not run_id:
        return None
    command = f'gh api repos/{owner_repo}/actions/runs/{run_id}'
    return run_gh_command(command, allow_empty=True)


def get_workflow_run_jobs(owner_repo: str, run_id: str) -> List[Dict]:
    """
    Get all jobs for a workflow run.

    Args:
        owner_repo: Repository in format "owner/repo"
        run_id: Workflow run ID

    Returns:
        List of job objects, empty list if not found or error
    """
    if not run_id:
        return []
    command = f'gh api repos/{owner_repo}/actions/runs/{run_id}/jobs'
    result = run_gh_command(command, allow_empty=True)
    
    if isinstance(result, dict) and 'jobs' in result:
        return result['jobs']
    elif isinstance(result, list):
        return result
    
    return []


def get_job_logs(owner_repo: str, job_id: int) -> Optional[str]:
    """
    Get logs for a specific job as plain text.

    Args:
        owner_repo: Repository in format "owner/repo"
        job_id: Job ID

    Returns:
        Log text as string or None if not available
    """
    if not job_id:
        return None
    
    try:
        command = f'gh api repos/{owner_repo}/actions/jobs/{job_id}/logs'
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout if result.stdout.strip() else None
    except subprocess.CalledProcessError:
        # Logs may not be available or job may not exist
        return None
    except Exception:
        return None


def extract_error_summary_from_logs(log_text: str, max_words: int = 50) -> str:
    """
    Analyze logs to identify root cause and create a concise 50-word summary.

    Args:
        log_text: Full log text from workflow job
        max_words: Target word count for summary (default 50)

    Returns:
        Concise error summary identifying root cause (approximately 50 words)
    """
    if not log_text:
        return ''
    
    lines = log_text.split('\n')
    
    # Analyze last 500 lines for errors (most recent failures)
    recent_lines = lines[-500:] if len(lines) > 500 else lines
    
    # Key patterns to identify root causes
    root_cause_patterns = {
        'cloudformation': r'(?:CloudFormation|CFN|stack).*?(?:failed|error|rollback|UPDATE_FAILED)',
        'lambda': r'(?:Lambda|Function).*?(?:failed|error|timeout|throttled)',
        'test': r'(?:test|spec).*?(?:failed|error|assertion)',
        'build': r'(?:build|compile|npm|yarn|pip).*?(?:failed|error)',
        'deployment': r'(?:deploy|deployment).*?(?:failed|error|timeout)',
        'permission': r'(?:permission|access|unauthorized|forbidden)',
        'timeout': r'(?:timeout|timed out|exceeded)',
        'resource': r'(?:resource.*?not found|does not exist|already exists)',
    }
    
    # Collect key error information
    error_context = {
        'main_error': '',
        'failed_resource': '',
        'error_type': '',
        'error_message': '',
        'exit_code': '',
    }
    
    # Look for main error message (usually near the end)
    for i in range(len(recent_lines) - 1, max(0, len(recent_lines) - 50), -1):
        line = recent_lines[i].strip()
        if not line or len(line) > 300:
            continue
        
        line_lower = line.lower()
        
        # Find exit code
        exit_match = re.search(r'exit code (\d+)', line_lower)
        if exit_match and not error_context['exit_code']:
            error_context['exit_code'] = exit_match.group(1)
        
        # Find main error patterns (prioritize ##[error] lines)
        if '##[error]' in line:
            if not error_context['main_error']:
                # Extract meaningful error message
                error_msg = re.sub(r'##\[error\]', '', line, flags=re.IGNORECASE).strip()
                error_msg = re.sub(r'^\d{4}-\d{2}-\d{2}T.*?Z\s*', '', error_msg)  # Remove timestamps
                if error_msg and len(error_msg) < 200:
                    error_context['main_error'] = error_msg
        elif 'error:' in line_lower and not error_context['main_error']:
            error_msg = re.sub(r'^.*?error:\s*', '', line, flags=re.IGNORECASE).strip()
            error_msg = re.sub(r'^\d{4}-\d{2}-\d{2}T.*?Z\s*', '', error_msg)
            if error_msg and len(error_msg) < 200:
                error_context['main_error'] = error_msg
        
        # Look for CloudFormation stack failures (common pattern)
        if 'UPDATE_ROLLBACK_COMPLETE' in line or 'ROLLBACK_COMPLETE' in line:
            error_context['error_type'] = 'cloudformation'
            # Extract stack name if available
            stack_match = re.search(r'stack[:\s]+([A-Za-z0-9_-]+)', line_lower)
            if stack_match:
                error_context['failed_resource'] = stack_match.group(1)
            # Also try to extract from "Failed to create/update the stack: <name>"
            stack_match2 = re.search(r'(?:create|update)\s+the\s+stack[:\s]+([A-Za-z0-9_-]+)', line_lower)
            if stack_match2:
                error_context['failed_resource'] = stack_match2.group(1)
        
        # Look for UPDATE_FAILED or CREATE_FAILED (CloudFormation)
        if 'UPDATE_FAILED' in line or 'CREATE_FAILED' in line:
            if not error_context['error_type']:
                error_context['error_type'] = 'cloudformation'
            # Extract resource name
            resource_match = re.search(r'UPDATE_FAILED\s+(?:AWS::)?([A-Za-z0-9_-]+)', line)
            if resource_match:
                error_context['failed_resource'] = resource_match.group(1)
            elif not error_context['main_error']:
                # Use the line as main error if we don't have one
                error_context['main_error'] = line
        
        # Identify error type from patterns
        for error_type, pattern in root_cause_patterns.items():
            if re.search(pattern, line, re.IGNORECASE):
                if not error_context['error_type']:
                    error_context['error_type'] = error_type
                # Extract resource name if available
                if not error_context['failed_resource']:
                    resource_match = re.search(r'(?:AWS::|Resource:?|Function:?)\s*([A-Za-z0-9_-]+)', line)
                    if resource_match:
                        error_context['failed_resource'] = resource_match.group(1)
        
        # Look for "Waiter ... failed" messages (CloudFormation waiters)
        if 'waiter' in line_lower and 'failed' in line_lower:
            error_context['error_type'] = 'cloudformation'
            # Extract what the waiter was waiting for
            waiter_match = re.search(r'waiter\s+([A-Za-z0-9_-]+)\s+failed', line_lower)
            if waiter_match:
                error_context['failed_resource'] = waiter_match.group(1)
            if not error_context['main_error']:
                error_context['main_error'] = line
        
        # Extract stack name from "Failed to create/update the stack: <name>" pattern
        if 'failed to' in line_lower and 'stack' in line_lower:
            stack_match = re.search(r'stack[:\s,]+([A-Za-z0-9_-]+)', line_lower)
            if stack_match and not error_context['failed_resource']:
                error_context['failed_resource'] = stack_match.group(1)
                if not error_context['error_type']:
                    error_context['error_type'] = 'cloudformation'
    
    # Build concise summary focusing on root cause
    summary_parts = []
    
    # Determine error category and build summary
    if error_context['error_type'] == 'cloudformation':
        # CloudFormation errors: focus on stack and root cause
        if error_context['failed_resource']:
            summary_parts.append(f"CloudFormation stack update failed for {error_context['failed_resource']}")
        else:
            summary_parts.append("CloudFormation stack update failed")
        
        # Extract root cause from main error
        if error_context['main_error']:
            # Look for key phrases indicating root cause
            error_lower = error_context['main_error'].lower()
            if 'rollback' in error_lower:
                summary_parts.append("due to rollback")
            elif 'waiter' in error_lower:
                summary_parts.append("waiter encountered terminal failure state")
            elif 'resource' in error_lower and 'update' in error_lower:
                summary_parts.append("resource update failed")
            
            # Extract specific error reason if available
            reason_match = re.search(r'(?:because|due to|reason:)\s+([^.!?]+)', error_lower)
            if reason_match:
                reason = reason_match.group(1).strip()
                if len(reason.split()) <= 15:
                    summary_parts.append(f"- {reason}")
    elif error_context['error_type']:
        category_map = {
            'lambda': 'Lambda function',
            'test': 'Test',
            'build': 'Build',
            'deployment': 'Deployment',
            'permission': 'Permission',
            'timeout': 'Timeout',
            'resource': 'Resource',
        }
        category = category_map.get(error_context['error_type'], 'Deployment')
        summary_parts.append(f"{category} failed")
        
        if error_context['failed_resource']:
            summary_parts.append(f"({error_context['failed_resource']})")
    else:
        summary_parts.append('Deployment failed')
    
    # Add main error message (extract key phrase, limit to essential info)
    if error_context['main_error'] and len(summary_parts) < 3:
        # Clean and extract key information
        error_msg = error_context['main_error']
        # Remove common prefixes and timestamps
        error_msg = re.sub(r'^(Error|Failed|Exception):\s*', '', error_msg, flags=re.IGNORECASE)
        error_msg = re.sub(r'\d{4}-\d{2}-\d{2}T[\d:\.]+Z\s*', '', error_msg)  # Remove timestamps
        
        # Extract first meaningful sentence or phrase
        sentences = re.split(r'[.!?]\s+', error_msg)
        if sentences:
            key_msg = sentences[0].strip()
            # Remove redundant information
            key_msg = re.sub(r'Process completed with exit code \d+', '', key_msg, flags=re.IGNORECASE)
            key_msg = re.sub(r'##\[error\]', '', key_msg, flags=re.IGNORECASE)
            key_msg = ' '.join(key_msg.split())  # Normalize whitespace
            
            # Limit length
            words = key_msg.split()
            if len(words) > 15:
                words = words[:15]
                key_msg = ' '.join(words) + '...'
            
            if key_msg and key_msg not in ' '.join(summary_parts):
                summary_parts.append(f"- {key_msg}")
    
    # Add exit code if available and not already mentioned
    if error_context['exit_code'] and error_context['exit_code'] not in ' '.join(summary_parts):
        summary_parts.append(f"(exit code {error_context['exit_code']})")
    
    # Combine into summary
    summary = ' '.join(summary_parts)
    
    # Ensure it's approximately 50 words
    words = summary.split()
    if len(words) > max_words:
        # Trim to 50 words, prioritizing the beginning (most important info)
        summary = ' '.join(words[:max_words])
        if not summary[-1] in '.!?':
            summary += '...'
    elif len(words) < 25:
        # If too short and we have more error context, add it
        if error_context['main_error']:
            error_words = error_context['main_error'].split()
            # Add up to 25 more words
            additional_words = error_words[:min(25, max_words - len(words))]
            if additional_words:
                additional_text = ' '.join(additional_words)
                # Clean up
                additional_text = re.sub(r'\d{4}-\d{2}-\d{2}T[\d:\.]+Z\s*', '', additional_text)
                additional_text = ' '.join(additional_text.split())
                if additional_text:
                    summary += ' ' + additional_text
                    words = summary.split()
                    if len(words) > max_words:
                        summary = ' '.join(words[:max_words])
    
    return summary.strip()


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
    
    # Extract workflow run id from statuses and fetch run data for accurate timing
    workflow_run_id = extract_workflow_run_id_from_statuses(statuses)
    workflow_run_data = get_workflow_run_data(owner_repo, workflow_run_id) if workflow_run_id else None
    
    # Use workflow run data if available, otherwise fall back to status/deployment times
    deployment_start_time = (
        (workflow_run_data.get('run_started_at') if workflow_run_data else None)
        or (workflow_run_data.get('created_at') if workflow_run_data else None)
        or status_created_at
        or created_at
        or ''
    )
    deployment_end_time = (
        (workflow_run_data.get('updated_at') if workflow_run_data else None)
        or status_updated_at
        or ''
    )
    
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
    
    # Calculate deployment duration from workflow run or status times
    duration_seconds = None
    duration_str = '-'
    
    # Use workflow run timing if available; otherwise derive from status times
    if deployment_dt and completion_dt:
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
    
    # Error summary: extract detailed error from workflow logs if available
    error_summary = ''
    if status_state in ['error', 'failure']:
        # Try to get detailed error from workflow logs
        if workflow_run_id:
            try:
                jobs = get_workflow_run_jobs(owner_repo, workflow_run_id)
                # Find failed jobs
                failed_jobs = [
                    job for job in jobs
                    if job.get('conclusion') in ['failure', 'cancelled']
                ]
                
                if failed_jobs:
                    # Get logs from first failed job
                    first_failed_job = failed_jobs[0]
                    job_id = first_failed_job.get('id')
                    
                    if job_id:
                        log_text = get_job_logs(owner_repo, job_id)
                        if log_text:
                            error_summary = extract_error_summary_from_logs(log_text)
                
                # Fallback to status description if log extraction failed
                if not error_summary:
                    if status_description:
                        error_summary = extract_first_n_words(status_description, 20)
                    else:
                        error_summary = 'Deployment failed'
            except Exception:
                # If any error occurs, fall back to basic error message
                if status_description:
                    error_summary = extract_first_n_words(status_description, 20)
                else:
                    error_summary = 'Deployment failed'
        else:
            # No workflow run ID, use status description
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
        'run_id': str(workflow_run_id) if workflow_run_id else '-',
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
        "Run ID",
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
            sanitize_table_cell(deployment.get('run_id', '-')),
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

