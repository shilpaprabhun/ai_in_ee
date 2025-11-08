"""
AWS CodeDeploy Deployment Data Extractor

This script extracts deployment data from AWS CodeDeploy across multiple accounts
and environments (dev, test, prod, demo) for a specified bounded context and time period.
"""

import boto3
import yaml
from datetime import datetime
from typing import List, Dict, Optional
import argparse
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass
class DeploymentRecord:
    """Data class representing a single deployment record"""
    deployment_id: str
    account_name: str
    account_id: str
    environment: str
    application_name: str
    deployment_group: str
    create_time: datetime
    complete_time: Optional[datetime]
    status: str
    description: str
    commit_ids: List[str]
    pr_ids: List[str]
    error_summary: Optional[str]

    def to_markdown_row(self) -> List[str]:
        """Convert to markdown table row cells"""
        return [
            self.deployment_id,
            self.account_name,
            self.account_id,
            self.environment,
            self.application_name,
            self.deployment_group,
            self.create_time.strftime('%Y-%m-%d %H:%M:%S') if self.create_time else '',
            self.complete_time.strftime('%Y-%m-%d %H:%M:%S') if self.complete_time else '',
            self.status,
            self.description[:50] + '...' if len(self.description) > 50 else self.description,
            ', '.join(self.commit_ids) if self.commit_ids else '-',
            ', '.join(self.pr_ids) if self.pr_ids else '-',
            (self.error_summary[:100] + '...') if self.error_summary and len(self.error_summary) > 100 else (self.error_summary or '-')
        ]


