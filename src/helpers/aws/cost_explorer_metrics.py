import boto3
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from pathlib import Path


class CostExplorerMetrics:
    def __init__(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime,
        region: str = 'us-east-1'
    ):
        """
        Initialize the Cost Explorer Metrics class.
        
        Args:
            account_id: AWS account ID
            start_date: Start date for metrics collection
            end_date: End date for metrics collection
            region: AWS region (not used by Cost Explorer, but kept for consistency)
        """
        self.account_id = account_id
        self.region = region
        self.start_date = start_date
        self.end_date = end_date
        
        # Create AWS session for the specified account
        self.session = self._create_session(account_id, region)
        
        # Initialize AWS Cost Explorer client (global service, no region needed)
        self.cost_explorer_client = self.session.client('ce', region_name='us-east-1')
    
    def _create_session(self, account_id: str, region: str = 'us-east-1') -> boto3.Session:
        """
        Create an AWS session for the specified account.
        
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
                RoleSessionName=f"cost-explorer-{account_id}"
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
    
    def get_cost_and_usage_by_service(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, float]:
        """
        Retrieve cost and usage data grouped by service for a given time period.
        
        Args:
            start_time: Start time for the cost query
            end_time: End time for the cost query
            
        Returns:
            Dictionary mapping service names to their costs
        """
        try:
            # Format dates for Cost Explorer API (YYYY-MM-DD)
            start_date_str = start_time.strftime('%Y-%m-%d')
            end_date_str = end_time.strftime('%Y-%m-%d')
            
            # Get cost and usage data grouped by service
            response = self.cost_explorer_client.get_cost_and_usage(
                TimePeriod={
                    'Start': start_date_str,
                    'End': end_date_str
                },
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                GroupBy=[
                    {
                        'Type': 'DIMENSION',
                        'Key': 'SERVICE'
                    }
                ]
            )
            
            # Parse the response and extract service costs
            service_costs = {}
            
            for result in response.get('ResultsByTime', []):
                for group in result.get('Groups', []):
                    service_name = group.get('Keys', [None])[0]
                    if service_name:
                        # Extract cost amount (stored as string in Cost Explorer)
                        cost_amount = group.get('Metrics', {}).get('UnblendedCost', {}).get('Amount', '0')
                        try:
                            cost_value = float(cost_amount)
                            # Sum costs if service appears multiple times
                            service_costs[service_name] = service_costs.get(service_name, 0) + cost_value
                        except (ValueError, TypeError):
                            continue
            
            return service_costs
            
        except Exception as e:
            print(f"Error getting cost and usage data: {e}")
            return {}
    
    def get_monthly_metrics(self) -> Dict[str, Dict]:
        """
        Retrieve cost metrics for each month in the specified date range.
        
        Returns:
            Dictionary with metrics for each month (key is month label like 'Oct-25')
        """
        monthly_metrics = {}
        
        # Generate month ranges from start_date to end_date
        current_date = datetime(self.start_date.year, self.start_date.month, 1)
        end_date_month = datetime(self.end_date.year, self.end_date.month, 1)
        
        while current_date <= end_date_month:
            # Calculate month start and end
            month_start = current_date
            if current_date.month == 12:
                month_end = datetime(current_date.year + 1, 1, 1)
            else:
                month_end = datetime(current_date.year, current_date.month + 1, 1)
            
            # Adjust month_start and month_end to be within the requested date range
            if month_start < self.start_date:
                month_start = self.start_date
            if month_end > self.end_date:
                month_end = self.end_date + timedelta(days=1)  # Include the end date
            
            # Skip if the month range is invalid
            if month_start >= month_end:
                # Move to next month
                if current_date.month == 12:
                    current_date = datetime(current_date.year + 1, 1, 1)
                else:
                    current_date = datetime(current_date.year, current_date.month + 1, 1)
                continue
            
            # Get cost data for this month
            service_costs = self.get_cost_and_usage_by_service(month_start, month_end)
            
            # Calculate total cost
            total_cost = sum(service_costs.values())
            
            # Sort services by cost (descending)
            sorted_services = sorted(
                service_costs.items(),
                key=lambda x: x[1],
                reverse=True
            )
            
            # Extract top 3 services
            top_service = sorted_services[0] if len(sorted_services) > 0 else (None, 0)
            second_service = sorted_services[1] if len(sorted_services) > 1 else (None, 0)
            third_service = sorted_services[2] if len(sorted_services) > 2 else (None, 0)
            
            # Get remaining services (all except top 3)
            remaining_services = sorted_services[3:] if len(sorted_services) > 3 else []
            
            # Create month key (e.g., 'Oct-25')
            month_key = current_date.strftime('%b-%y')
            
            monthly_metrics[month_key] = {
                'total_cost': total_cost,
                'top_service': top_service,
                'second_service': second_service,
                'third_service': third_service,
                'remaining_services': remaining_services
            }
            
            # Move to next month
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)
        
        return monthly_metrics
    
    def format_service_cost(self, service_name: str, cost: float) -> str:
        """
        Format service name and cost for display.
        
        Args:
            service_name: Name of the AWS service
            cost: Cost amount
            
        Returns:
            Formatted string: "Service Name ($X.XX)"
        """
        if service_name is None or cost == 0:
            return "-"
        return f"{service_name} (${cost:.2f})"
    
    def format_remaining_services(self, remaining_services: List[Tuple[str, float]]) -> str:
        """
        Format remaining services list as a bulleted list for display.
        
        Args:
            remaining_services: List of tuples (service_name, cost)
            
        Returns:
            Formatted HTML bulleted list string with all remaining services (excluding zero-cost services)
        """
        if not remaining_services:
            return "-"
        
        # Filter out services with zero cost (using threshold to exclude services that round to $0.00)
        non_zero_services = [(name, cost) for name, cost in remaining_services if cost >= 0.01]
        
        if not non_zero_services:
            return "-"
        
        # Format as HTML bulleted list for markdown table compatibility
        bullet_items = [
            f"<li>{name} (${cost:.2f})</li>"
            for name, cost in non_zero_services
        ]
        
        formatted = f"<ul>{''.join(bullet_items)}</ul>"
        
        return formatted
    
    def generate_markdown_report(self, monthly_metrics: Dict[str, Dict]) -> str:
        """
        Generate a markdown report from the collected metrics.
        
        Args:
            monthly_metrics: Dictionary with metrics for each month
            
        Returns:
            Markdown formatted string
        """
        markdown = "# Cost Explorer\n\n"
        
        # Table header
        markdown += "| Month | Total | Highest spend | 2nd highest spend | 3rd highest spend | All services |\n"
        markdown += "|-------|-------|---------------|-------------------|-------------------|-------------|\n"
        
        # Process all periods - sort months in descending order (newest first)
        def sort_month_key(month_str: str):
            try:
                return datetime.strptime(month_str, '%b-%y')
            except ValueError:
                # Fallback for non-month keys
                return datetime.min
        
        for month_key in sorted(monthly_metrics.keys(), key=sort_month_key, reverse=True):
            data = monthly_metrics[month_key]
            
            # Format values
            total_cost = f"${data['total_cost']:.2f}"
            highest_spend = self.format_service_cost(
                data['top_service'][0],
                data['top_service'][1]
            )
            second_highest = self.format_service_cost(
                data['second_service'][0],
                data['second_service'][1]
            )
            third_highest = self.format_service_cost(
                data['third_service'][0],
                data['third_service'][1]
            )
            all_services = self.format_remaining_services(data['remaining_services'])
            
            markdown += f"| {month_key} | {total_cost} | {highest_spend} | {second_highest} | {third_highest} | {all_services} |\n"
        
        markdown += "\n"
        
        return markdown
    
    def generate_cost_report(self, output_path: str):
        """
        Generate the complete cost report for the specified date range.
        
        Args:
            output_path: Full path for the output markdown file
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"Processing account: {self.account_id}")
        print(f"Date range: {self.start_date.date()} to {self.end_date.date()}")
        print("Retrieving cost and usage data...")
        
        monthly_metrics = self.get_monthly_metrics()
        
        print(f"Found cost data for {len(monthly_metrics)} month(s)")
        
        print("Generating markdown report...")
        markdown_content = self.generate_markdown_report(monthly_metrics)
        
        with open(output_file, 'w') as f:
            f.write(markdown_content)
        
        print(f"Cost report generated: {output_path}")


