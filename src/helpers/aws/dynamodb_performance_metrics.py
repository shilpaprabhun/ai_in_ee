import boto3
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import statistics
from pathlib import Path


class DynamoDBPerformanceMetrics:
    def __init__(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime,
        region: str = 'us-east-1'
    ):
        """
        Initialize the DynamoDB Performance Metrics class.
        
        Args:
            account_id: AWS account ID
            start_date: Start date for metrics collection
            end_date: End date for metrics collection
            region: AWS region to scan (default: us-east-1)
        """
        self.account_id = account_id
        self.region = region
        self.start_date = start_date
        self.end_date = end_date
        
        # Create AWS session for the specified account
        self.session = self._create_session(account_id, region)
        
        # Initialize AWS clients
        self.dynamodb_client = self.session.client('dynamodb', region_name=region)
        self.cloudwatch_client = self.session.client('cloudwatch', region_name=region)
    
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
                RoleSessionName=f"dynamodb-metrics-{account_id}"
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
    
    def get_all_tables(self) -> List[str]:
        """
        Retrieve all DynamoDB table names from the AWS account.
        
        Returns:
            List of table names
        """
        table_names = []
        
        try:
            paginator = self.dynamodb_client.get_paginator('list_tables')
            for page in paginator.paginate():
                table_names.extend(page.get('TableNames', []))
        except Exception as e:
            print(f"Error listing DynamoDB tables: {e}")
        
        return table_names
    
    def get_table_operations(self, table_name: str) -> List[str]:
        """
        Discover all operation types for a given table by querying CloudWatch metrics.
        
        Args:
            table_name: Name of the DynamoDB table
            
        Returns:
            List of operation types (e.g., ['GetItem', 'PutItem', 'Query'])
        """
        operations = set()
        
        try:
            # Query CloudWatch to discover operations for this table
            response = self.cloudwatch_client.list_metrics(
                Namespace='AWS/DynamoDB',
                Dimensions=[
                    {'Name': 'TableName', 'Value': table_name}
                ]
            )
            
            for metric in response.get('Metrics', []):
                for dim in metric.get('Dimensions', []):
                    if dim['Name'] == 'Operation':
                        operations.add(dim['Value'])
            
            # Handle pagination
            while 'NextToken' in response:
                response = self.cloudwatch_client.list_metrics(
                    Namespace='AWS/DynamoDB',
                    Dimensions=[
                        {'Name': 'TableName', 'Value': table_name}
                    ],
                    NextToken=response['NextToken']
                )
                
                for metric in response.get('Metrics', []):
                    for dim in metric.get('Dimensions', []):
                        if dim['Name'] == 'Operation':
                            operations.add(dim['Value'])
        except Exception as e:
            print(f"Error discovering operations for table {table_name}: {e}")
        
        # Common operations to check if none found
        if not operations:
            common_operations = [
                'GetItem', 'PutItem', 'UpdateItem', 'DeleteItem',
                'BatchGetItem', 'BatchWriteItem', 'Query', 'Scan',
                'TransactGetItems', 'TransactWriteItems'
            ]
            return common_operations
        
        return sorted(list(operations))
    
    def get_cloudwatch_metric_data(
        self,
        table_name: str,
        operation: Optional[str],
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
        statistic: str,
        use_operation_dimension: bool = True
    ) -> List[Dict]:
        """
        Retrieve CloudWatch metric data for a specific DynamoDB table and operation.
        
        Args:
            table_name: Name of the DynamoDB table
            operation: Operation type (optional, None for all operations)
            metric_name: CloudWatch metric name
            start_time: Start time for the metric query
            end_time: End time for the metric query
            statistic: Statistic type (Average, Maximum, Minimum, Sum)
            use_operation_dimension: Whether to include Operation dimension (default: True)
                Set to False for table-level metrics like ConsumedReadCapacityUnits
            
        Returns:
            List of metric datapoints
        """
        try:
            dimensions = [{'Name': 'TableName', 'Value': table_name}]
            
            # Add operation dimension if specified and metric supports it
            # Capacity units metrics don't support Operation dimension
            if operation and use_operation_dimension:
                dimensions.append({'Name': 'Operation', 'Value': operation})
            
            response = self.cloudwatch_client.get_metric_statistics(
                Namespace='AWS/DynamoDB',
                MetricName=metric_name,
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # 1 day
                Statistics=[statistic]
            )
            return response['Datapoints']
        except Exception as e:
            operation_desc = operation if operation else "all operations"
            print(f"Error getting {metric_name} for {table_name}/{operation_desc}: {e}")
            return []
    
    def calculate_percentiles(self, values: List[float]) -> Dict[str, float]:
        """
        Calculate percentiles for a list of values.
        
        Args:
            values: List of numeric values
            
        Returns:
            Dictionary with p50, p95, p99 percentiles
        """
        if not values:
            return {'p50': 0, 'p95': 0, 'p99': 0}
        
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        return {
            'p50': sorted_values[int(n * 0.5) - 1] if n > 0 else 0,
            'p95': sorted_values[int(n * 0.95) - 1] if n > 0 else 0,
            'p99': sorted_values[int(n * 0.99) - 1] if n > 0 else 0
        }
    
    def get_table_metrics(
        self,
        table_name: str,
        operation: Optional[str] = None
    ) -> Dict[str, Dict]:
        """
        Retrieve all metrics for a specific DynamoDB table and operation month by month.
        
        Args:
            table_name: Name of the DynamoDB table
            operation: Operation type (optional, None aggregates across all operations)
            
        Returns:
            Dictionary with metrics for each month (key is month label like 'Oct-25')
        """
        metrics = {}
        
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
            
            # Initialize period data for this month
            period_data = {
                'read_capacity_units': 0,
                'write_capacity_units': 0,
                'latency_values': [],
                'read_throttle_events': 0,
                'write_throttle_events': 0,
                'user_errors': 0,
                'system_errors': 0
            }
            
            # Get metrics for this month
            # Capacity units are table-level metrics (don't support Operation dimension)
            # Query them once per table and reuse for all operations
            capacity_units_datapoints_read = self.get_cloudwatch_metric_data(
                table_name, None, 'ConsumedReadCapacityUnits', month_start, month_end, 'Sum', use_operation_dimension=False
            )
            capacity_units_datapoints_write = self.get_cloudwatch_metric_data(
                table_name, None, 'ConsumedWriteCapacityUnits', month_start, month_end, 'Sum', use_operation_dimension=False
            )
            
            period_data['read_capacity_units'] = sum(dp['Sum'] for dp in capacity_units_datapoints_read if 'Sum' in dp)
            period_data['write_capacity_units'] = sum(dp['Sum'] for dp in capacity_units_datapoints_write if 'Sum' in dp)
            
            # Other metrics support Operation dimension
            metrics_config = [
                ('SuccessfulRequestLatency', 'Average', 'latency_values'),
                ('ReadThrottleEvents', 'Sum', 'read_throttle_events'),
                ('WriteThrottleEvents', 'Sum', 'write_throttle_events'),
                ('UserErrors', 'Sum', 'user_errors'),
                ('SystemErrors', 'Sum', 'system_errors')
            ]
            
            for metric_name, statistic, key in metrics_config:
                datapoints = self.get_cloudwatch_metric_data(
                    table_name, operation, metric_name, month_start, month_end, statistic, use_operation_dimension=True
                )
                
                if key.endswith('_values'):
                    period_data[key] = [dp[statistic] for dp in datapoints if statistic in dp]
                else:
                    period_data[key] = sum(dp[statistic] for dp in datapoints if statistic in dp)
            
            # Calculate latency statistics
            if period_data['latency_values']:
                latency_stats = {
                    'average': statistics.mean(period_data['latency_values']),
                    'max': max(period_data['latency_values']),
                    'min': min(period_data['latency_values'])
                }
                percentiles = self.calculate_percentiles(period_data['latency_values'])
                latency_stats.update(percentiles)
            else:
                latency_stats = {
                    'average': 0, 'max': 0, 'min': 0,
                    'p50': 0, 'p95': 0, 'p99': 0
                }
            
            # Create month key (e.g., 'Oct-25')
            month_key = current_date.strftime('%b-%y')
            
            metrics[month_key] = {
                'read_capacity_units': int(period_data['read_capacity_units']),
                'write_capacity_units': int(period_data['write_capacity_units']),
                'latency_stats': latency_stats,
                'read_throttle_events': int(period_data['read_throttle_events']),
                'write_throttle_events': int(period_data['write_throttle_events']),
                'user_errors': int(period_data['user_errors']),
                'system_errors': int(period_data['system_errors'])
            }
            
            # Move to next month
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)
        
        return metrics
    
    def generate_markdown_report(self, table_metrics: Dict[str, Dict]) -> str:
        """
        Generate a markdown report from the collected metrics.
        
        Args:
            table_metrics: Dictionary with metrics for all tables
            Format: {table_name: {operation: {month_key: metrics}}}
            
        Returns:
            Markdown formatted string
        """
        markdown = "# DynamoDB\n\n"
        
        for table_name in sorted(table_metrics.keys()):
            markdown += f"## {table_name}\n\n"
            
            # table_data structure: {operation: {month_key: metrics}}
            table_data = table_metrics[table_name]
            
            for operation in sorted(table_data.keys()):
                operation_metrics = table_data[operation]
                markdown += f"### Operation: {operation}\n\n"
                markdown += self._generate_table_table(operation_metrics)
            
            markdown += "---\n\n"
        
        return markdown
    
    def _generate_table_table(self, metrics: Dict[str, Dict]) -> str:
        """
        Generate a markdown table for DynamoDB metrics.
        
        Args:
            metrics: Dictionary with month_key -> metrics data
            
        Returns:
            Markdown table string
        """
        # Table header
        markdown = "| Month | Read Capacity Units | Write Capacity Units | Latency Avg (ms) | Latency Max (ms) | Latency p50 (ms) | Latency p95 (ms) | Latency p99 (ms) | Read Throttles | Write Throttles | User Errors | System Errors |\n"
        markdown += "|-------|---------------------|----------------------|------------------|------------------|------------------|------------------|------------------|----------------|----------------|-------------|---------------|\n"
        
        # Process all periods - sort months chronologically
        def sort_month_key(month_str: str):
            try:
                return datetime.strptime(month_str, '%b-%y')
            except ValueError:
                return datetime.min
        
        for period_key in sorted(metrics.keys(), key=sort_month_key):
            data = metrics[period_key]
            latency_stats = data['latency_stats']
            
            # Format numbers with commas
            read_capacity = f"{data['read_capacity_units']:,}"
            write_capacity = f"{data['write_capacity_units']:,}"
            
            markdown += (
                f"| {period_key} | {read_capacity} | {write_capacity} | "
                f"{latency_stats['average']:.1f} | {latency_stats['max']:.0f} | "
                f"{latency_stats['p50']:.0f} | {latency_stats['p95']:.0f} | "
                f"{latency_stats['p99']:.0f} | "
                f"{data['read_throttle_events']} | {data['write_throttle_events']} | "
                f"{data['user_errors']} | {data['system_errors']} |\n"
            )
        
        markdown += "\n"
        return markdown
    
    def generate_performance_report(
        self,
        output_path: str
    ):
        """
        Generate the complete performance report for all DynamoDB tables.
        
        Args:
            output_path: Full path for the output markdown file
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"Processing account: {self.account_id}")
        print(f"Date range: {self.start_date.date()} to {self.end_date.date()}")
        print("Retrieving DynamoDB tables...")
        
        tables = self.get_all_tables()
        print(f"Found {len(tables)} DynamoDB tables")
        
        all_metrics = {}
        
        for i, table_name in enumerate(tables, 1):
            print(f"Processing table {i}/{len(tables)}: {table_name}")
            
            try:
                # Discover all operations for this table
                operations = self.get_table_operations(table_name)
                print(f"  Found {len(operations)} operation types")
                
                if not operations:
                    # Fallback: try to get aggregated metrics if no operations found
                    print(f"  No operations found, trying aggregated metrics...")
                    metrics = self.get_table_metrics(table_name, None)
                    if metrics:
                        all_metrics[table_name] = {'ALL': metrics}
                    continue
                
                table_metrics_dict = {}
                for j, operation in enumerate(operations, 1):
                    print(f"  Processing operation {j}/{len(operations)}: {operation}")
                    
                    try:
                        metrics = self.get_table_metrics(table_name, operation)
                        if metrics:
                            table_metrics_dict[operation] = metrics
                    except Exception as e:
                        print(f"    Error processing {operation}: {e}")
                        continue
                
                if table_metrics_dict:
                    all_metrics[table_name] = table_metrics_dict
            except Exception as e:
                print(f"Error processing {table_name}: {e}")
                continue
        
        print("Generating markdown report...")
        markdown_content = self.generate_markdown_report(all_metrics)
        
        with open(output_file, 'w') as f:
            f.write(markdown_content)
        
        print(f"Performance report generated: {output_path}")


def parse_arguments():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Extract AWS DynamoDB performance metrics for specified accounts and date range',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract DynamoDB metrics for Oct 2025
  python dynamodb_performance_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 --account-ids 205382370719 975050219213

  # Specify custom output directory
  python dynamodb_performance_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 \\
      --account-ids 205382370719 --output-dir data/domains/hr/worker/aws/

  # Specify multiple regions
  python dynamodb_performance_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 \\
      --account-ids 205382370719 --regions us-east-1 us-west-2
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
    
    parser.add_argument(
        '--regions',
        nargs='+',
        default=['us-east-1'],
        help='AWS regions to query (default: us-east-1)'
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
    
    print(f"=== AWS DynamoDB Performance Metrics Extraction ===")
    print(f"Date Range: {start_date.date()} to {end_date.date()}")
    print(f"Accounts: {', '.join(args.account_ids)}")
    print(f"Regions: {', '.join(args.regions)}")
    print(f"Metrics granularity: Table + Operation level")
    print()
    
    # Process each account - generate one file per account
    for account_id in args.account_ids:
        try:
            print(f"\n{'='*60}")
            print(f"Processing Account: {account_id}")
            if len(args.regions) > 1:
                print(f"Regions: {', '.join(args.regions)}")
            print(f"{'='*60}\n")
            
            # Collect metrics from all regions for this account
            all_table_metrics = {}
            
            for region in args.regions:
                try:
                    print(f"Processing region: {region}")
                    
                    # Create metrics collector for this account/region
                    metrics_collector = DynamoDBPerformanceMetrics(
                        account_id=account_id,
                        start_date=start_date,
                        end_date=end_date,
                        region=region
                    )
                    
                    # Get tables and their metrics for this region
                    tables = metrics_collector.get_all_tables()
                    print(f"Found {len(tables)} DynamoDB tables in {region}")
                    
                    for i, table_name in enumerate(tables, 1):
                        # Initialize table structure: Table -> Operation -> Month -> Metrics
                        if table_name not in all_table_metrics:
                            all_table_metrics[table_name] = {}
                        
                        print(f"Processing table {i}/{len(tables)}: {table_name}")
                        try:
                            # Discover all operations for this table
                            operations = metrics_collector.get_table_operations(table_name)
                            
                            if not operations:
                                # Fallback: try aggregated metrics if no operations found
                                metrics = metrics_collector.get_table_metrics(table_name, None)
                                if metrics:
                                    operation_key = 'ALL'
                                    if operation_key not in all_table_metrics[table_name]:
                                        all_table_metrics[table_name][operation_key] = {}
                                    # Merge metrics from different regions
                                    for period_key, period_data in metrics.items():
                                        if period_key not in all_table_metrics[table_name][operation_key]:
                                            all_table_metrics[table_name][operation_key][period_key] = period_data.copy()
                                        else:
                                            # Combine metrics from different regions
                                            existing = all_table_metrics[table_name][operation_key][period_key]
                                            existing['read_capacity_units'] += period_data['read_capacity_units']
                                            existing['write_capacity_units'] += period_data['write_capacity_units']
                                            existing['read_throttle_events'] += period_data['read_throttle_events']
                                            existing['write_throttle_events'] += period_data['write_throttle_events']
                                            existing['user_errors'] += period_data['user_errors']
                                            existing['system_errors'] += period_data['system_errors']
                                            
                                            # Average latency stats
                                            existing_latency = existing['latency_stats']
                                            new_latency = period_data['latency_stats']
                                            existing['latency_stats'] = {
                                                'average': (existing_latency['average'] + new_latency['average']) / 2,
                                                'max': max(existing_latency['max'], new_latency['max']),
                                                'min': min(existing_latency['min'], new_latency['min']),
                                                'p50': max(existing_latency['p50'], new_latency['p50']),
                                                'p95': max(existing_latency['p95'], new_latency['p95']),
                                                'p99': max(existing_latency['p99'], new_latency['p99'])
                                            }
                                continue
                            
                            # Process each operation
                            for operation in operations:
                                if operation not in all_table_metrics[table_name]:
                                    all_table_metrics[table_name][operation] = {}
                                
                                metrics = metrics_collector.get_table_metrics(table_name, operation)
                                
                                # Merge metrics from different regions
                                for period_key, period_data in metrics.items():
                                    if period_key not in all_table_metrics[table_name][operation]:
                                        all_table_metrics[table_name][operation][period_key] = period_data.copy()
                                    else:
                                        # Combine metrics from different regions
                                        existing = all_table_metrics[table_name][operation][period_key]
                                        existing['read_capacity_units'] += period_data['read_capacity_units']
                                        existing['write_capacity_units'] += period_data['write_capacity_units']
                                        existing['read_throttle_events'] += period_data['read_throttle_events']
                                        existing['write_throttle_events'] += period_data['write_throttle_events']
                                        existing['user_errors'] += period_data['user_errors']
                                        existing['system_errors'] += period_data['system_errors']
                                        
                                        # Average latency stats
                                        existing_latency = existing['latency_stats']
                                        new_latency = period_data['latency_stats']
                                        existing['latency_stats'] = {
                                            'average': (existing_latency['average'] + new_latency['average']) / 2,
                                            'max': max(existing_latency['max'], new_latency['max']),
                                            'min': min(existing_latency['min'], new_latency['min']),
                                            'p50': max(existing_latency['p50'], new_latency['p50']),
                                            'p95': max(existing_latency['p95'], new_latency['p95']),
                                            'p99': max(existing_latency['p99'], new_latency['p99'])
                                        }
                        except Exception as e:
                            print(f"Error processing {table_name} in {region}: {e}")
                            continue
                
                except Exception as e:
                    print(f"Error processing region {region} for account {account_id}: {e}")
                    continue
            
            # Generate output filename
            # Format: DynamoDBPerformance-{Account-ID}-{start-date}-{end-date}.md
            filename = f"DynamoDBPerformance-{account_id}-{start_date_str}-{end_date_str}.md"
            
            output_path = output_dir / filename
            
            # Generate report using the first collector's method
            if all_table_metrics:
                temp_collector = DynamoDBPerformanceMetrics(
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date,
                    region=args.regions[0]
                )
                
                # Write the report
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                
                markdown_content = temp_collector.generate_markdown_report(all_table_metrics)
                
                with open(output_file, 'w') as f:
                    f.write(markdown_content)
                
                print(f"\nPerformance report generated: {output_path}")
            else:
                print(f"No metrics collected for account {account_id}")
        
        except Exception as e:
            print(f"Error processing account {account_id}: {e}")
            continue
    
    print("\n=== Extraction Complete ===")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

