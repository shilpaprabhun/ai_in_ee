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

    def _get_page_comments(self, page_id: str) -> List[Dict]:
        """
        Get all comments for a page.

        Args:
            page_id: Page ID

        Returns:
            List of comment dictionaries
        """
        comments = []
        try:
            # Try to get comments using get_page_child_by_type
            try:
                result = self.confluence.get_page_child_by_type(
                    page_id,
                    type='comment',
                    start=0,
                    limit=1000,
                    expand='body.storage,version'
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
                    }
                    comments.append(comment_data)

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

        Note: Viewers are only available via Content Insights API, which requires
        Confluence Standard, Premium, or Enterprise subscription with Content Insights enabled.
        This method attempts to retrieve them but may return an empty list if not available.

        Args:
            page_id: Page ID

        Returns:
            List of viewer display names (may be empty if not available)
        """
        viewers = []
        try:
            import requests
            
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

        except ImportError:
            # requests library not available - skip viewer retrieval
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

                # Get creation date from history if available
                try:
                    # Try to get history via expand parameter
                    page_with_history = self.confluence.get_page_by_id(
                        pid,
                        expand='body.storage,version,metadata.labels,history'
                    )
                    history = page_with_history.get('history', {})
                    if history:
                        created_date = history.get('createdDate', created_date)
                except Exception:
                    # If history expand doesn't work, try separate call
                    try:
                        history = self.confluence.get_page_history(pid)
                        if history and history.get('createdBy'):
                            created_date = history.get('createdDate', created_date)
                    except Exception:
                        pass

                if not self._page_in_date_range(created_date, updated_date, start_date, end_date):
                    continue

                # Extract all required data
                page_data = {
                    'id': pid,
                    'title': page.get('title', 'Untitled'),
                    'url': f"{self.server}/wiki{page.get('_links', {}).get('webui', '')}",
                    'content': page.get('body', {}).get('storage', {}).get('value', ''),
                    'created': created_date,
                    'updated': updated_date,
                    'versions_count': self._get_page_versions_count(pid),
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
                f"**Last Updated:** {page['updated'][:10] if page['updated'] else 'N/A'}",
                f"**Versions:** {page['versions_count']}",
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
                # Convert Confluence storage format to plain text/markdown
                # This is a simplified conversion - may need HTML parsing
                content = page['content']
                # Remove HTML tags for basic display
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
                    lines.extend([
                        f"**{comment['author']}** ({comment['created'][:10] if comment['created'] else 'N/A'}):",
                        "",
                    ])
                    if comment['content']:
                        comment_content = re.sub(r'<[^>]+>', '', comment['content'])
                        lines.append(comment_content)
                    else:
                        lines.append("*No content*")
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

