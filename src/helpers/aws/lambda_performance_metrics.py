import boto3
import argparse
from datetime import datetime, timedelta
from typing import Dict, List
import statistics
from pathlib import Path


class LambdaPerformanceMetrics:
    def __init__(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime,
        region: str = 'us-east-1'
    ):
        """
        Initialize the Lambda Performance Metrics class.
        
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
        self.lambda_client = self.session.client('lambda', region_name=region)
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
                RoleSessionName=f"lambda-metrics-{account_id}"
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
        
    def get_all_lambda_functions(self) -> List[str]:
        """
        Retrieve all Lambda function names from the AWS account.
        
        Returns:
            List of Lambda function names
        """
        function_names = []
        paginator = self.lambda_client.get_paginator('list_functions')
        
        for page in paginator.paginate():
            for function in page['Functions']:
                function_names.append(function['FunctionName'])

        print("Total Lambda functions found:")
        print(len(function_names))

        return function_names
    
    def get_function_memory_size(self, function_name: str) -> int:
        """
        Get the allocated memory size for a Lambda function.
        
        Args:
            function_name: Name of the Lambda function
            
        Returns:
            Allocated memory size in MB
        """
        try:
            response = self.lambda_client.get_function(FunctionName=function_name)
            return response['Configuration']['MemorySize']
        except Exception as e:
            print(f"Error getting memory size for {function_name}: {e}")
            return 128  # Default memory size
    
    def get_cloudwatch_metric_data(self, function_name: str, metric_name: str, 
                                  start_time: datetime, end_time: datetime, 
                                  statistic: str) -> List[Dict]:
        """
        Retrieve CloudWatch metric data for a specific Lambda function.
        
        Args:
            function_name: Name of the Lambda function
            metric_name: CloudWatch metric name
            start_time: Start time for the metric query
            end_time: End time for the metric query
            statistic: Statistic type (Average, Maximum, Minimum, Sum)
            
        Returns:
            List of metric datapoints
        """
        try:
            response = self.cloudwatch_client.get_metric_statistics(
                Namespace='AWS/Lambda',
                MetricName=metric_name,
                Dimensions=[
                    {
                        'Name': 'FunctionName',
                        'Value': function_name
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # 1 day
                Statistics=[statistic]
            )
            return response['Datapoints']
        except Exception as e:
            print(f"Error getting {metric_name} for {function_name}: {e}")
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
    
    def get_function_metrics(self, function_name: str) -> Dict[str, Dict]:
        """
        Retrieve all metrics for a specific Lambda function month by month for the specified date range.
        
        Args:
            function_name: Name of the Lambda function
            
        Returns:
            Dictionary with metrics for each month (key is month label like 'Oct-25')
        """
        allocated_memory = self.get_function_memory_size(function_name)
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
                'invocations': 0,
                'errors': 0,
                'duration_values': [],
                'throttles': 0,
                'memory_values': [],
                'allocated_memory': allocated_memory
            }
            
            # Get metrics for this month
            metrics_config = [
                ('Invocations', 'Sum', 'invocations'),
                ('Errors', 'Sum', 'errors'),
                ('Duration', 'Average', 'duration_values'),
                ('Throttles', 'Sum', 'throttles'),
                ('MemoryUtilization', 'Average', 'memory_values')
            ]
            
            for metric_name, statistic, key in metrics_config:
                datapoints = self.get_cloudwatch_metric_data(
                    function_name, metric_name, month_start, month_end, statistic
                )
                
                if key == 'duration_values' or key == 'memory_values':
                    period_data[key] = [dp[statistic] for dp in datapoints if statistic in dp]
                else:
                    period_data[key] = sum(dp[statistic] for dp in datapoints if statistic in dp)
            
            # Calculate duration statistics
            if period_data['duration_values']:
                duration_stats = {
                    'average': statistics.mean(period_data['duration_values']),
                    'max': max(period_data['duration_values']),
                    'min': min(period_data['duration_values'])
                }
                percentiles = self.calculate_percentiles(period_data['duration_values'])
                duration_stats.update(percentiles)
            else:
                duration_stats = {
                    'average': 0, 'max': 0, 'min': 0,
                    'p50': 0, 'p95': 0, 'p99': 0
                }
            
            # Calculate memory utilization
            if period_data['memory_values']:
                avg_memory_percent = statistics.mean(period_data['memory_values'])
                used_memory = (avg_memory_percent / 100) * allocated_memory
            else:
                avg_memory_percent = 0
                used_memory = 0
            
            # Get cold start data (approximated from duration spikes)
            cold_starts = self.estimate_cold_starts(period_data['duration_values'])
            
            # Create month key (e.g., 'Oct-25')
            month_key = current_date.strftime('%b-%y')
            
            metrics[month_key] = {
                'invocations': int(period_data['invocations']),
                'errors': int(period_data['errors']),
                'duration_stats': duration_stats,
                'throttles': int(period_data['throttles']),
                'cold_starts': cold_starts,
                'memory_util_percent': round(avg_memory_percent, 1),
                'used_memory': int(used_memory),
                'allocated_memory': allocated_memory
            }
            
            # Move to next month
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)
        
        return metrics
    
    def estimate_cold_starts(self, duration_values: List[float]) -> int:
        """
        Estimate cold starts based on duration outliers.
        
        Args:
            duration_values: List of duration values
            
        Returns:
            Estimated number of cold starts
        """
        if not duration_values or len(duration_values) < 10:
            return 0
        
        # Cold starts typically have much higher duration
        mean_duration = statistics.mean(duration_values)
        threshold = mean_duration * 3  # Assume cold starts are 3x normal duration
        
        cold_starts = sum(1 for duration in duration_values if duration > threshold)
        return cold_starts
    
    def generate_markdown_report(self, function_metrics: Dict[str, Dict]) -> str:
        """
        Generate a markdown report from the collected metrics.
        
        Args:
            function_metrics: Dictionary with metrics for all functions
            
        Returns:
            Markdown formatted string
        """
        markdown = "# Lambda\n\n"
        
        # Get the period key from the first function's metrics
        period_label = None
        if function_metrics:
            first_function = next(iter(function_metrics.values()))
            if first_function:
                period_label = next(iter(first_function.keys()))
        
        # Use date range as period label if available
        if not period_label:
            period_label = f"{self.start_date.strftime('%b-%y')} to {self.end_date.strftime('%b-%y')}"
        
        for function_name, metrics in function_metrics.items():
            markdown += f"## {function_name}\n\n"
            
            # Table header
            markdown += "| Month | Invocations | Errors | Average (ms) | Max (ms) | Min (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throttles | Cold Starts | Memory Util (%) |\n"
            markdown += "|-------|-------------|--------|--------------|----------|----------|----------|----------|----------|-----------|-------------|----------------|\n"
            
            # Process all periods - sort months chronologically
            # Parse month-year strings (e.g., 'Oct-25') and sort by datetime
            def sort_month_key(month_str: str):
                try:
                    return datetime.strptime(month_str, '%b-%y')
                except ValueError:
                    # Fallback for non-month keys
                    return datetime.min
            
            for period_key in sorted(metrics.keys(), key=sort_month_key):
                data = metrics[period_key]
                stats = data['duration_stats']
                
                # Format numbers with commas
                invocations = f"{data['invocations']:,}"
                memory_util = f"{data['memory_util_percent']} ({data['used_memory']}/{data['allocated_memory']})"
                
                markdown += f"| {period_key} | {invocations} | {data['errors']} | {stats['average']:.1f} | {stats['max']:.0f} | {stats['min']:.0f} | {stats['p50']:.0f} | {stats['p95']:.0f} | {stats['p99']:.0f} | {data['throttles']} | {data['cold_starts']} | {memory_util} |\n"
            
            markdown += "\n"
        
        return markdown
    
    def generate_performance_report(self, output_path: str):
        """
        Generate the complete performance report for all Lambda functions.
        
        Args:
            output_path: Full path for the output markdown file
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"Processing account: {self.account_id}")
        print(f"Date range: {self.start_date.date()} to {self.end_date.date()}")
        print("Retrieving Lambda functions...")
        
        function_names = self.get_all_lambda_functions()
        print(f"Found {len(function_names)} Lambda functions")
        
        all_metrics = {}
        
        for i, function_name in enumerate(function_names, 1):
            print(f"Processing function {i}/{len(function_names)}: {function_name}")
            try:
                metrics = self.get_function_metrics(function_name)
                all_metrics[function_name] = metrics
            except Exception as e:
                print(f"Error processing {function_name}: {e}")
                continue
        
        print("Generating markdown report...")
        markdown_content = self.generate_markdown_report(all_metrics)
        
        with open(output_file, 'w') as f:
            f.write(markdown_content)
        
        print(f"Performance report generated: {output_path}")