def parse_arguments():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Extract AWS Cost Explorer metrics for specified accounts and date range',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract cost metrics for Oct 2025
  python cost_explorer_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 --account-ids 205382370719 975050219213

  # Specify custom output directory
  python cost_explorer_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 \\
      --account-ids 205382370719 --output-dir data/domains/hr/worker/aws/

  # Extract cost metrics for multiple months
  python cost_explorer_metrics.py --start-date 2025-10-01 --end-date 2025-12-31 \\
      --account-ids 205382370719
        """
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
        '--account-ids',
        required=True,
        nargs='+',
        help='AWS account ID(s) to process'
    )
    
    parser.add_argument(
        '--output-dir',
        default=None,
        help='Output directory for generated files (default: current directory)'
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
    
    if start_date > end_date:
        print("Error: Start date must be before end date.")
        return 1
    
    # Generate date strings for filename (YYYYMMDD format)
    start_date_str = start_date.strftime('%Y%m%d')
    end_date_str = end_date.strftime('%Y%m%d')
    
    # Default output directory
    if args.output_dir is None:
        output_dir = Path('.')
    else:
        output_dir = Path(args.output_dir)
    
    print(f"=== AWS Cost Explorer Metrics Extraction ===")
    print(f"Date Range: {start_date.date()} to {end_date.date()}")
    print(f"Accounts: {', '.join(args.account_ids)}")
    print()
    
    # Process each account - generate one file per account
    for account_id in args.account_ids:
        try:
            print(f"\n{'='*60}")
            print(f"Processing Account: {account_id}")
            print(f"{'='*60}\n")
            
            # Create metrics collector for this account
            metrics_collector = CostExplorerMetrics(
                account_id=account_id,
                start_date=start_date,
                end_date=end_date
            )
            
            # Generate output filename
            # Format: CostExplorer-{Account-ID}-{start-date}-{end-date}.md
            filename = f"CostExplorer-{account_id}-{start_date_str}-{end_date_str}.md"
            
            output_path = output_dir / filename
            
            # Generate report
            metrics_collector.generate_cost_report(str(output_path))
        
        except Exception as e:
            print(f"Error processing account {account_id}: {e}")
            continue
    
    print("\n=== Extraction Complete ===")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

