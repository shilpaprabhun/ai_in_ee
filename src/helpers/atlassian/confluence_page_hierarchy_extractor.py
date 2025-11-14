#!/usr/bin/env python3
"""
Confluence Page Hierarchy Extractor

This script extracts data from a Confluence page and all its sub-pages in the
hierarchy, filtering by labels and date range.

It extracts:
- Page title, content, comments, and reactions
- Page metadata (created, updated, versions)
- Page readers (watchers and viewers)
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
import re
from urllib.parse import urlparse, parse_qs
import base64

try:
    from dotenv import load_dotenv
except ImportError:
    print(
        "Error: python-dotenv package not installed. Install with: pip install python-dotenv",
        file=sys.stderr
    )
    sys.exit(1)

try:
    from atlassian import Confluence
except ImportError:
    print(
        "Error: atlassian package not installed. Install with: pip install atlassian-python-api",
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

try:
    import requests
except ImportError:
    print(
        "Warning: requests package not installed. Some features may not work. "
        "Install with: pip install requests",
        file=sys.stderr
    )
    requests = None

# Load environment variables from .env file
load_dotenv()


class ConfluencePageHierarchyExtractor:
    """
    Extract Confluence page hierarchy data filtered by labels and date range.
    """

    def __init__(
        self,
        server: str,
        email: str,
        api_token: str
    ):
        """
        Initialize the Confluence extractor.

        Args:
            server: Confluence server URL (e.g., https://your-domain.atlassian.net)
            email: Confluence user email
            api_token: Confluence API token
        """
        self.server = server
        self.email = email
        self.api_token = api_token

        # Initialize Confluence client
        try:
            self.confluence = Confluence(
                url=server,
                username=email,
                password=api_token
            )
        except Exception as e:
            print(f"Error connecting to Confluence: {e}", file=sys.stderr)
            sys.exit(1)
        
        # Initialize HTML to Markdown converter for preserving formatting
        if html2text is not None:
            self.html_converter = html2text.HTML2Text()
            self.html_converter.ignore_links = False
            self.html_converter.ignore_images = False
            self.html_converter.ignore_emphasis = False
            self.html_converter.body_width = 0  # Don't wrap lines
            self.html_converter.unicode_snob = True  # Preserve Unicode characters (emojis)
            self.html_converter.mark_code = True  # Preserve code blocks
        else:
            self.html_converter = None

    def _extract_page_id_from_url(self, page_url: str) -> Optional[str]:
        """
        Extract page ID from Confluence page URL.

        Args:
            page_url: Confluence page URL

        Returns:
            Page ID if found, None otherwise
        """
        try:
            parsed_url = urlparse(page_url)
            query_params = parse_qs(parsed_url.query)

            # Try pageId parameter first (common in older URLs)
            if 'pageId' in query_params:
                return query_params['pageId'][0]

            # Try to extract from path (newer URL format)
            # Format: /wiki/spaces/SPACE/pages/PAGE_ID/Page+Title
            path_parts = parsed_url.path.split('/')
            if 'pages' in path_parts:
                pages_index = path_parts.index('pages')
                if pages_index + 1 < len(path_parts):
                    return path_parts[pages_index + 1]

            # Try to extract numeric ID from path
            for part in path_parts:
                if part.isdigit():
                    return part

            print(f"Warning: Could not extract page ID from URL: {page_url}", file=sys.stderr)
            return None

        except Exception as e:
            print(f"Error parsing page URL: {e}", file=sys.stderr)
            return None

    def _get_all_descendants(
        self,
        page_id: str,
        visited: Optional[Set[str]] = None
    ) -> List[str]:
        """
        Recursively get all descendant page IDs.

        Args:
            page_id: Starting page ID
            visited: Set of already visited page IDs to avoid cycles

        Returns:
            List of all descendant page IDs (including the starting page)
        """
        if visited is None:
            visited = set()

        if page_id in visited:
            return []

        visited.add(page_id)
        descendants = [page_id]

        try:
            # Use CQL to find child pages (more reliable than get_page_child_by_type)
            space_key = None
            try:
                page_info = self.confluence.get_page_by_id(page_id, expand='space')
                space_key = page_info.get('space', {}).get('key')
            except Exception as e:
                print(
                    f"Warning: Could not get space info for page {page_id}: {e}",
                    file=sys.stderr
                )
                # Still try to continue with CQL if we can construct it

            if space_key:
                # Use CQL to find direct children
                cql = f'space = {space_key} AND parent = {page_id} AND type = page'
                try:
                    children_result = self.confluence.cql(cql, limit=1000)
                    children = children_result.get('results', [])
                    
                    for child in children:
                        child_id = child.get('content', {}).get('id')
                        if child_id and child_id not in visited:
                            child_descendants = self._get_all_descendants(child_id, visited)
                            descendants.extend(child_descendants)
                except Exception as e:
                    print(
                        f"Warning: Error using CQL to get children for page {page_id}: {e}",
                        file=sys.stderr
                    )
            else:
                # Fallback: try get_page_child_by_type if available
                try:
                    if hasattr(self.confluence, 'get_page_child_by_type'):
                        children = self.confluence.get_page_child_by_type(
                            page_id,
                            type='page',
                            start=0,
                            limit=1000
                        )
                        for child in children.get('results', []):
                            child_id = child.get('id')
                            if child_id and child_id not in visited:
                                child_descendants = self._get_all_descendants(child_id, visited)
                                descendants.extend(child_descendants)
                except Exception as e:
                    print(
                        f"Warning: Error getting children for page {page_id}: {e}",
                        file=sys.stderr
                    )

        except Exception as e:
            print(
                f"Warning: Error getting descendants for page {page_id}: {e}",
                file=sys.stderr
            )

        return descendants

    def _page_matches_labels(
        self,
        page_labels: List[str],
        filter_labels: List[str]
    ) -> bool:
        """
        Check if page has any of the filter labels (OR logic).

        Args:
            page_labels: List of labels on the page
            filter_labels: List of labels to filter by

        Returns:
            True if page has any of the filter labels
        """
        if not filter_labels:
            return True

        page_label_set = {label.lower() for label in page_labels}
        filter_label_set = {label.lower().strip() for label in filter_labels}

        return bool(page_label_set & filter_label_set)

    def _page_in_date_range(
        self,
        created_date: str,
        updated_date: str,
        start_date: datetime,
        end_date: datetime
    ) -> bool:
        """
        Check if page was created or updated within the date range.

        Args:
            created_date: Page creation date (ISO format string)
            updated_date: Page last updated date (ISO format string)
            start_date: Start of date range
            end_date: End of date range

        Returns:
            True if page was created or updated within range
        """
        try:
            created_dt = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
            updated_dt = datetime.fromisoformat(updated_date.replace('Z', '+00:00'))

            # Remove timezone for comparison
            created_dt = created_dt.replace(tzinfo=None)
            updated_dt = updated_dt.replace(tzinfo=None)

            created_in_range = start_date <= created_dt <= end_date
            updated_in_range = start_date <= updated_dt <= end_date

            return created_in_range or updated_in_range

        except (ValueError, AttributeError) as e:
            print(
                f"Warning: Error parsing dates: {e}",
                file=sys.stderr
            )
            return False

    def _get_comment_children(self, comment_id: str, depth: int = 0) -> List[Dict]:
        """
        Recursively get nested comments (replies) for a comment.

        Args:
            comment_id: Comment ID
            depth: Current nesting depth (for indentation)

        Returns:
            List of nested comment dictionaries
        """
        nested_comments = []
        if depth > 10:  # Prevent infinite recursion
            return nested_comments

        try:
            if requests is None:
                return nested_comments

            # Use REST API v2 to get comment children
            auth_string = f"{self.email}:{self.api_token}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            headers = {
                'Authorization': f'Basic {auth_b64}',
                'Accept': 'application/json'
            }

            # Try REST API v2 endpoint for comment children
            url = f"{self.server}/wiki/api/v2/footer-comments/{comment_id}/children"
            response = requests.get(url, headers=headers, params={'limit': 1000}, timeout=10)

            if response.status_code == 200:
                data = response.json()
                children = data.get('results', [])
                
                for child in children:
                    comment_data = {
                        'id': child.get('id'),
                        'author': child.get('author', {}).get('displayName', 'Unknown'),
                        'created': child.get('createdAt', ''),
                        'content': child.get('body', {}).get('storage', {}).get('value', ''),
                        'depth': depth + 1,
                    }
                    nested_comments.append(comment_data)
                    
                    # Recursively get nested comments
                    child_nested = self._get_comment_children(child.get('id'), depth + 1)
                    nested_comments.extend(child_nested)
            elif response.status_code == 404:
                # API v2 might not be available, fall back to v1
                pass
            else:
                # Try alternative endpoint using v1 API
                try:
                    # Use the Confluence client's method if available
                    if hasattr(self.confluence, 'get_comment_by_id'):
                        child_result = self.confluence.get_comment_by_id(comment_id)
                        # Process children if available
                        pass
                except Exception:
                    pass

        except Exception as e:
            # Silently fail for nested comments - not critical
            pass

        return nested_comments

    def _get_page_comments(self, page_id: str) -> List[Dict]:
        """
        Get all comments for a page, including nested comments (replies).

        Args:
            page_id: Page ID

        Returns:
            List of comment dictionaries (flat list with depth indicator)
        """
        comments = []
        try:
            # Try REST API v2 first for better nested comment support
            if requests is not None:
                try:
                    auth_string = f"{self.email}:{self.api_token}"
                    auth_bytes = auth_string.encode('ascii')
                    auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
                    headers = {
                        'Authorization': f'Basic {auth_b64}',
                        'Accept': 'application/json'
                    }

                    url = f"{self.server}/wiki/api/v2/pages/{page_id}/footer-comments"
                    response = requests.get(url, headers=headers, params={'limit': 1000}, timeout=10)

                    if response.status_code == 200:
                        data = response.json()
                        top_level_comments = data.get('results', [])
                        
                        for comment in top_level_comments:
                            comment_data = {
                                'id': comment.get('id'),
                                'author': comment.get('author', {}).get('displayName', 'Unknown'),
                                'created': comment.get('createdAt', ''),
                                'content': comment.get('body', {}).get('storage', {}).get('value', ''),
                                'depth': 0,
                            }
                            comments.append(comment_data)
                            
                            # Get nested comments (replies)
                            nested = self._get_comment_children(comment.get('id'), depth=0)
                            comments.extend(nested)
                        
                        return comments
                except Exception:
                    # Fall back to v1 API
                    pass

            # Fall back to v1 API
            try:
                result = self.confluence.get_page_child_by_type(
                    page_id,
                    type='comment',
                    start=0,
                    limit=1000,
                    expand='body.storage,version,children.comment.body.storage,children.comment.version'
                )
            except (AttributeError, TypeError):
                # Fall back to getting page with comments expanded
                page = self.confluence.get_page_by_id(
                    page_id,
                    expand='body.storage,version,children.comment.body.storage,children.comment.version'
                )
                # Extract comments from page children
                children = page.get('children', {}).get('comment', {}).get('results', [])
                result = {'results': children}

            # Handle both dict and list responses
            comment_list = result.get('results', []) if isinstance(result, dict) else result
            
            for comment in comment_list:
                if isinstance(comment, dict):
                    comment_data = {
                        'id': comment.get('id'),
                        'author': comment.get('version', {}).get('by', {}).get('displayName', 'Unknown'),
                        'created': comment.get('version', {}).get('when', ''),
                        'content': comment.get('body', {}).get('storage', {}).get('value', ''),
                        'depth': 0,
                    }
                    comments.append(comment_data)
                    
                    # Try to get nested comments using v1 API
                    try:
                        comment_children = comment.get('children', {}).get('comment', {}).get('results', [])
                        for child in comment_children:
                            child_data = {
                                'id': child.get('id'),
                                'author': child.get('version', {}).get('by', {}).get('displayName', 'Unknown'),
                                'created': child.get('version', {}).get('when', ''),
                                'content': child.get('body', {}).get('storage', {}).get('value', ''),
                                'depth': 1,
                            }
                            comments.append(child_data)
                    except Exception:
                        # Try recursive fetch for nested comments
                        nested = self._get_comment_children(comment.get('id'), depth=0)
                        comments.extend(nested)

        except Exception as e:
            print(
                f"Warning: Error getting comments for page {page_id}: {e}",
                file=sys.stderr
            )

        return comments

    def _get_page_reactions(self, page_id: str) -> List[Dict]:
        """
        Get reactions for a page.

        Args:
            page_id: Page ID

        Returns:
            List of reaction dictionaries
        """
        reactions = []
        try:
            # Try to get reactions via the content property API
            # Note: This may vary by Confluence version
            page = self.confluence.get_page_by_id(
                page_id,
                expand='metadata.properties'
            )

            # Reactions might be in metadata.properties or via a separate endpoint
            # This is a placeholder - actual implementation may need adjustment
            # based on your Confluence version
            properties = page.get('metadata', {}).get('properties', {})
            if 'reactions' in properties:
                reactions_data = properties['reactions']
                # Process reactions data if available
                # Format may vary by Confluence version

        except Exception as e:
            # Reactions might not be available via API in all Confluence versions
            print(
                f"Info: Reactions not available for page {page_id}: {e}",
                file=sys.stderr
            )

        return reactions

    def _get_page_watchers(self, page_id: str) -> List[str]:
        """
        Get list of watchers for a page.

        Args:
            page_id: Page ID

        Returns:
            List of watcher display names
        """
        watchers = []
        try:
            # Try to get watchers using get_watchers method if available
            if hasattr(self.confluence, 'get_watchers'):
                watchers_result = self.confluence.get_watchers(page_id)
                if isinstance(watchers_result, dict):
                    for watcher in watchers_result.get('watchers', []):
                        display_name = watcher.get('displayName', watcher.get('username', 'Unknown'))
                        if display_name:
                            watchers.append(display_name)
            else:
                # Watchers API might not be available in all Confluence versions
                # This is acceptable - return empty list
                pass

        except Exception:
            # Watchers might not be available - this is acceptable
            pass

        return watchers

    def _get_page_viewers(self, page_id: str) -> List[str]:
        """
        Get list of viewers for a page.

        IMPORTANT: Viewer data is ONLY available via Content Insights API, which requires:
        - Confluence Standard, Premium, or Enterprise subscription (not available in Free)
        - Content Insights feature enabled by administrator
        - User must have appropriate permissions
        
        If your Confluence instance doesn't have Content Insights enabled,
        this method will return an empty list. This is EXPECTED behavior and not an error.
        
        The standard Confluence REST API does not provide viewer/analytics data.

        Args:
            page_id: Page ID

        Returns:
            List of viewer display names (empty list if Content Insights not available)
        """
        viewers = []
        try:
            if requests is None:
                return viewers
            
            # Create basic auth header
            auth_string = f"{self.email}:{self.api_token}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            headers = {
                'Authorization': f'Basic {auth_b64}',
                'Accept': 'application/json'
            }
            
            # Try Content Insights API endpoint (available in Standard/Premium/Enterprise)
            # This endpoint may not be available in all Confluence instances
            try:
                url = f"{self.server}/wiki/rest/api/content/{page_id}/insights/viewers"
                response = requests.get(url, headers=headers, params={'limit': 1000}, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict):
                        results = data.get('results', [])
                        for viewer in results:
                            display_name = viewer.get('displayName') or viewer.get('username', 'Unknown')
                            if display_name and display_name != 'Unknown':
                                viewers.append(display_name)
                elif response.status_code in [403, 404]:
                    # 403 = Permission denied (Content Insights not enabled or no access)
                    # 404 = Endpoint not found (Content Insights not available)
                    # This is expected for most Confluence instances
                    pass
            except Exception:
                # Content Insights API not available - try alternative endpoint
                try:
                    # Alternative: Try analytics endpoint if available
                    url = f"{self.server}/wiki/rest/analytics/1.0/content/{page_id}/viewers"
                    response = requests.get(url, headers=headers, params={'limit': 1000}, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        if isinstance(data, dict):
                            results = data.get('results', [])
                            for viewer in results:
                                display_name = viewer.get('displayName') or viewer.get('username', 'Unknown')
                                if display_name and display_name != 'Unknown':
                                    viewers.append(display_name)
                except Exception:
                    # Analytics endpoint also not available - this is expected for most instances
                    pass

        except Exception:
            # Viewers are typically not available via standard API
            # This is expected behavior for most Confluence instances
            pass

        return viewers

    def _get_page_versions_count(self, page_id: str) -> int:
        """
        Get the number of versions for a page.

        Args:
            page_id: Page ID

        Returns:
            Number of versions
        """
        try:
            page = self.confluence.get_page_by_id(
                page_id,
                expand='version'
            )
            return page.get('version', {}).get('number', 1)

        except Exception as e:
            print(
                f"Warning: Error getting version count for page {page_id}: {e}",
                file=sys.stderr
            )
            return 1

    def _get_page_created_by(self, page_id: str) -> Optional[str]:
        """
        Get the display name of the user who created the page.

        Args:
            page_id: Page ID

        Returns:
            Display name of creator, or None if not available
        """
        try:
            page = self.confluence.get_page_by_id(
                page_id,
                expand='history'
            )
            history = page.get('history', {})
            created_by = history.get('createdBy', {})
            return created_by.get('displayName', None)

        except Exception as e:
            print(
                f"Warning: Error getting creator for page {page_id}: {e}",
                file=sys.stderr
            )
            return None

    def _get_page_updaters(self, page_id: str) -> List[str]:
        """
        Get list of all users who have updated the page (from version history).

        Args:
            page_id: Page ID

        Returns:
            List of unique updater display names
        """
        updaters = []
        try:
            if requests is None:
                return updaters

            # Create basic auth header
            auth_string = f"{self.email}:{self.api_token}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            headers = {
                'Authorization': f'Basic {auth_b64}',
                'Accept': 'application/json'
            }

            # Get page versions
            url = f"{self.server}/wiki/rest/api/content/{page_id}/version"
            response = requests.get(url, headers=headers, params={'limit': 1000}, timeout=10)

            if response.status_code == 200:
                data = response.json()
                versions = data.get('results', [])

                updater_set = set()
                for version in versions:
                    updater = version.get('by', {}).get('displayName', None)
                    if updater:
                        updater_set.add(updater)

                updaters = sorted(list(updater_set))

        except Exception as e:
            print(
                f"Warning: Error getting updaters for page {page_id}: {e}",
                file=sys.stderr
            )

        return updaters

    def _resolve_account_id_to_display_name(self, account_id: str) -> Optional[str]:
        """
        Resolve a Confluence account ID to a user display name.

        Args:
            account_id: Account ID (e.g., "712020:d3be99c3-d040-4901-ac85-c18b852c6ee7")

        Returns:
            Display name if found, None otherwise
        """
        try:
            if requests is None:
                return None

            # Create basic auth header
            auth_string = f"{self.email}:{self.api_token}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            headers = {
                'Authorization': f'Basic {auth_b64}',
                'Accept': 'application/json'
            }

            # Get user info
            url = f"{self.server}/wiki/rest/api/user"
            response = requests.get(
                url,
                headers=headers,
                params={'accountId': account_id},
                timeout=10
            )

            if response.status_code == 200:
                user_data = response.json()
                return user_data.get('displayName', None)

        except Exception:
            # Silently fail - account ID might not resolve
            pass

        return None

    def _replace_user_mentions_with_names(self, content: str) -> str:
        """
        Replace user mention tags in content with display names before Markdown conversion.
        
        This ensures that user mentions in tables and other content are preserved
        when html2text converts HTML to Markdown.
        
        Args:
            content: Page content in Confluence storage format (XML/HTML)
            
        Returns:
            Content with user mention tags replaced by display names
        """
        try:
            # Cache for resolved account IDs to avoid duplicate API calls
            account_id_cache = {}
            
            def get_display_name(account_id: str) -> str:
                """Get display name from cache or resolve it."""
                if account_id not in account_id_cache:
                    display_name = self._resolve_account_id_to_display_name(account_id)
                    account_id_cache[account_id] = display_name if display_name else ''
                return account_id_cache[account_id]
            
            # Pattern 1: <ac:link><ri:user ri:account-id="..."/></ac:link>
            # This is the most common format in tables
            def replace_user_link(match):
                full_match = match.group(0)
                account_id_match = re.search(r'ri:account-id="([^"]+)"', full_match)
                if account_id_match:
                    account_id = account_id_match.group(1)
                    display_name = get_display_name(account_id)
                    if display_name:
                        return display_name
                return ''  # Remove if can't resolve
            
            # Replace <ac:link><ri:user .../></ac:link> patterns first
            # Use non-greedy matching to handle nested tags correctly
            content = re.sub(
                r'<ac:link[^>]*>\s*<ri:user[^>]*ri:account-id="([^"]+)"[^>]*/>\s*</ac:link>',
                replace_user_link,
                content,
                flags=re.DOTALL
            )
            
            # Pattern 2: <ri:user ri:account-id="..."/> (standalone, not wrapped in ac:link)
            # Only match if not already inside an ac:link tag
            def replace_user_standalone(match):
                account_id = match.group(1)
                display_name = get_display_name(account_id)
                if display_name:
                    return display_name
                return ''  # Remove if can't resolve
            
            # Replace standalone <ri:user .../> patterns
            # We need to be careful not to match ones already inside ac:link
            # Process from right to left to avoid nested tag issues
            content = re.sub(
                r'<ri:user[^>]*ri:account-id="([^"]+)"[^>]*/>',
                replace_user_standalone,
                content
            )
            
            # Pattern 3: <ac:mention ac:account-id="..."/>
            def replace_mention(match):
                account_id = match.group(1)
                display_name = get_display_name(account_id)
                if display_name:
                    return display_name
                return ''  # Remove if can't resolve
            
            content = re.sub(
                r'<ac:mention[^>]*ac:account-id="([^"]+)"[^>]*/>',
                replace_mention,
                content
            )
            
        except Exception as e:
            print(
                f"Warning: Error replacing user mentions with names: {e}",
                file=sys.stderr
            )
        
        return content

    def _extract_user_mentions(self, content: str) -> List[str]:
        """
        Extract user mentions (tags) from Confluence storage format content.

        Args:
            content: Page content in Confluence storage format (XML/HTML)

        Returns:
            List of unique user display names mentioned in the content
        """
        mentioned_users = []
        try:
            # Extract account IDs from various mention formats
            account_ids = []

            # Format 1: <ri:user ri:account-id="..."/>
            account_ids.extend(re.findall(
                r'<ri:user[^>]*ri:account-id="([^"]+)"',
                content
            ))

            # Format 2: <ri:user ri:userkey="..."/> (legacy)
            userkeys = re.findall(
                r'<ri:user[^>]*ri:userkey="([^"]+)"',
                content
            )
            # Note: userkeys might need different resolution, but try account-id first

            # Format 3: <ac:mention ac:account-id="..."/>
            account_ids.extend(re.findall(
                r'<ac:mention[^>]*ac:account-id="([^"]+)"',
                content
            ))

            # Get unique account IDs
            unique_account_ids = list(set(account_ids))

            # Resolve account IDs to display names
            user_set = set()
            for account_id in unique_account_ids:
                display_name = self._resolve_account_id_to_display_name(account_id)
                if display_name:
                    user_set.add(display_name)

            mentioned_users = sorted(list(user_set))

        except Exception as e:
            print(
                f"Warning: Error extracting user mentions: {e}",
                file=sys.stderr
            )

        return mentioned_users

    def extract_pages(
        self,
        page_url: str,
        start_date: datetime,
        end_date: datetime,
        filter_labels: List[str]
    ) -> List[Dict]:
        """
        Extract pages from hierarchy matching labels and date range.

        Args:
            page_url: Starting page URL
            start_date: Start of date range
            end_date: End of date range
            filter_labels: List of labels to filter by

        Returns:
            List of page dictionaries with extracted data
        """
        # Extract page ID from URL
        page_id = self._extract_page_id_from_url(page_url)
        if not page_id:
            print("Error: Could not extract page ID from URL", file=sys.stderr)
            return []

        print(f"Starting page ID: {page_id}")
        print("Traversing page hierarchy...")

        # Get all descendants
        all_page_ids = self._get_all_descendants(page_id)
        print(f"Found {len(all_page_ids)} pages in hierarchy")

        matching_pages = []

        for pid in all_page_ids:
            try:
                # Get full page details
                page = self.confluence.get_page_by_id(
                    pid,
                    expand='body.storage,version,metadata.labels'
                )

                # Extract labels
                page_labels = [
                    label['name']
                    for label in page.get('metadata', {}).get('labels', {}).get('results', [])
                ]

                # Check label filter
                if not self._page_matches_labels(page_labels, filter_labels):
                    continue

                # Check date range
                created_date = page.get('version', {}).get('when', '')
                updated_date = page.get('version', {}).get('when', '')

                # Get creation date and creator from history if available
                created_by = None
                try:
                    # Try to get history via expand parameter
                    page_with_history = self.confluence.get_page_by_id(
                        pid,
                        expand='body.storage,version,metadata.labels,history'
                    )
                    history = page_with_history.get('history', {})
                    if history:
                        created_date = history.get('createdDate', created_date)
                        created_by_obj = history.get('createdBy', {})
                        created_by = created_by_obj.get('displayName', None)
                except Exception:
                    # If history expand doesn't work, try separate call
                    try:
                        history = self.confluence.get_page_history(pid)
                        if history:
                            if history.get('createdBy'):
                                created_date = history.get('createdDate', created_date)
                                created_by_obj = history.get('createdBy', {})
                                created_by = created_by_obj.get('displayName', None)
                    except Exception:
                        pass

                if not self._page_in_date_range(created_date, updated_date, start_date, end_date):
                    continue

                # Extract all required data
                page_content = page.get('body', {}).get('storage', {}).get('value', '')
                
                page_data = {
                    'id': pid,
                    'title': page.get('title', 'Untitled'),
                    'url': f"{self.server}/wiki{page.get('_links', {}).get('webui', '')}",
                    'content': page_content,
                    'created': created_date,
                    'updated': updated_date,
                    'versions_count': self._get_page_versions_count(pid),
                    'created_by': created_by,
                    'updaters': self._get_page_updaters(pid),
                    'mentioned_users': self._extract_user_mentions(page_content),
                    'labels': page_labels,
                    'comments': self._get_page_comments(pid),
                    'reactions': self._get_page_reactions(pid),
                    'watchers': self._get_page_watchers(pid),
                    'viewers': self._get_page_viewers(pid),
                }

                matching_pages.append(page_data)
                print(f"  Matched: {page_data['title']}")

            except Exception as e:
                print(
                    f"Warning: Error processing page {pid}: {e}",
                    file=sys.stderr
                )
                continue

        print(f"\nFound {len(matching_pages)} pages matching criteria")
        return matching_pages

    def export_to_markdown(
        self,
        pages: List[Dict],
        output_path: Path
    ) -> None:
        """
        Export extracted pages to markdown format.

        Args:
            pages: List of page dictionaries
            output_path: Path to output file
        """
        lines = [
            "# Confluence Page Hierarchy Extract",
            "",
            f"**Total Pages:** {len(pages)}",
            "",
            "---",
            "",
        ]

        for idx, page in enumerate(pages, 1):
            lines.extend([
                f"## {idx}. {page['title']}",
                "",
                f"**URL:** [{page['url']}]({page['url']})",
                f"**Page ID:** {page['id']}",
                f"**Created:** {page['created'][:10] if page['created'] else 'N/A'}",
                f"**Created By:** {page.get('created_by', 'N/A')}",
                f"**Last Updated:** {page['updated'][:10] if page['updated'] else 'N/A'}",
                f"**Versions:** {page['versions_count']}",
                "",
            ])

            # Display updaters
            updaters = page.get('updaters', [])
            if updaters:
                updaters_str = ", ".join(updaters)
                lines.extend([
                    f"**Updated By:** {updaters_str}",
                    "",
                ])
            else:
                lines.extend([
                    "**Updated By:** N/A",
                    "",
                ])

            # Display mentioned users
            mentioned_users = page.get('mentioned_users', [])
            if mentioned_users:
                mentioned_str = ", ".join(mentioned_users)
                lines.extend([
                    f"**Mentioned Users:** {mentioned_str}",
                    "",
                ])
            else:
                lines.extend([
                    "**Mentioned Users:** None",
                    "",
                ])

            # Labels
            if page['labels']:
                labels_str = ", ".join(page['labels'])
                lines.extend([
                    f"**Labels:** {labels_str}",
                    "",
                ])

            # Content
            lines.extend([
                "### Content",
                "",
            ])
            if page['content']:
                # Convert Confluence storage format (HTML) to markdown
                content = page['content']
                
                # Replace user mention tags with display names BEFORE converting to markdown
                # This ensures user names appear in tables and other content
                content = self._replace_user_mentions_with_names(content)
                
                if self.html_converter is not None:
                    # Use html2text to preserve formatting (headings, tables, emojis, etc.)
                    markdown_content = self.html_converter.handle(content)
                    lines.append(markdown_content)
                else:
                    # Fallback: basic HTML tag removal
                    content = re.sub(r'<[^>]+>', '', content)
                    lines.append(content)
            else:
                lines.append("*No content*")
            lines.append("")

            # Comments
            lines.extend([
                "### Comments",
                "",
            ])
            if page['comments']:
                for comment in page['comments']:
                    # Indent nested comments based on depth
                    indent = "  " * comment.get('depth', 0)
                    indent_prefix = "> " if comment.get('depth', 0) > 0 else ""
                    
                    lines.extend([
                        f"{indent}{indent_prefix}**{comment['author']}** ({comment['created'][:10] if comment['created'] else 'N/A'}):",
                        "",
                    ])
                    if comment['content']:
                        if self.html_converter is not None:
                            # Replace user mentions in comments before converting to markdown
                            comment_content = self._replace_user_mentions_with_names(comment['content'])
                            # Convert HTML to markdown preserving formatting
                            comment_markdown = self.html_converter.handle(comment_content)
                            # Indent each line of the comment
                            indented_lines = [
                                f"{indent}{indent_prefix}{line}" 
                                for line in comment_markdown.split('\n')
                            ]
                            lines.extend(indented_lines)
                        else:
                            # Fallback: basic HTML tag removal
                            comment_content = re.sub(r'<[^>]+>', '', comment['content'])
                            lines.append(f"{indent}{indent_prefix}{comment_content}")
                    else:
                        lines.append(f"{indent}{indent_prefix}*No content*")
                    lines.append("")
            else:
                lines.append("*No comments*")
            lines.append("")

            # Reactions
            if page['reactions']:
                lines.extend([
                    "### Reactions",
                    "",
                ])
                for reaction in page['reactions']:
                    # Format reactions if available
                    lines.append(str(reaction))
                lines.append("")

            # Watchers
            lines.extend([
                "### Watchers",
                "",
            ])
            if page['watchers']:
                for watcher in page['watchers']:
                    lines.append(f"- {watcher}")
            else:
                lines.append("*No watchers*")
            lines.append("")

            # Viewers
            lines.extend([
                "### Viewers",
                "",
            ])
            if page['viewers']:
                for viewer in page['viewers']:
                    lines.append(f"- {viewer}")
            else:
                lines.append("*No viewer data available*")
            lines.append("")

            lines.append("---")
            lines.append("")

        content = '\n'.join(lines)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Extract Confluence page hierarchy filtered by labels and date range'
    )
    parser.add_argument(
        '--server',
        default=os.getenv('CONFLUENCE_SERVER') or os.getenv('ATLASSIAN_SERVER'),
        help='Confluence server URL (e.g., https://your-domain.atlassian.net). '
             'Can also be set via CONFLUENCE_SERVER or ATLASSIAN_SERVER environment variable.'
    )
    parser.add_argument(
        '--email',
        default=os.getenv('CONFLUENCE_EMAIL') or os.getenv('ATLASSIAN_ACCESSOR_EMAIL') or os.getenv('ATLASSIAN_EMAIL'),
        help='Confluence user email. Can also be set via CONFLUENCE_EMAIL, '
             'ATLASSIAN_ACCESSOR_EMAIL, or ATLASSIAN_EMAIL environment variable.'
    )
    parser.add_argument(
        '--api-token',
        default=os.getenv('CONFLUENCE_API_TOKEN') or os.getenv('ATLASSIAN_API_TOKEN'),
        help='Confluence API token. Can also be set via CONFLUENCE_API_TOKEN or '
             'ATLASSIAN_API_TOKEN environment variable.'
    )
    parser.add_argument(
        '--page-url',
        required=True,
        help='Starting page URL'
    )
    parser.add_argument(
        '--start-date',
        required=True,
        help='Start date for filtering (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end-date',
        required=True,
        help='End date for filtering (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--labels',
        required=True,
        help='Comma-separated list of labels to filter by'
    )
    parser.add_argument(
        '--output',
        help='Output file path (default: auto-generated)'
    )

    args = parser.parse_args()

    # Validate required credentials
    if not args.server:
        print(
            "Error: Confluence server URL is required. "
            "Provide via --server argument, CONFLUENCE_SERVER, or ATLASSIAN_SERVER environment variable.",
            file=sys.stderr
        )
        sys.exit(1)

    if not args.email:
        print(
            "Error: Confluence email is required. "
            "Provide via --email argument, CONFLUENCE_EMAIL, ATLASSIAN_ACCESSOR_EMAIL, "
            "or ATLASSIAN_EMAIL environment variable.",
            file=sys.stderr
        )
        sys.exit(1)

    if not args.api_token:
        print(
            "Error: Confluence API token is required. "
            "Provide via --api-token argument, CONFLUENCE_API_TOKEN, or ATLASSIAN_API_TOKEN environment variable.",
            file=sys.stderr
        )
        sys.exit(1)

    return args


def main():
    """Main function."""
    args = parse_arguments()

    # Parse dates
    try:
        start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
    except ValueError:
        print("Error: Invalid start date format. Use YYYY-MM-DD format.", file=sys.stderr)
        sys.exit(1)

    try:
        end_date = datetime.strptime(args.end_date, '%Y-%m-%d')
    except ValueError:
        print("Error: Invalid end date format. Use YYYY-MM-DD format.", file=sys.stderr)
        sys.exit(1)

    # Parse labels
    filter_labels = [label.strip() for label in args.labels.split(',') if label.strip()]

    if not filter_labels:
        print("Error: At least one label must be provided.", file=sys.stderr)
        sys.exit(1)

    # Generate date strings for filename (YYYYMMDD format)
    start_date_str = start_date.strftime('%Y%m%d')
    end_date_str = end_date.strftime('%Y%m%d')

    # Generate default output path if not provided
    if args.output:
        output_path = Path(args.output)
    else:
        # Extract page identifier from URL for default path
        # Try to get page title from URL (last part after /pages/)
        page_identifier = 'PageHierarchy'
        if '/pages/' in args.page_url:
            # Extract the part after /pages/ which contains page ID and title
            parts = args.page_url.split('/pages/')
            if len(parts) > 1:
                page_part = parts[1]
                # Extract page ID (numeric) if available
                page_id_match = re.search(r'/(\d+)/', page_part)
                if page_id_match:
                    page_id = page_id_match.group(1)
                    # Try to extract title from URL (after page ID)
                    title_part = page_part.split(f'/{page_id}/')
                    if len(title_part) > 1:
                        title = title_part[1].split('?')[0]  # Remove query params
                        # Clean title: replace + with -, remove special chars
                        title_clean = re.sub(r'[^\w\-]', '-', title.replace('+', '-'))
                        page_identifier = title_clean[:50]  # Limit length
                    else:
                        page_identifier = f"Page-{page_id}"
        
        output_path = Path(
            f"data/domains/confluence/"
            f"Confluence-PageHierarchy-{page_identifier}-{start_date_str}-{end_date_str}.md"
        )

    print("=== Confluence Page Hierarchy Extraction ===")
    print(f"Server: {args.server}")
    print(f"Page URL: {args.page_url}")
    print(f"Date Range: {args.start_date} to {args.end_date}")
    print(f"Labels: {', '.join(filter_labels)}")
    print(f"Output: {output_path}\n")

    # Create extractor
    extractor = ConfluencePageHierarchyExtractor(
        server=args.server,
        email=args.email,
        api_token=args.api_token
    )

    # Extract pages
    pages = extractor.extract_pages(
        page_url=args.page_url,
        start_date=start_date,
        end_date=end_date,
        filter_labels=filter_labels
    )

    # Export to markdown
    if pages:
        extractor.export_to_markdown(pages, output_path)
        print(f"\nSuccessfully generated {output_path}")
    else:
        print("\nNo pages matched the criteria.")


if __name__ == "__main__":
    main()