def parse_arguments():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Extract AWS Lambda performance metrics for specified accounts and date range',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract Lambda metrics for Oct 2025
  python lambda_performance_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 --account-ids 205382370719 975050219213

  # Specify custom output directory
  python lambda_performance_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 \\
      --account-ids 205382370719 --output-dir data/domains/hr/worker/aws/

  # Specify regions
  python lambda_performance_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 \\
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
    
    print(f"=== AWS Lambda Performance Metrics Extraction ===")
    print(f"Date Range: {start_date.date()} to {end_date.date()}")
    print(f"Accounts: {', '.join(args.account_ids)}")
    print(f"Regions: {', '.join(args.regions)}")
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
            all_function_metrics = {}
            
            for region in args.regions:
                try:
                    print(f"Processing region: {region}")
                    
                    # Create metrics collector for this account/region
                    metrics_collector = LambdaPerformanceMetrics(
                        account_id=account_id,
                        start_date=start_date,
                        end_date=end_date,
                        region=region
                    )
                    
                    # Get functions and their metrics for this region
                    function_names = metrics_collector.get_all_lambda_functions()
                    print(f"Found {len(function_names)} Lambda functions in {region}")
                    
                    for i, function_name in enumerate(function_names, 1):
                        # If function exists in multiple regions, combine metrics
                        if function_name not in all_function_metrics:
                            all_function_metrics[function_name] = {}
                        
                        print(f"Processing function {i}/{len(function_names)}: {function_name}")
                        try:
                            metrics = metrics_collector.get_function_metrics(function_name)
                            # Merge metrics from different regions (for the same period)
                            # For simplicity, if a function exists in multiple regions,
                            # we'll aggregate the already-computed stats
                            for period_key, period_data in metrics.items():
                                if period_key not in all_function_metrics[function_name]:
                                    # First region for this period
                                    all_function_metrics[function_name][period_key] = period_data.copy()
                                else:
                                    # Combine metrics from different regions
                                    existing = all_function_metrics[function_name][period_key]
                                    # Sum metrics that should be summed
                                    existing['invocations'] += period_data['invocations']
                                    existing['errors'] += period_data['errors']
                                    existing['throttles'] += period_data['throttles']
                                    existing['cold_starts'] += period_data['cold_starts']
                                    # For duration and memory, we'll approximate by weighted average
                                    # or use the max region's values (simpler approach)
                                    # Taking max for duration stats to be conservative
                                    existing_duration = existing['duration_stats']
                                    new_duration = period_data['duration_stats']
                                    existing['duration_stats'] = {
                                        'average': (existing_duration['average'] + new_duration['average']) / 2,
                                        'max': max(existing_duration['max'], new_duration['max']),
                                        'min': min(existing_duration['min'], new_duration['min']),
                                        'p50': max(existing_duration['p50'], new_duration['p50']),
                                        'p95': max(existing_duration['p95'], new_duration['p95']),
                                        'p99': max(existing_duration['p99'], new_duration['p99'])
                                    }
                                    # For memory, average the utilization
                                    existing['memory_util_percent'] = round(
                                        (existing['memory_util_percent'] + period_data['memory_util_percent']) / 2, 1
                                    )
                                    # Recalculate used memory based on average utilization
                                    existing['used_memory'] = int((existing['memory_util_percent'] / 100) * existing['allocated_memory'])
                        except Exception as e:
                            print(f"Error processing {function_name} in {region}: {e}")
                            continue
                
                except Exception as e:
                    print(f"Error processing region {region} for account {account_id}: {e}")
                    continue
            
            # Generate output filename
            # Format: LambdaPerformance-{Account-ID}-{start-date}-{end-date}.md
            filename = f"LambdaPerformance-{account_id}-{start_date_str}-{end_date_str}.md"
            
            output_path = output_dir / filename
            
            # Generate report using the first collector's method
            # We need to create a temporary collector just for report generation
            if all_function_metrics:
                temp_collector = LambdaPerformanceMetrics(
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date,
                    region=args.regions[0]
                )
                
                # Write the report
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                
                markdown_content = temp_collector.generate_markdown_report(all_function_metrics)
                
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