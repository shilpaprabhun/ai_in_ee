#!/usr/bin/env python3
"""
Jira Project Data Extractor

This script extracts data from multiple Jira projects for a specific period
with filters on labels, feature team, and Found In fields.

It extracts:
- Basic issue data (title, description, priority, type, state)
- Comments with authors and timestamps
- State transitions with timestamps
- Assignment changes with timestamps
- Metadata (environment, components, CE/DE refinement status)
- Development data (commits, PRs, builds) from composite custom field
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
import re

try:
    from dotenv import load_dotenv
except ImportError:
    print(
        "Error: python-dotenv package not installed. Install with: pip install python-dotenv",
        file=sys.stderr
    )
    sys.exit(1)

try:
    from jira import JIRA
except ImportError:
    print(
        "Error: jira package not installed. Install with: pip install jira",
        file=sys.stderr
    )
    sys.exit(1)

try:
    import html2text
except ImportError:
    print(
        "Warning: html2text package not installed. HTML formatting will not be preserved. "
        "Install with: pip install html2text",
        file=sys.stderr
    )
    html2text = None

# Load environment variables from .env file
load_dotenv()


class JiraProjectDataExtractor:
    """
    Extract Jira issue data from multiple projects filtered by date range
    and custom fields (labels, feature team, Found In).
    """

    def __init__(
        self,
        server: str,
        email: str,
        api_token: str
    ):
        """
        Initialize the Jira extractor.

        Args:
            server: Jira server URL (e.g., https://your-domain.atlassian.net)
            email: Jira user email
            api_token: Jira API token
        """
        self.server = server
        self.email = email
        self.api_token = api_token

        # Initialize Jira client
        try:
            print("Connecting to Jira server...")
            connect_start_time = time.time()
            self.jira = JIRA(
                server=server,
                basic_auth=(email, api_token)
            )
            connect_duration = time.time() - connect_start_time
            print(f"Successfully connected to Jira in {connect_duration:.2f}s")
        except Exception as e:
            print(f"Error connecting to Jira: {e}", file=sys.stderr)
            sys.exit(1)

        # Cache for field mappings
        self._field_cache: Dict[str, Optional[str]] = {}
        
        # Initialize HTML to Markdown converter
        if html2text is not None:
            self.html_converter = html2text.HTML2Text()
            self.html_converter.ignore_links = False
            self.html_converter.ignore_images = False
            self.html_converter.ignore_emphasis = False
            self.html_converter.body_width = 0
            self.html_converter.unicode_snob = True
            self.html_converter.mark_code = True
        else:
            self.html_converter = None

    def _get_field_id(self, field_name: str) -> Optional[str]:
        """
        Get custom field ID by name.

        Args:
            field_name: Field name to search for

        Returns:
            Field ID if found, None otherwise
        """
        if field_name in self._field_cache:
            return self._field_cache[field_name]

        try:
            fields = self.jira.fields()
            for field in fields:
                if field['name'].lower() == field_name.lower():
                    field_id = field['id']
                    self._field_cache[field_name] = field_id
                    return field_id
        except Exception as e:
            print(
                f"Warning: Error getting field ID for {field_name}: {e}",
                file=sys.stderr
            )

        self._field_cache[field_name] = None
        return None

    def _get_field_id_numeric(self, field_name: str) -> Optional[str]:
        """
        Get custom field ID numeric part for JQL queries.
        
        JQL requires just the numeric part (e.g., "10354") not the full ID
        (e.g., "customfield_10354").
        
        Args:
            field_name: Field name to search for
            
        Returns:
            Numeric part of field ID if found, None otherwise
        """
        field_id = self._get_field_id(field_name)
        if not field_id:
            return None
        
        # Extract numeric part from customfield_XXXXX format
        # Handle both "customfield_10354" and "10354" formats
        if field_id.startswith('customfield_'):
            return field_id.replace('customfield_', '')
        # If it's already numeric, return as is
        if field_id.isdigit():
            return field_id
        # Try to extract any trailing digits
        match = re.search(r'(\d+)$', field_id)
        if match:
            return match.group(1)
        
        return None

    def _field_exists_in_issue(self, issue: Any, field_id: str) -> bool:
        """
        Check if a field exists in an issue.

        Args:
            issue: Jira issue object
            field_id: Field ID to check

        Returns:
            True if field exists and has a value, False otherwise
        """
        try:
            return hasattr(issue.fields, field_id) and getattr(issue.fields, field_id) is not None
        except Exception:
            return False

    def _get_field_value(self, issue: Any, field_id: str) -> Any:
        """
        Get field value from issue.

        Args:
            issue: Jira issue object
            field_id: Field ID

        Returns:
            Field value or None
        """
        try:
            if hasattr(issue.fields, field_id):
                return getattr(issue.fields, field_id)
        except Exception:
            pass
        return None

    def _matches_filter(
        self,
        issue: Any,
        filter_labels: Optional[List[str]] = None,
        filter_feature_team: Optional[str] = None,
        filter_found_in: Optional[str] = None
    ) -> bool:
        """
        Check if issue matches filter criteria.

        Args:
            issue: Jira issue object
            filter_labels: List of labels to filter by (OR logic within labels)
            filter_feature_team: Feature team value to filter by
            filter_found_in: Found In value to filter by

        Returns:
            True if issue matches filters, False otherwise
            
        Note: If both filter_labels and filter_feature_team are provided with the same value,
        it uses OR logic (matches if either label OR feature team contains the value).
        Otherwise, uses AND logic (all provided filters must match).
        """
        # If filtering for same value in labels and feature team, use OR logic
        if (filter_labels and filter_feature_team and 
            len(filter_labels) == 1 and filter_labels[0].lower() == filter_feature_team.lower()):
            # OR logic: match if EITHER label OR feature team contains the value
            value = filter_labels[0].lower()
            
            # Check labels
            issue_labels = []
            for label in (issue.fields.labels or []):
                if hasattr(label, 'name'):
                    issue_labels.append(label.name.lower())
                else:
                    issue_labels.append(str(label).lower())
            label_match = value in issue_labels
            
            # Check feature team
            feature_team_match = False
            feature_team_field_id = self._get_field_id('Feature Team')
            if feature_team_field_id and self._field_exists_in_issue(issue, feature_team_field_id):
                field_value = self._get_field_value(issue, feature_team_field_id)
                if field_value:
                    if isinstance(field_value, list):
                        team_values = [str(v).lower() for v in field_value]
                    else:
                        team_values = [str(field_value).lower()]
                    feature_team_match = value in team_values
            
            # Return True if either matches
            if label_match or feature_team_match:
                # Still need to check Found In if provided
                if filter_found_in:
                    found_in_field_id = self._get_field_id('Found In')
                    if found_in_field_id and self._field_exists_in_issue(issue, found_in_field_id):
                        field_value = self._get_field_value(issue, found_in_field_id)
                        if field_value:
                            if isinstance(field_value, list):
                                found_in_values = [str(v).lower() for v in field_value]
                            else:
                                found_in_values = [str(field_value).lower()]
                            return filter_found_in.lower() in found_in_values
                        return False
                    return False
                return True
            return False
        
        # Default AND logic for different filters
        # Check labels
        if filter_labels:
            issue_labels = []
            for label in (issue.fields.labels or []):
                if hasattr(label, 'name'):
                    issue_labels.append(label.name.lower())
                else:
                    issue_labels.append(str(label).lower())
            filter_labels_lower = [label.lower() for label in filter_labels]
            if not any(label in issue_labels for label in filter_labels_lower):
                return False

        # Check feature team (if filter provided and field exists)
        if filter_feature_team:
            feature_team_field_id = self._get_field_id('Feature Team')
            if feature_team_field_id and self._field_exists_in_issue(issue, feature_team_field_id):
                field_value = self._get_field_value(issue, feature_team_field_id)
                if field_value:
                    # Handle multi-value fields (list or single value)
                    if isinstance(field_value, list):
                        team_values = [str(v).lower() for v in field_value]
                    else:
                        team_values = [str(field_value).lower()]
                    if filter_feature_team.lower() not in team_values:
                        return False
                else:
                    return False

        # Check Found In (if filter provided and field exists)
        if filter_found_in:
            found_in_field_id = self._get_field_id('Found In')
            if found_in_field_id and self._field_exists_in_issue(issue, found_in_field_id):
                field_value = self._get_field_value(issue, found_in_field_id)
                if field_value:
                    # Handle multi-value fields
                    if isinstance(field_value, list):
                        found_in_values = [str(v).lower() for v in field_value]
                    else:
                        found_in_values = [str(field_value).lower()]
                    if filter_found_in.lower() not in found_in_values:
                        return False
                else:
                    return False

        return True

    def _extract_basic_data(self, issue: Any) -> Dict[str, Any]:
        """
        Extract basic issue data.

        Args:
            issue: Jira issue object

        Returns:
            Dictionary with basic issue data
        """
        created_by = None
        if issue.fields.creator:
            created_by = issue.fields.creator.displayName or issue.fields.creator.name

        assignee = None
        if issue.fields.assignee:
            assignee = issue.fields.assignee.displayName or issue.fields.assignee.name

        current_state = None
        if issue.fields.status:
            current_state = issue.fields.status.name

        priority = None
        if issue.fields.priority:
            priority = issue.fields.priority.name

        issue_type = None
        if issue.fields.issuetype:
            issue_type = issue.fields.issuetype.name

        description = ''
        if issue.fields.description:
            # Get raw description to preserve Jira wiki markup
            try:
                # Access the raw description field directly
                raw_desc = issue.raw['fields'].get('description')
                if raw_desc:
                    # If it's a dict (ADF format), try to extract text
                    if isinstance(raw_desc, dict):
                        # Try to get text from ADF structure
                        if 'content' in raw_desc:
                            # ADF format - extract text nodes
                            text_parts = []
                            def extract_text(node):
                                if isinstance(node, dict):
                                    if node.get('type') == 'text':
                                        text_parts.append(node.get('text', ''))
                                    if 'content' in node:
                                        for child in node['content']:
                                            extract_text(child)
                                elif isinstance(node, list):
                                    for item in node:
                                        extract_text(item)
                            extract_text(raw_desc)
                            description = '\n'.join(text_parts) if text_parts else str(raw_desc)
                        else:
                            description = str(raw_desc)
                    else:
                        description = str(raw_desc)
            except Exception:
                # Fallback to string representation
                description = str(issue.fields.description)
            
            # If description contains HTML, convert it while preserving structure
            if '<' in description and '>' in description and '{' not in description:
                # Convert HTML to markdown preserving formatting
                if self.html_converter:
                    description = self.html_converter.handle(description)
            
            # Convert Jira wiki markup to markdown equivalents
            # {noformat} -> code block
            description = re.sub(r'\{noformat\}(.*?)\{/noformat\}', r'```\n\1\n```', description, flags=re.DOTALL)
            # {code} -> code block
            description = re.sub(r'\{code\}(.*?)\{/code\}', r'```\n\1\n```', description, flags=re.DOTALL)
            # {code:lang} -> code block with language
            description = re.sub(r'\{code:([^}]+)\}(.*?)\{/code\}', r'```\1\n\2\n```', description, flags=re.DOTALL)
            # {panel} -> blockquote
            description = re.sub(r'\{panel\}(.*?)\{/panel\}', r'> \1', description, flags=re.DOTALL)
            # *bold* -> **bold**
            description = re.sub(r'\*([^*]+)\*', r'**\1**', description)
            # _italic_ -> *italic*
            description = re.sub(r'_([^_]+)_', r'*\1*', description)

        return {
            'key': issue.key,
            'title': issue.fields.summary or '',
            'description': description,
            'priority': priority or 'N/A',
            'type': issue_type or 'N/A',
            'created_by': created_by or 'N/A',
            'created_at': issue.fields.created,
            'current_state': current_state or 'N/A',
            'current_assignee': assignee or 'Unassigned',
            'labels': [
                label.name if hasattr(label, 'name') else str(label)
                for label in (issue.fields.labels or [])
            ]
        }

    def _extract_comments(self, issue: Any) -> List[Dict[str, str]]:
        """
        Extract all comments from issue.

        Args:
            issue: Jira issue object

        Returns:
            List of comment dictionaries
        """
        comments = []
        try:
            comment_fetch_start = time.time()
            issue_comments = self.jira.comments(issue)
            comment_fetch_duration = time.time() - comment_fetch_start
            
            # Only log if there are many comments or it took significant time
            if len(issue_comments) > 10 or comment_fetch_duration > 1.0:
                print(f"      Fetched {len(issue_comments)} comments for {issue.key} (took {comment_fetch_duration:.2f}s)")
            
            for comment in issue_comments:
                author = None
                if comment.author:
                    author = comment.author.displayName or comment.author.name

                body = ''
                if comment.body:
                    # Get raw comment body
                    try:
                        raw_body = comment.raw.get('body')
                        if raw_body:
                            if isinstance(raw_body, dict):
                                # ADF format - extract text
                                text_parts = []
                                def extract_text(node):
                                    if isinstance(node, dict):
                                        if node.get('type') == 'text':
                                            text_parts.append(node.get('text', ''))
                                        if 'content' in node:
                                            for child in node['content']:
                                                extract_text(child)
                                    elif isinstance(node, list):
                                        for item in node:
                                            extract_text(item)
                                extract_text(raw_body)
                                body = '\n'.join(text_parts) if text_parts else str(raw_body)
                            else:
                                body = str(raw_body)
                    except Exception:
                        body = str(comment.body)
                    
                    # Convert HTML if needed
                    if '<' in body and '>' in body and '{' not in body:
                        if self.html_converter:
                            body = self.html_converter.handle(body)
                    
                    # Convert Jira wiki markup to markdown
                    body = re.sub(r'\{noformat\}(.*?)\{/noformat\}', r'```\n\1\n```', body, flags=re.DOTALL)
                    body = re.sub(r'\{code\}(.*?)\{/code\}', r'```\n\1\n```', body, flags=re.DOTALL)
                    body = re.sub(r'\{code:([^}]+)\}(.*?)\{/code\}', r'```\1\n\2\n```', body, flags=re.DOTALL)
                    body = re.sub(r'\{panel\}(.*?)\{/panel\}', r'> \1', body, flags=re.DOTALL)
                    body = re.sub(r'\*([^*]+)\*', r'**\1**', body)
                    body = re.sub(r'_([^_]+)_', r'*\1*', body)

                comments.append({
                    'author': author or 'Unknown',
                    'created': comment.created,
                    'content': body
                })
        except Exception as e:
            print(
                f"Warning: Error extracting comments for {issue.key}: {e}",
                file=sys.stderr
            )

        return comments

    def _extract_state_transitions(self, issue: Any) -> List[Dict[str, str]]:
        """
        Extract state transitions from issue changelog.

        Args:
            issue: Jira issue object

        Returns:
            List of state transition dictionaries
        """
        transitions = []
        try:
            # Note: This may make an additional API call if changelog wasn't already fetched
            transition_fetch_start = time.time()
            changelog = self.jira.issue(issue.key, expand='changelog')
            transition_fetch_duration = time.time() - transition_fetch_start
            
            if changelog.changelog and changelog.changelog.histories:
                history_count = len(changelog.changelog.histories)
                # Only log if there are many histories or it took significant time
                if history_count > 20 or transition_fetch_duration > 1.0:
                    print(f"      Extracting state transitions from {history_count} changelog entries for {issue.key} (fetch took {transition_fetch_duration:.2f}s)")
                for history in changelog.changelog.histories:
                    for item in history.items:
                        if item.field == 'status':
                            changed_by = None
                            if history.author:
                                changed_by = history.author.displayName or history.author.name

                            transitions.append({
                                'from': item.fromString or 'N/A',
                                'to': item.toString or 'N/A',
                                'changed_by': changed_by or 'Unknown',
                                'changed_at': history.created
                            })
        except Exception as e:
            print(
                f"Warning: Error extracting state transitions for {issue.key}: {e}",
                file=sys.stderr
            )

        return transitions

    def _extract_assignment_changes(self, issue: Any) -> List[Dict[str, str]]:
        """
        Extract assignment changes from issue changelog.

        Args:
            issue: Jira issue object

        Returns:
            List of assignment change dictionaries
        """
        changes = []
        try:
            # Note: This may make an additional API call if changelog wasn't already fetched
            assignment_fetch_start = time.time()
            changelog = self.jira.issue(issue.key, expand='changelog')
            assignment_fetch_duration = time.time() - assignment_fetch_start
            
            if changelog.changelog and changelog.changelog.histories:
                history_count = len(changelog.changelog.histories)
                # Only log if there are many histories or it took significant time
                if history_count > 20 or assignment_fetch_duration > 1.0:
                    print(f"      Extracting assignment changes from {history_count} changelog entries for {issue.key} (fetch took {assignment_fetch_duration:.2f}s)")
                for history in changelog.changelog.histories:
                    for item in history.items:
                        if item.field == 'assignee':
                            changed_by = None
                            if history.author:
                                changed_by = history.author.displayName or history.author.name

                            from_assignee = 'Unassigned'
                            if item.fromString:
                                from_assignee = item.fromString
                            elif item.from_:
                                from_assignee = item.from_

                            to_assignee = 'Unassigned'
                            if item.toString:
                                to_assignee = item.toString
                            elif item.to:
                                to_assignee = item.to

                            changes.append({
                                'from': from_assignee,
                                'to': to_assignee,
                                'changed_by': changed_by or 'Unknown',
                                'changed_at': history.created
                            })
        except Exception as e:
            print(
                f"Warning: Error extracting assignment changes for {issue.key}: {e}",
                file=sys.stderr
            )

        return changes

    def _extract_metadata(self, issue: Any) -> Dict[str, Any]:
        """
        Extract metadata fields (environment, components, CE/DE status).

        Args:
            issue: Jira issue object

        Returns:
            Dictionary with metadata
        """
        metadata = {
            'environment': 'N/A',
            'components': [],
            'ce_de_refinement_status': 'N/A',
            'feature_team': 'N/A',
            'found_in': 'N/A'
        }

        # Environment
        env_field_id = self._get_field_id('Environment')
        if env_field_id and self._field_exists_in_issue(issue, env_field_id):
            env_value = self._get_field_value(issue, env_field_id)
            if env_value:
                if isinstance(env_value, list):
                    metadata['environment'] = ', '.join([str(v) for v in env_value])
                else:
                    metadata['environment'] = str(env_value)

        # Components
        if issue.fields.components:
            metadata['components'] = [comp.name for comp in issue.fields.components]

        # CE/DE Refinement Status
        ce_de_field_id = self._get_field_id('CE/DE Refinement Status')
        if ce_de_field_id and self._field_exists_in_issue(issue, ce_de_field_id):
            ce_de_value = self._get_field_value(issue, ce_de_field_id)
            if ce_de_value:
                if isinstance(ce_de_value, list):
                    metadata['ce_de_refinement_status'] = ', '.join([str(v) for v in ce_de_value])
                else:
                    metadata['ce_de_refinement_status'] = str(ce_de_value)

        # Feature Team
        feature_team_field_id = self._get_field_id('Feature Team')
        if feature_team_field_id and self._field_exists_in_issue(issue, feature_team_field_id):
            team_value = self._get_field_value(issue, feature_team_field_id)
            if team_value:
                if isinstance(team_value, list):
                    metadata['feature_team'] = ', '.join([str(v) for v in team_value])
                else:
                    metadata['feature_team'] = str(team_value)

        # Found In
        found_in_field_id = self._get_field_id('Found In')
        if found_in_field_id and self._field_exists_in_issue(issue, found_in_field_id):
            found_in_value = self._get_field_value(issue, found_in_field_id)
            if found_in_value:
                if isinstance(found_in_value, list):
                    metadata['found_in'] = ', '.join([str(v) for v in found_in_value])
                else:
                    metadata['found_in'] = str(found_in_value)

        return metadata

    def _extract_development_data(self, issue: Any) -> Dict[str, List[Dict[str, str]]]:
        """
        Extract development data from composite custom field.

        Args:
            issue: Jira issue object

        Returns:
            Dictionary with commits, PRs, and builds
        """
        development = {
            'commits': [],
            'pull_requests': [],
            'builds': []
        }

        try:
            dev_field_id = self._get_field_id('Development')
            if dev_field_id and self._field_exists_in_issue(issue, dev_field_id):
                dev_value = self._get_field_value(issue, dev_field_id)
                if dev_value:
                    # Development field is typically a composite field
                    # Parse the structure to extract commits, PRs, builds
                    dev_str = str(dev_value)
                    
                    # Try to parse commits (common format: commit IDs)
                    commit_pattern = r'commit[:\s]+([a-f0-9]{7,40})'
                    commits = re.findall(commit_pattern, dev_str, re.IGNORECASE)
                    development['commits'] = [{'id': c} for c in commits]

                    # Try to parse PRs (common format: PR-123 or #123)
                    pr_pattern = r'(?:PR|pull request|#)(?:-|\s)?(\d+)'
                    pr_matches = re.findall(pr_pattern, dev_str, re.IGNORECASE)
                    development['pull_requests'] = [{'id': pr_id} for pr_id in pr_matches]

                    # Try to parse builds (common format: Build-123 or build status)
                    build_pattern = r'build[:\s]+(\d+|[a-z0-9-]+)'
                    builds = re.findall(build_pattern, dev_str, re.IGNORECASE)
                    development['builds'] = [{'id': b, 'status': 'Unknown'} for b in builds]

                    # If the field is structured data, try to parse it
                    if isinstance(dev_value, dict):
                        if 'commits' in dev_value:
                            development['commits'] = dev_value['commits']
                        if 'pullRequests' in dev_value or 'pull_requests' in dev_value:
                            prs = dev_value.get('pullRequests') or dev_value.get('pull_requests', [])
                            development['pull_requests'] = prs
                        if 'builds' in dev_value:
                            development['builds'] = dev_value['builds']
        except Exception as e:
            print(
                f"Warning: Error extracting development data for {issue.key}: {e}",
                file=sys.stderr
            )

        return development

    def _get_issue_count(self, jql: str) -> int:
        """
        Get the count of issues matching a JQL query.
        
        Uses enhanced_search_issues with json_result=True to paginate through
        results and get an accurate count without fetching full issue data.
        
        Args:
            jql: JQL query string
            
        Returns:
            Number of issues matching the query
        """
        try:
            # Use enhanced_search_issues with json_result=True for efficient counting
            # Paginate through results using nextPageToken
            count = 0
            next_page_token = None
            max_results = 100  # Page size
            
            while True:
                # Get JSON response to access pagination tokens
                response = self.jira.enhanced_search_issues(
                    jql_str=jql,
                    nextPageToken=next_page_token,
                    maxResults=max_results,
                    fields=['key'],  # Only need keys for counting
                    json_result=True
                )
                
                # Extract issues from response
                issues = response.get('issues', [])
                if not issues:
                    break
                
                count += len(issues)
                
                # Check for next page token
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
                
                # Safety limit
                if count >= 10000:
                    break
            
            return count
        except Exception as e:
            print(
                f"Warning: Error getting issue count: {e}",
                file=sys.stderr
            )
            return 0

    def _build_jql_query(
        self,
        project_key: str,
        start_date: datetime,
        end_date: datetime,
        filter_labels: Optional[List[str]] = None,
        filter_feature_team: Optional[str] = None,
        filter_found_in: Optional[str] = None,
        feature_team_field_id_numeric: Optional[str] = None,
        found_in_field_id_numeric: Optional[str] = None
    ) -> str:
        """
        Build JQL query for a project with filters.
        
        Args:
            project_key: Project key
            start_date: Start date for filtering
            end_date: End date for filtering
            filter_labels: List of labels to filter by
            filter_feature_team: Feature team value to filter by
            filter_found_in: Found In value to filter by
            feature_team_field_id_numeric: Numeric field ID for Feature Team
            found_in_field_id_numeric: Numeric field ID for Found In
            
        Returns:
            JQL query string
        """
        # Build JQL query (quote project key to handle reserved words like "IS")
        jql_parts = [f'project = "{project_key}"']
        
        # Date filter: issues created OR updated within the date range
        # Use < end_date+1 to include the full end date (Jira dates are exclusive for <=)
        start_str = start_date.strftime('%Y-%m-%d')
        end_date_plus_one = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')
        jql_parts.append(
            f"((created >= {start_str} AND created < {end_date_plus_one}) "
            f"OR (updated >= {start_str} AND updated < {end_date_plus_one}))"
        )

        # Add label and feature team filters to JQL
        # If labels and feature team are the same value, use OR logic in JQL
        if (filter_labels and filter_feature_team and 
            len(filter_labels) == 1 and 
            filter_labels[0].lower() == filter_feature_team.lower()):
            # OR logic: match if EITHER label OR feature team matches
            if feature_team_field_id_numeric:
                # Use field ID format: cf[field_id] = "value"
                jql_parts.append(
                    f'(labels = "{filter_feature_team}" OR cf[{feature_team_field_id_numeric}] = "{filter_feature_team}")'
                )
            else:
                # Fallback to field name format
                jql_parts.append(
                    f'(labels = "{filter_feature_team}" OR "Feature Team" = "{filter_feature_team}")'
                )
        else:
            # Default AND logic: add filters separately
            # Add label filters to JQL
            if filter_labels:
                if len(filter_labels) == 1:
                    # Single label: labels = "label"
                    jql_parts.append(f'labels = "{filter_labels[0]}"')
                else:
                    # Multiple labels: labels IN ("label1", "label2")
                    label_list = ', '.join([f'"{label}"' for label in filter_labels])
                    jql_parts.append(f'labels IN ({label_list})')

            # Add feature team filter to JQL
            if filter_feature_team:
                if feature_team_field_id_numeric:
                    # Use field ID format: cf[field_id] = "value"
                    jql_parts.append(f'cf[{feature_team_field_id_numeric}] = "{filter_feature_team}"')
                else:
                    # Fallback to field name format
                    jql_parts.append(f'"Feature Team" = "{filter_feature_team}"')

        # Add Found In filter to JQL
        if filter_found_in:
            if found_in_field_id_numeric:
                jql_parts.append(f'cf[{found_in_field_id_numeric}] = "{filter_found_in}"')
            else:
                jql_parts.append(f'"Found In" = "{filter_found_in}"')

        return ' AND '.join(jql_parts)

    def extract_issues(
        self,
        project_keys: List[str],
        start_date: datetime,
        end_date: datetime,
        filter_labels: Optional[List[str]] = None,
        filter_feature_team: Optional[str] = None,
        filter_found_in: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extract issues from multiple projects matching filters.

        Args:
            project_keys: List of project keys to search
            start_date: Start date for filtering
            end_date: End date for filtering
            filter_labels: List of labels to filter by
            filter_feature_team: Feature team value to filter by
            filter_found_in: Found In value to filter by

        Returns:
            List of issue dictionaries with extracted data
        """
        all_issues = []
        overall_start_time = time.time()

        # Get field IDs for custom fields once (they're the same across projects)
        # Use numeric IDs for JQL queries (JQL requires just the number, not "customfield_XXXXX")
        feature_team_field_id_numeric = None
        found_in_field_id_numeric = None
        if filter_feature_team:
            feature_team_field_id_numeric = self._get_field_id_numeric('Feature Team')
            if feature_team_field_id_numeric:
                print(f"  Feature Team field ID: {feature_team_field_id_numeric}")
            else:
                print(f"  Warning: Could not find Feature Team field ID, will use field name in JQL")
        if filter_found_in:
            found_in_field_id_numeric = self._get_field_id_numeric('Found In')
            if found_in_field_id_numeric:
                print(f"  Found In field ID: {found_in_field_id_numeric}")
            else:
                print(f"  Warning: Could not find Found In field ID, will use field name in JQL")

        # First, build JQL queries and get counts for all projects
        print("\n=== Querying Issue Counts ===")
        project_counts = {}
        total_count = 0
        
        for project_key in project_keys:
            jql = self._build_jql_query(
                project_key=project_key,
                start_date=start_date,
                end_date=end_date,
                filter_labels=filter_labels,
                filter_feature_team=filter_feature_team,
                filter_found_in=filter_found_in,
                feature_team_field_id_numeric=feature_team_field_id_numeric,
                found_in_field_id_numeric=found_in_field_id_numeric
            )
            
            print(f"\nProject: {project_key}")
            print(f"  JQL Query: {jql}")
            count = self._get_issue_count(jql)
            project_counts[project_key] = {
                'jql': jql,
                'count': count
            }
            total_count += count
            print(f"  Count: {count} issues")

        # Display summary and ask for confirmation
        print(f"\n=== Summary ===")
        print(f"Total projects: {len(project_keys)}")
        print(f"Total issues matching criteria: {total_count}")
        print("\nBreakdown by project:")
        for project_key, info in project_counts.items():
            print(f"  - {project_key}: {info['count']} issues")
        
        if total_count == 0:
            print("\nNo issues found matching the criteria. Exiting.")
            return []
        
        # Ask user for confirmation
        print(f"\n⚠️  This will fetch and process {total_count} issues.")
        print("This may take a significant amount of time.")
        while True:
            response = input("Do you want to continue? (yes/no): ").strip().lower()
            if response in ('yes', 'y'):
                print("\nProceeding with extraction...")
                break
            elif response in ('no', 'n'):
                print("\nExtraction cancelled by user.")
                return []
            else:
                print("Please enter 'yes' or 'no'.")

        # Proceed with extraction
        for project_idx, project_key in enumerate(project_keys, 1):
            project_start_time = time.time()
            project_info = project_counts[project_key]
            jql = project_info['jql']
            expected_count = project_info['count']
            
            print(f"\n[{project_idx}/{len(project_keys)}] Processing project: {project_key}")
            print(f"  Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
            print(f"  Expected issues: {expected_count}")
            print(f"  JQL Query: {jql}")

            try:
                # Use enhanced_search_issues (new API) which handles pagination automatically
                # This method uses nextPageToken for pagination instead of deprecated startAt
                print(f"  Fetching issues using JQL query...")
                fetch_start_time = time.time()
                
                # Fetch all issues matching the JQL query using enhanced_search_issues
                # The library handles pagination automatically with nextPageToken
                all_issues_result = self.jira.enhanced_search_issues(
                    jql_str=jql,
                    maxResults=False,  # Fetch all results
                    fields=['key']  # Minimal fields - same as count query
                )
                
                fetch_duration = time.time() - fetch_start_time
                print(f"  Found {len(all_issues_result)} issues (took {fetch_duration:.2f}s)")
                
                # Now fetch full issue details with changelog
                issues = []
                print(f"  Fetching full issue details for {len(all_issues_result)} issues...")
                detail_start_time = time.time()
                
                for idx, issue in enumerate(all_issues_result, 1):
                    try:
                        issue_fetch_start = time.time()
                        # Fetch full issue with changelog
                        full_issue = self.jira.issue(issue.key, expand='changelog')
                        issues.append(full_issue)
                        issue_fetch_duration = time.time() - issue_fetch_start
                        
                        if idx % 25 == 0 or idx == len(all_issues_result):
                            print(f"    Fetched {idx}/{len(all_issues_result)} issues (avg {issue_fetch_duration:.2f}s per issue)")
                    except Exception as e:
                        print(f"    Warning: Could not fetch full details for {issue.key}: {e}", file=sys.stderr)
                        # Still add the basic issue if we can't get full details
                        issues.append(issue)
                        continue
                
                detail_duration = time.time() - detail_start_time
                print(f"  Full details fetch completed in {detail_duration:.2f}s")
                print(f"  Found {len(issues)} issues total (took {time.time() - fetch_start_time:.2f}s)")

                project_fetch_duration = time.time() - project_start_time
                print(f"  Found {len(issues)} issues in date range (took {project_fetch_duration:.2f}s)")

                # Process and filter issues
                print(f"  Processing and filtering {len(issues)} issues...")
                processing_start_time = time.time()
                matched_count = 0
                filtered_count = 0
                
                for idx, issue in enumerate(issues, 1):
                    if idx % 50 == 0 or idx == len(issues):
                        print(f"    Processing issue {idx}/{len(issues)}...")
                    
                    issue_process_start = time.time()
                    
                    # Apply filters
                    if not self._matches_filter(
                        issue,
                        filter_labels,
                        filter_feature_team,
                        filter_found_in
                    ):
                        filtered_count += 1
                        continue

                    # Extract all data
                    extract_start = time.time()
                    basic_data = self._extract_basic_data(issue)
                    comments = self._extract_comments(issue)
                    state_transitions = self._extract_state_transitions(issue)
                    assignment_changes = self._extract_assignment_changes(issue)
                    metadata = self._extract_metadata(issue)
                    development = self._extract_development_data(issue)
                    extract_duration = time.time() - extract_start

                    issue_data = {
                        **basic_data,
                        'comments': comments,
                        'state_transitions': state_transitions,
                        'assignment_changes': assignment_changes,
                        **metadata,
                        'development': development
                    }

                    all_issues.append(issue_data)
                    matched_count += 1
                    issue_process_duration = time.time() - issue_process_start
                    
                    # Log every 25 issues or if processing is slow
                    if idx % 25 == 0 or idx == len(issues) or issue_process_duration > 5.0:
                        print(f"    Matched {matched_count}: {issue.key} - {basic_data['title'][:50]}... (extraction: {extract_duration:.2f}s, total: {issue_process_duration:.2f}s)")

                processing_duration = time.time() - processing_start_time
                project_total_duration = time.time() - project_start_time
                print(f"  Project {project_key} completed:")
                print(f"    - Total issues fetched: {len(issues)}")
                print(f"    - Matched after filtering: {matched_count}")
                print(f"    - Filtered out: {filtered_count}")
                print(f"    - Processing time: {processing_duration:.2f}s")
                print(f"    - Total project time: {project_total_duration:.2f}s")

            except Exception as e:
                print(
                    f"Error processing project {project_key}: {e}",
                    file=sys.stderr
                )
                import traceback
                traceback.print_exc()
                continue

        overall_duration = time.time() - overall_start_time
        print(f"\n=== Extraction Complete ===")
        print(f"Total matched issues: {len(all_issues)}")
        print(f"Total time: {overall_duration:.2f}s ({overall_duration/60:.2f} minutes)")
        if len(all_issues) > 0:
            print(f"Average time per issue: {overall_duration/len(all_issues):.2f}s")
        
        return all_issues

    def export_to_markdown(
        self,
        issues: List[Dict[str, Any]],
        output_path: Path,
        start_date: datetime,
        end_date: datetime,
        project_keys: List[str],
        filter_labels: Optional[List[str]] = None,
        filter_feature_team: Optional[str] = None,
        filter_found_in: Optional[str] = None
    ) -> None:
        """
        Export extracted issues to markdown format.

        Args:
            issues: List of issue dictionaries
            output_path: Path to output file
            start_date: Start date for header
            end_date: End date for header
            project_keys: List of project keys for header
            filter_labels: Filter labels for header
            filter_feature_team: Filter feature team for header
            filter_found_in: Filter found in for header
        """
        lines = [
            "# Jira Project Data Extract",
            "",
            f"**Date Range:** {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            f"**Projects:** {', '.join(project_keys)}",
            "**Filters:**",
        ]

        if filter_labels:
            lines.append(f"- Labels: {', '.join(filter_labels)}")
        if filter_feature_team:
            lines.append(f"- Feature Team: {filter_feature_team}")
        if filter_found_in:
            lines.append(f"- Found In: {filter_found_in}")

        lines.extend([
            "",
            f"**Total Issues:** {len(issues)}",
            "",
            "---",
            "",
            "## Summary Table",
            "",
        ])

        # Table header
        table_header = [
            "Issue Key", "Title", "Priority", "Type", "Created By",
            "Created At", "Current State", "Current Assignee", "Environment",
            "Components", "CE/DE Status", "State Transitions", "Assignment Changes",
            "Comments", "Commits", "PRs", "Builds", "Labels", "Feature Team", "Found In"
        ]
        lines.append("| " + " | ".join(table_header) + " |")
        lines.append("|" + "|".join(["---"] * len(table_header)) + "|")

        # Table rows
        for issue in issues:
            created_at = issue['created_at'][:10] if issue['created_at'] else 'N/A'
            components_str = ', '.join(issue.get('components', [])) or 'N/A'
            labels_str = ', '.join(issue.get('labels', [])) or 'N/A'

            row = [
                issue['key'],
                issue['title'][:50] + '...' if len(issue['title']) > 50 else issue['title'],
                issue['priority'],
                issue['type'],
                issue['created_by'],
                created_at,
                issue['current_state'],
                issue['current_assignee'],
                issue.get('environment', 'N/A'),
                components_str,
                issue.get('ce_de_refinement_status', 'N/A'),
                str(len(issue.get('state_transitions', []))),
                str(len(issue.get('assignment_changes', []))),
                str(len(issue.get('comments', []))),
                str(len(issue.get('development', {}).get('commits', []))),
                str(len(issue.get('development', {}).get('pull_requests', []))),
                str(len(issue.get('development', {}).get('builds', []))),
                labels_str,
                issue.get('feature_team', 'N/A'),
                issue.get('found_in', 'N/A')
            ]
            lines.append("| " + " | ".join(row) + " |")

        lines.extend([
            "",
            "---",
            "",
            "## Detailed Issue Information",
            ""
        ])

        # Detailed sections
        print(f"  Generating detailed sections for {len(issues)} issues...")
        for idx, issue in enumerate(issues, 1):
            if idx % 50 == 0 or idx == len(issues):
                print(f"    Writing issue {idx}/{len(issues)}...")
            lines.extend([
                f"### {issue['key']}: {issue['title']}",
                "",
                "**Description:**",
                ""
            ])
            
            # Description is already formatted, just add it
            description = issue['description'] or "*No description*"
            if description and description != "*No description*":
                lines.append(description)
            else:
                lines.append(description)
            
            lines.append("")
            
            lines.extend([
                f"**Priority:** {issue['priority']}",
                f"**Type:** {issue['type']}",
                f"**Created By:** {issue['created_by']}",
                f"**Created At:** {issue['created_at']}",
                f"**Current State:** {issue['current_state']}",
                f"**Current Assignee:** {issue['current_assignee']}",
                f"**Environment:** {issue.get('environment', 'N/A')}",
                f"**Components:** {', '.join(issue.get('components', [])) or 'N/A'}",
                f"**CE/DE Refinement Status:** {issue.get('ce_de_refinement_status', 'N/A')}",
                f"**Labels:** {', '.join(issue.get('labels', [])) or 'None'}",
                f"**Feature Team:** {issue.get('feature_team', 'N/A')}",
                f"**Found In:** {issue.get('found_in', 'N/A')}",
                ""
            ])

            # State transitions
            transitions = issue.get('state_transitions', [])
            if transitions:
                lines.extend([
                    "#### State Transitions",
                    "",
                    "| From | To | Changed By | Changed At |",
                    "|------|-----|------------|------------|"
                ])
                for trans in transitions:
                    changed_at = trans['changed_at'][:19] if trans['changed_at'] else 'N/A'
                    lines.append(
                        f"| {trans['from']} | {trans['to']} | "
                        f"{trans['changed_by']} | {changed_at} |"
                    )
                lines.append("")
            else:
                lines.extend([
                    "#### State Transitions",
                    "",
                    "*No state transitions recorded*",
                    ""
                ])

            # Assignment changes
            assignments = issue.get('assignment_changes', [])
            if assignments:
                lines.extend([
                    "#### Assignment Changes",
                    "",
                    "| From | To | Changed By | Changed At |",
                    "|------|-----|------------|------------|"
                ])
                for assign in assignments:
                    changed_at = assign['changed_at'][:19] if assign['changed_at'] else 'N/A'
                    lines.append(
                        f"| {assign['from']} | {assign['to']} | "
                        f"{assign['changed_by']} | {changed_at} |"
                    )
                lines.append("")
            else:
                lines.extend([
                    "#### Assignment Changes",
                    "",
                    "*No assignment changes recorded*",
                    ""
                ])

            # Comments
            comments = issue.get('comments', [])
            if comments:
                lines.extend([
                    "#### Comments",
                    ""
                ])
                for comment in comments:
                    created = comment['created'][:19] if comment['created'] else 'N/A'
                    lines.extend([
                        f"**{comment['author']}** ({created}):",
                        "",
                        comment['content'] or "*No content*",
                        ""
                    ])
            else:
                lines.extend([
                    "#### Comments",
                    "",
                    "*No comments*",
                    ""
                ])

            # Development
            dev = issue.get('development', {})
            commits = dev.get('commits', [])
            prs = dev.get('pull_requests', [])
            builds = dev.get('builds', [])

            lines.extend([
                "#### Development",
                ""
            ])

            if commits:
                lines.append("**Commits:**")
                for commit in commits:
                    lines.append(f"- `{commit.get('id', 'N/A')}`")
                lines.append("")
            else:
                lines.append("**Commits:** None")
                lines.append("")

            if prs:
                lines.append("**Pull Requests:**")
                for pr in prs:
                    pr_id = pr.get('id', 'N/A')
                    pr_title = pr.get('title', '')
                    if pr_title:
                        lines.append(f"- PR-{pr_id}: \"{pr_title}\"")
                    else:
                        lines.append(f"- PR-{pr_id}")
                lines.append("")
            else:
                lines.append("**Pull Requests:** None")
                lines.append("")

            if builds:
                lines.append("**Builds:**")
                for build in builds:
                    build_id = build.get('id', 'N/A')
                    build_status = build.get('status', 'Unknown')
                    build_time = build.get('timestamp', '')
                    if build_time:
                        lines.append(f"- Build-{build_id}: {build_status} ({build_time})")
                    else:
                        lines.append(f"- Build-{build_id}: {build_status}")
                lines.append("")
            else:
                lines.append("**Builds:** None")
                lines.append("")

            lines.append("---")
            lines.append("")

        print(f"  Writing markdown content to file...")
        write_start_time = time.time()
        content = '\n'.join(lines)
        content_size_mb = len(content.encode('utf-8')) / (1024 * 1024)
        print(f"  Content size: {content_size_mb:.2f} MB")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        write_duration = time.time() - write_start_time
        print(f"  File written in {write_duration:.2f}s")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Extract Jira project data filtered by date range and custom fields'
    )
    parser.add_argument(
        '--server',
        default=os.getenv('JIRA_SERVER') or os.getenv('ATLASSIAN_SERVER'),
        help='Jira server URL (e.g., https://your-domain.atlassian.net). '
             'Can also be set via JIRA_SERVER or ATLASSIAN_SERVER environment variable.'
    )
    parser.add_argument(
        '--email',
        default=os.getenv('JIRA_EMAIL') or os.getenv('ATLASSIAN_ACCESSOR_EMAIL') or os.getenv('ATLASSIAN_EMAIL'),
        help='Jira user email. Can also be set via JIRA_EMAIL, '
             'ATLASSIAN_ACCESSOR_EMAIL, or ATLASSIAN_EMAIL environment variable.'
    )
    parser.add_argument(
        '--api-token',
        default=os.getenv('JIRA_API_TOKEN') or os.getenv('ATLASSIAN_API_TOKEN'),
        help='Jira API token. Can also be set via JIRA_API_TOKEN or '
             'ATLASSIAN_API_TOKEN environment variable.'
    )
    parser.add_argument(
        '--projects',
        required=True,
        help='Comma-separated list of project keys'
    )
    parser.add_argument(
        '--start-date',
        help='Start date for filtering (YYYY-MM-DD). If not provided, defaults to 7 days ago.'
    )
    parser.add_argument(
        '--end-date',
        help='End date for filtering (YYYY-MM-DD). If not provided, defaults to today.'
    )
    parser.add_argument(
        '--labels',
        help='Comma-separated list of labels to filter by'
    )
    parser.add_argument(
        '--feature-team',
        help='Feature team value to filter by'
    )
    parser.add_argument(
        '--found-in',
        help='Found In value to filter by'
    )
    parser.add_argument(
        '--output',
        help='Output file path (default: auto-generated)'
    )

    args = parser.parse_args()

    # Validate required credentials
    if not args.server:
        print(
            "Error: Jira server URL is required. "
            "Provide via --server argument, JIRA_SERVER, or ATLASSIAN_SERVER environment variable.",
            file=sys.stderr
        )
        sys.exit(1)

    if not args.email:
        print(
            "Error: Jira email is required. "
            "Provide via --email argument, JIRA_EMAIL, ATLASSIAN_ACCESSOR_EMAIL, "
            "or ATLASSIAN_EMAIL environment variable.",
            file=sys.stderr
        )
        sys.exit(1)

    if not args.api_token:
        print(
            "Error: Jira API token is required. "
            "Provide via --api-token argument, JIRA_API_TOKEN, or ATLASSIAN_API_TOKEN environment variable.",
            file=sys.stderr
        )
        sys.exit(1)

    return args


def main():
    """Main function."""
    args = parse_arguments()

    # Parse dates
    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, '%Y-%m-%d')
        except ValueError:
            print("Error: Invalid end date format. Use YYYY-MM-DD format.", file=sys.stderr)
            sys.exit(1)
    else:
        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
        except ValueError:
            print("Error: Invalid start date format. Use YYYY-MM-DD format.", file=sys.stderr)
            sys.exit(1)
    else:
        # Default to 7 days ago
        start_date = end_date - timedelta(days=7)

    # Parse projects
    project_keys = [key.strip() for key in args.projects.split(',') if key.strip()]

    # Parse filters
    filter_labels = None
    if args.labels:
        filter_labels = [label.strip() for label in args.labels.split(',') if label.strip()]

    filter_feature_team = args.feature_team.strip() if args.feature_team else None
    filter_found_in = args.found_in.strip() if args.found_in else None

    # Generate default output path if not provided
    if args.output:
        output_path = Path(args.output)
    else:
        start_date_str = start_date.strftime('%Y%m%d')
        end_date_str = end_date.strftime('%Y%m%d')
        projects_str = '-'.join(project_keys)
        output_path = Path(
            f"data/domains/jira/"
            f"Jira-{projects_str}-{start_date_str}-{end_date_str}.md"
        )

    print("=== Jira Project Data Extraction ===")
    print(f"Server: {args.server}")
    print(f"Projects: {', '.join(project_keys)}")
    print(f"Date Range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    if filter_labels:
        print(f"Labels: {', '.join(filter_labels)}")
    if filter_feature_team:
        print(f"Feature Team: {filter_feature_team}")
    if filter_found_in:
        print(f"Found In: {filter_found_in}")
    print(f"Output: {output_path}\n")

    # Create extractor
    extractor = JiraProjectDataExtractor(
        server=args.server,
        email=args.email,
        api_token=args.api_token
    )

    # Extract issues
    print("\n=== Starting Issue Extraction ===")
    extraction_start_time = time.time()
    issues = extractor.extract_issues(
        project_keys=project_keys,
        start_date=start_date,
        end_date=end_date,
        filter_labels=filter_labels,
        filter_feature_team=filter_feature_team,
        filter_found_in=filter_found_in
    )
    extraction_duration = time.time() - extraction_start_time
    print(f"\n=== Issue Extraction Complete ===")
    print(f"Extraction took {extraction_duration:.2f}s ({extraction_duration/60:.2f} minutes)")

    # Export to markdown
    if issues:
        print(f"\n=== Starting Markdown Export ===")
        print(f"Exporting {len(issues)} issues to {output_path}")
        export_start_time = time.time()
        extractor.export_to_markdown(
            issues=issues,
            output_path=output_path,
            start_date=start_date,
            end_date=end_date,
            project_keys=project_keys,
            filter_labels=filter_labels,
            filter_feature_team=filter_feature_team,
            filter_found_in=filter_found_in
        )
        export_duration = time.time() - export_start_time
        print(f"Export completed in {export_duration:.2f}s")
        print(f"\nSuccessfully generated {output_path}")
    else:
        print("\nNo issues matched the criteria.")


if __name__ == "__main__":
    main()