class CodeDeployExtractor:
    """Extract deployment data from AWS CodeDeploy"""

    def __init__(self, config_path: str = 'context/infra/aws-accounts.yaml'):
        """
        Initialize the extractor

        Args:
            config_path: Path to the AWS accounts configuration file
        """
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load and parse the AWS accounts configuration"""
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def get_accounts_for_bc(self, domain_name: str, bc_name: str) -> List[Dict]:
        """
        Get AWS accounts for a specific bounded context

        Args:
            domain_name: Name of the domain
            bc_name: Name of the bounded context

        Returns:
            List of AWS account configurations
        """
        for domain in self.config.get('domains', []):
            if domain['name'] == domain_name:
                for bc in domain.get('bounded_contexts', []):
                    if bc['name'] == bc_name:
                        return bc.get('aws_accounts', [])
        return []

    def create_session(self, account_id: str, region: str = 'us-east-1') -> boto3.Session:
        """
        Create an AWS session for the specified account

        Args:
            account_id: AWS account ID
            region: AWS region

        Returns:
            boto3.Session object
        """
        # Try profile-based access first (format: PowerUserAccess-{account_id} or ReadOnlyAccessNew-{account_id})
        for role_name in ['PowerUserAccess', 'ReadOnlyAccessNew']:
            profile_name = f"{role_name}-{account_id}"
            try:
                session = boto3.Session(profile_name=profile_name, region_name=region)
                # Try to verify the session works
                try:
                    sts = session.client('sts')
                    identity = sts.get_caller_identity()
                    # If account ID matches, use this session
                    if identity.get('Account') == account_id:
                        return session
                    # If account ID doesn't match but session works, still use it
                    return session
                except Exception:
                    # If verification fails but profile exists, still try using it
                    # It might work for the actual operations
                    return session
            except Exception:
                continue

        # Fall back to assume role
        try:
            sts_client = boto3.client('sts')
            assumed_role = sts_client.assume_role(
                RoleArn=f"arn:aws:iam::{account_id}:role/DataExtractionRole",
                RoleSessionName=f"codedeploy-extraction-{account_id}"
            )

            return boto3.Session(
                aws_access_key_id=assumed_role['Credentials']['AccessKeyId'],
                aws_secret_access_key=assumed_role['Credentials']['SecretAccessKey'],
                aws_session_token=assumed_role['Credentials']['SessionToken'],
                region_name=region
            )
        except Exception as e:
            print(f"Warning: Could not assume role for account {account_id}: {e}")
            # Fall back to default session (useful if running in the same account)
            return boto3.Session(region_name=region)

    def extract_commit_and_pr_info(self, revision_info: Dict) -> tuple[List[str], List[str]]:
        """
        Extract commit IDs and PR IDs from revision information

        Args:
            revision_info: CodeDeploy revision information

        Returns:
            Tuple of (commit_ids, pr_ids)
        """
        commit_ids = []
        pr_ids = []

        if not revision_info:
            return commit_ids, pr_ids

        # Extract from GitHub revision
        if revision_info.get('revisionType') == 'GitHub':
            location = revision_info.get('gitHubLocation', {})
            commit_id = location.get('commitId')
            if commit_id:
                commit_ids.append(commit_id)

            # Try to extract PR ID from description or commit message
            repository = location.get('repository', '')

        # Extract from S3 revision (may contain metadata)
        elif revision_info.get('revisionType') == 'S3':
            location = revision_info.get('s3Location', {})
            # S3 revisions might have commit info in metadata

        # Look for PR IDs in description
        description = revision_info.get('description', '')
        pr_matches = re.findall(r'#(\d+)', description)
        pr_ids.extend(pr_matches)

        return commit_ids, pr_ids

    def get_deployments(
        self,
        account_id: str,
        account_name: str,
        environment: str,
        start_date: datetime,
        end_date: datetime,
        region: str = 'us-east-1'
    ) -> List[DeploymentRecord]:
        """
        Get all deployments for an account within the date range

        Args:
            account_id: AWS account ID
            account_name: Human-readable account name
            environment: Environment type (dev, test, prod, demo)
            start_date: Start of date range
            end_date: End of date range
            region: AWS region

        Returns:
            List of DeploymentRecord objects
        """
        records = []

        try:
            session = self.create_session(account_id, region)
            codedeploy = session.client('codedeploy')

            # Get all applications
            applications_response = codedeploy.list_applications()
            applications = applications_response.get('applications', [])

            for app_name in applications:
                print(f"Processing application: {app_name} in {account_name}")

                # Get deployment groups for this application
                groups_response = codedeploy.list_deployment_groups(applicationName=app_name)
                deployment_groups = groups_response.get('deploymentGroups', [])

                for group_name in deployment_groups:
                    # List deployments for this group
                    deployments_response = codedeploy.list_deployments(
                        applicationName=app_name,
                        deploymentGroupName=group_name,
                        createTimeRange={
                            'start': start_date,
                            'end': end_date
                        }
                    )

                    deployment_ids = deployments_response.get('deployments', [])

                    # Get detailed information for each deployment
                    if deployment_ids:
                        batch_response = codedeploy.batch_get_deployments(
                            deploymentIds=deployment_ids
                        )

                        for deployment in batch_response.get('deploymentsInfo', []):
                            # Extract commit and PR information
                            commit_ids, pr_ids = self.extract_commit_and_pr_info(
                                deployment.get('revision')
                            )

                            # Extract error information if deployment failed
                            error_summary = None
                            if deployment.get('status') in ['Failed', 'Stopped']:
                                error_info = deployment.get('errorInformation', {})
                                error_summary = error_info.get('message', 'No error details available')

                            record = DeploymentRecord(
                                deployment_id=deployment.get('deploymentId', ''),
                                account_name=account_name,
                                account_id=account_id,
                                environment=environment,
                                application_name=app_name,
                                deployment_group=group_name,
                                create_time=deployment.get('createTime'),
                                complete_time=deployment.get('completeTime'),
                                status=deployment.get('status', 'Unknown'),
                                description=deployment.get('description', ''),
                                commit_ids=commit_ids,
                                pr_ids=pr_ids,
                                error_summary=error_summary
                            )

                            records.append(record)

        except Exception as e:
            print(f"Error processing account {account_name} ({account_id}): {e}")

        return records

    def extract_for_bounded_context(
        self,
        domain_name: str,
        bc_name: str,
        start_date: datetime,
        end_date: datetime,
        regions: List[str] = ['us-east-1'],
        account_ids: Optional[List[str]] = None
    ) -> List[DeploymentRecord]:
        """
        Extract deployments for a specific bounded context across all accounts

        Args:
            domain_name: Name of the domain
            bc_name: Name of the bounded context
            start_date: Start of date range
            end_date: End of date range
            regions: List of AWS regions to check
            account_ids: Optional list of account IDs to filter by

        Returns:
            List of all deployment records
        """
        accounts = self.get_accounts_for_bc(domain_name, bc_name)

        if not accounts:
            print(f"No accounts found for {domain_name}/{bc_name}")
            return []

        # Filter accounts if account_ids provided
        if account_ids:
            accounts = [acc for acc in accounts if acc.get('account_id') in account_ids]
            if not accounts:
                print(f"No accounts found matching the specified account IDs: {account_ids}")
                return []

        all_records = []

        for account in accounts:
            account_id = account.get('account_id')
            account_name = account.get('name')
            environment = account.get('type')

            print(f"\nProcessing {environment} account: {account_name} ({account_id})")

            for region in regions:
                records = self.get_deployments(
                    account_id=account_id,
                    account_name=account_name,
                    environment=environment,
                    start_date=start_date,
                    end_date=end_date,
                    region=region
                )
                all_records.extend(records)

        # Sort by create_time descending
        all_records.sort(key=lambda x: x.create_time if x.create_time else datetime.min, reverse=True)

        return all_records

    def export_to_markdown(self, records: List[DeploymentRecord], output_path: str):
        """
        Export deployment records to Markdown table

        Args:
            records: List of deployment records
            output_path: Path to output Markdown file
        """
        if not records:
            print("No records to export")
            return

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        headers = [
            'Deployment ID', 'Account Name', 'Account ID', 'Environment',
            'Application Name', 'Deployment Group', 'Create Time', 'Complete Time',
            'Status', 'Description', 'Commit IDs', 'PR IDs', 'Error Summary'
        ]

        with open(output_file, 'w') as f:
            # Write header
            f.write('| ' + ' | '.join(headers) + ' |\n')
            # Write separator
            f.write('| ' + ' | '.join(['---'] * len(headers)) + ' |\n')

            # Write data rows
            for record in records:
                cells = record.to_markdown_row()
                # Escape pipe characters in cell content
                escaped_cells = [str(cell).replace('|', '\\|').replace('\n', ' ') for cell in cells]
                f.write('| ' + ' | '.join(escaped_cells) + ' |\n')

        print(f"\nExported {len(records)} records to {output_path}")


def parse_arguments():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Extract AWS CodeDeploy deployment data for a bounded context',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract HR/Worker deployments for May-Oct 2025
  python codedeploy_extractor.py --domain HR --bc Worker --start-date 2025-05-01 --end-date 2025-10-31

  # Specify custom output file and multiple regions
  python codedeploy_extractor.py --domain HR --bc Worker --start-date 2025-05-01 --end-date 2025-10-31 \\
      --output data/deployments.md --regions us-east-1 us-west-2
        """
    )

    parser.add_argument(
        '--domain',
        required=True,
        help='Domain name (e.g., HR, Compliance)'
    )

    parser.add_argument(
        '--bc',
        required=True,
        help='Bounded Context name (e.g., Worker, Global Compliance Engine)'
    )

    parser.add_argument(
        '--start-date',
        required=True,
        help='Start date in YYYY-MM-DD format'
    )

    parser.add_argument(
        '--end-date',
        required=True,
        help='End date in YYYY-MM-DD format'
    )

    parser.add_argument(
        '--output',
        default=None,
        help='Output markdown file path (default: data/<domain>_<bc>_deployments.md)'
    )

    parser.add_argument(
        '--regions',
        nargs='+',
        default=['us-east-1'],
        help='AWS regions to query (default: us-east-1)'
    )

    parser.add_argument(
        '--account-ids',
        nargs='+',
        default=None,
        help='Filter to specific AWS account ID(s) (optional)'
    )

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_arguments()

    # Parse dates
    try:
        start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
        end_date = datetime.strptime(args.end_date, '%Y-%m-%d')
    except ValueError as e:
        print(f"Error: Invalid date format. Use YYYY-MM-DD format.")
        print(f"Details: {e}")
        return 1

    # Generate default output path if not provided
    if args.output is None:
        domain_clean = args.domain.lower().replace(' ', '_')
        bc_clean = args.bc.lower().replace(' ', '_')
        output_path = f"data/{domain_clean}_{bc_clean}_deployments.md"
    else:
        output_path = args.output

    print(f"=== AWS CodeDeploy Data Extraction ===")
    print(f"Domain: {args.domain}")
    print(f"Bounded Context: {args.bc}")
    print(f"Date Range: {start_date.date()} to {end_date.date()}")
    print(f"Regions: {', '.join(args.regions)}")
    print(f"Output: {output_path}\n")

    # Create extractor and run
    extractor = CodeDeployExtractor()

    # Extract deployments
    records = extractor.extract_for_bounded_context(
        domain_name=args.domain,
        bc_name=args.bc,
        start_date=start_date,
        end_date=end_date,
        regions=args.regions,
        account_ids=args.account_ids
    )

    # Print summary
    print(f"\n=== Summary ===")
    print(f"Total deployments found: {len(records)}")

    if records:
        status_counts = {}
        for record in records:
            status_counts[record.status] = status_counts.get(record.status, 0) + 1

        print("\nDeployments by status:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")

    # Export to Markdown
    extractor.export_to_markdown(records, output_path)

    return 0


if __name__ == '__main__':
    main()
