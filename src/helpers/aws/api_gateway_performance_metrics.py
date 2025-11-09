import boto3
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import statistics
from pathlib import Path


class ApiGatewayMetrics:
    def __init__(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime,
        region: str = 'us-east-1'
    ):
        """
        Initialize the API Gateway Performance Metrics class.
        
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
        self.apigateway_client = self.session.client('apigatewayv2', region_name=region)
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
                RoleSessionName=f"apigateway-metrics-{account_id}"
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
    
    def get_all_apis(self) -> List[Dict[str, str]]:
        """
        Retrieve all HTTP/REST API Gateway APIs from the AWS account.
        
        Returns:
            List of dictionaries with 'ApiId' and 'Name' keys
        """
        apis = []
        try:
            paginator = self.apigateway_client.get_paginator('get_apis')
            
            for page in paginator.paginate():
                for api in page['Items']:
                    # Only include HTTP APIs (ProtocolType == 'HTTP')
                    if api.get('ProtocolType') == 'HTTP':
                        apis.append({
                            'ApiId': api['ApiId'],
                            'Name': api.get('Name', api['ApiId'])
                        })
        except Exception as e:
            print(f"Error listing APIs: {e}")
        
        return apis
    
    def get_api_stages(self, api_id: str) -> List[str]:
        """
        Retrieve all stages for a given API.
        
        Args:
            api_id: API Gateway API ID
            
        Returns:
            List of stage names
        """
        stages = []
        try:
            response = self.apigateway_client.get_stages(ApiId=api_id)
            for stage in response.get('Items', []):
                stages.append(stage['StageName'])
        except Exception as e:
            print(f"Error getting stages for API {api_id}: {e}")
        
        return stages
    
    def get_endpoint_method_combinations(
        self,
        api_name: str,
        stage: Optional[str]
    ) -> List[Tuple[str, str]]:
        """
        Discover all unique Method and Resource combinations for an API.
        
        Args:
            api_name: Name of the API Gateway API
            stage: Stage name (optional, None for all stages)
            
        Returns:
            List of tuples (method, resource) representing unique endpoint combinations
        """
        combinations = set()
        try:
            # Use list_metrics to discover all unique dimension combinations
            dimensions = [
                {
                    'Name': 'ApiName',
                    'Value': api_name
                }
            ]
            
            if stage:
                dimensions.append({
                    'Name': 'Stage',
                    'Value': stage
                })
            
            # Query for any metric to discover dimension combinations
            response = self.cloudwatch_client.list_metrics(
                Namespace='AWS/ApiGateway',
                Dimensions=dimensions
            )
            
            for metric in response.get('Metrics', []):
                method = None
                resource = None
                
                for dim in metric.get('Dimensions', []):
                    if dim['Name'] == 'Method':
                        method = dim['Value']
                    elif dim['Name'] == 'Resource':
                        resource = dim['Value']
                
                if method and resource:
                    combinations.add((method, resource))
            
            # Handle pagination
            while 'NextToken' in response:
                response = self.cloudwatch_client.list_metrics(
                    Namespace='AWS/ApiGateway',
                    Dimensions=dimensions,
                    NextToken=response['NextToken']
                )
                
                for metric in response.get('Metrics', []):
                    method = None
                    resource = None
                    
                    for dim in metric.get('Dimensions', []):
                        if dim['Name'] == 'Method':
                            method = dim['Value']
                        elif dim['Name'] == 'Resource':
                            resource = dim['Value']
                    
                    if method and resource:
                        combinations.add((method, resource))
        
        except Exception as e:
            print(f"Error discovering endpoints for {api_name}/{stage}: {e}")
        
        return sorted(list(combinations))
    
    def get_cloudwatch_metric_data(
        self,
        api_name: str,
        stage: Optional[str],
        method: Optional[str],
        resource: Optional[str],
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
        statistic: str
    ) -> List[Dict]:
        """
        Retrieve CloudWatch metric data for a specific API Gateway API endpoint.
        
        Args:
            api_name: Name of the API Gateway API
            stage: Stage name (optional, None for all stages)
            method: HTTP method (optional, None for all methods)
            resource: Resource path (optional, None for all resources)
            metric_name: CloudWatch metric name
            start_time: Start time for the metric query
            end_time: End time for the metric query
            statistic: Statistic type (Average, Maximum, Minimum, Sum)
            
        Returns:
            List of metric datapoints
        """
        try:
            dimensions = [
                {
                    'Name': 'ApiName',
                    'Value': api_name
                }
            ]
            
            # Add stage dimension if specified
            if stage:
                dimensions.append({
                    'Name': 'Stage',
                    'Value': stage
                })
            
            # Add method dimension if specified
            if method:
                dimensions.append({
                    'Name': 'Method',
                    'Value': method
                })
            
            # Add resource dimension if specified
            if resource:
                dimensions.append({
                    'Name': 'Resource',
                    'Value': resource
                })
            
            response = self.cloudwatch_client.get_metric_statistics(
                Namespace='AWS/ApiGateway',
                MetricName=metric_name,
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # 1 day
                Statistics=[statistic]
            )
            return response['Datapoints']
        except Exception as e:
            endpoint_desc = f"{method} {resource}" if method and resource else "aggregated"
            print(f"Error getting {metric_name} for {api_name}/{stage}/{endpoint_desc}: {e}")
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
    
    def get_api_metrics(
        self,
        api_name: str,
        stage: Optional[str] = None,
        method: Optional[str] = None,
        resource: Optional[str] = None
    ) -> Dict[str, Dict]:
        """
        Retrieve all metrics for a specific API Gateway API endpoint month by month for the specified date range.
        
        Args:
            api_name: Name of the API Gateway API
            stage: Stage name (optional, None aggregates across all stages)
            method: HTTP method (optional, None aggregates across all methods)
            resource: Resource path (optional, None aggregates across all resources)
            
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
                'count': 0,
                'latency_values': [],
                'integration_latency_values': [],
                'error_4xx': 0,
                'error_5xx': 0
            }
            
            # Get metrics for this month
            metrics_config = [
                ('Count', 'Sum', 'count'),
                ('Latency', 'Average', 'latency_values'),
                ('IntegrationLatency', 'Average', 'integration_latency_values'),
                ('4XXError', 'Sum', 'error_4xx'),
                ('5XXError', 'Sum', 'error_5xx')
            ]
            
            for metric_name, statistic, key in metrics_config:
                datapoints = self.get_cloudwatch_metric_data(
                    api_name, stage, method, resource, metric_name, month_start, month_end, statistic
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
            
            # Calculate integration latency statistics
            if period_data['integration_latency_values']:
                integration_latency_stats = {
                    'average': statistics.mean(period_data['integration_latency_values']),
                    'max': max(period_data['integration_latency_values']),
                    'min': min(period_data['integration_latency_values'])
                }
                percentiles = self.calculate_percentiles(period_data['integration_latency_values'])
                integration_latency_stats.update(percentiles)
            else:
                integration_latency_stats = {
                    'average': 0, 'max': 0, 'min': 0,
                    'p50': 0, 'p95': 0, 'p99': 0
                }
            
            # Create month key (e.g., 'Oct-25')
            month_key = current_date.strftime('%b-%y')
            
            metrics[month_key] = {
                'count': int(period_data['count']),
                'latency_stats': latency_stats,
                'integration_latency_stats': integration_latency_stats,
                'error_4xx': int(period_data['error_4xx']),
                'error_5xx': int(period_data['error_5xx'])
            }
            
            # Move to next month
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)
        
        return metrics
    
    def generate_markdown_report(self, api_metrics: Dict[str, Dict]) -> str:
        """
        Generate a markdown report from the collected metrics.
        
        Args:
            api_metrics: Dictionary with metrics for all APIs
            Format: {api_key: {stage_key: {endpoint_key: {month_key: metrics}}}}
            where endpoint_key is "{method} {resource}"
            
        Returns:
            Markdown formatted string
        """
        markdown = "# API Gateway\n\n"
        
        for api_key, api_data in api_metrics.items():
            markdown += f"## {api_key}\n\n"
            
            # api_data structure: {stage_key: {endpoint_key: {month_key: metrics}}}
            for stage_key, stage_data in sorted(api_data.items()):
                markdown += f"### Stage: {stage_key}\n\n"
                
                # stage_data structure: {endpoint_key: {month_key: metrics}}
                for endpoint_key in sorted(stage_data.keys()):
                    endpoint_metrics = stage_data[endpoint_key]
                    markdown += f"#### {endpoint_key}\n\n"
                    markdown += self._generate_api_table(endpoint_metrics)
                
                markdown += "---\n\n"
        
        return markdown
    
    def _generate_api_table(self, metrics: Dict[str, Dict]) -> str:
        """
        Generate a markdown table for API metrics.
        
        Args:
            metrics: Dictionary with month_key -> metrics data
            
        Returns:
            Markdown table string
        """
        # Table header
        markdown = "| Month | Requests | Latency Avg (ms) | Latency Max (ms) | Latency p50 (ms) | Latency p95 (ms) | Latency p99 (ms) | Integration Latency Avg (ms) | Integration Latency Max (ms) | Integration Latency p95 (ms) | 4XX Errors | 5XX Errors |\n"
        markdown += "|-------|----------|------------------|------------------|------------------|------------------|------------------|------------------------------|-----------------------------|----------------------------|-------------|-------------|\n"
        
        # Process all periods - sort months chronologically
        def sort_month_key(month_str: str):
            try:
                return datetime.strptime(month_str, '%b-%y')
            except ValueError:
                return datetime.min
        
        for period_key in sorted(metrics.keys(), key=sort_month_key):
            data = metrics[period_key]
            latency_stats = data['latency_stats']
            integration_latency_stats = data['integration_latency_stats']
            
            # Format numbers with commas
            count = f"{data['count']:,}"
            
            markdown += (
                f"| {period_key} | {count} | "
                f"{latency_stats['average']:.1f} | {latency_stats['max']:.0f} | "
                f"{latency_stats['p50']:.0f} | {latency_stats['p95']:.0f} | "
                f"{latency_stats['p99']:.0f} | "
                f"{integration_latency_stats['average']:.1f} | "
                f"{integration_latency_stats['max']:.0f} | "
                f"{integration_latency_stats['p95']:.0f} | "
                f"{data['error_4xx']} | {data['error_5xx']} |\n"
            )
        
        markdown += "\n"
        return markdown
    
    def generate_performance_report(
        self,
        output_path: str,
        group_by_stage: bool = True
    ):
        """
        Generate the complete performance report for all API Gateway APIs.
        
        Args:
            output_path: Full path for the output markdown file
            group_by_stage: If True, generate separate metrics per stage (default: True)
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"Processing account: {self.account_id}")
        print(f"Date range: {self.start_date.date()} to {self.end_date.date()}")
        print("Retrieving API Gateway APIs...")
        
        apis = self.get_all_apis()
        print(f"Found {len(apis)} HTTP/REST APIs")
        
        all_metrics = {}
        
        for i, api in enumerate(apis, 1):
            api_id = api['ApiId']
            api_name = api['Name']
            print(f"Processing API {i}/{len(apis)}: {api_name} ({api_id})")
            
            try:
                # Always group by stage for endpoint-level granularity
                stages = self.get_api_stages(api_id)
                if not stages:
                    stages = [None]  # Use None to indicate no stage filtering
                
                api_metrics_dict = {}
                for stage in stages:
                    stage_key = stage if stage else 'all-stages'
                    print(f"  Processing stage: {stage_key}")
                    
                    # Discover all endpoint+method combinations for this API/stage
                    endpoint_combinations = self.get_endpoint_method_combinations(api_name, stage)
                    print(f"    Found {len(endpoint_combinations)} endpoint+method combinations")
                    
                    if not endpoint_combinations:
                        # Fallback: try to get aggregated metrics if no endpoints found
                        print(f"    No endpoints found, trying aggregated metrics...")
                        metrics = self.get_api_metrics(api_name, stage, None, None)
                        if metrics:
                            api_metrics_dict[stage_key] = {'ALL': metrics}
                        continue
                    
                    endpoint_metrics_dict = {}
                    for j, (method, resource) in enumerate(endpoint_combinations, 1):
                        endpoint_key = f"{method} {resource}"
                        print(f"    Processing endpoint {j}/{len(endpoint_combinations)}: {endpoint_key}")
                        
                        try:
                            metrics = self.get_api_metrics(api_name, stage, method, resource)
                            if metrics:
                                endpoint_metrics_dict[endpoint_key] = metrics
                        except Exception as e:
                            print(f"      Error processing {endpoint_key}: {e}")
                            continue
                    
                    if endpoint_metrics_dict:
                        api_metrics_dict[stage_key] = endpoint_metrics_dict
                
                if api_metrics_dict:
                    all_metrics[api_name] = api_metrics_dict
            except Exception as e:
                print(f"Error processing {api_name}: {e}")
                continue
        
        print("Generating markdown report...")
        markdown_content = self.generate_markdown_report(all_metrics)
        
        with open(output_file, 'w') as f:
            f.write(markdown_content)
        
        print(f"Performance report generated: {output_path}")


def parse_arguments():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Extract AWS API Gateway performance metrics for specified accounts and date range',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract API Gateway metrics for Oct 2025
  python api_gateway_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 --account-ids 205382370719 975050219213

  # Specify custom output directory
  python api_gateway_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 \\
      --account-ids 205382370719 --output-dir data/domains/hr/worker/aws/

  # Specify multiple regions
  python api_gateway_metrics.py --start-date 2025-10-01 --end-date 2025-10-31 \\
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
    
    print(f"=== AWS API Gateway Performance Metrics Extraction ===")
    print(f"Date Range: {start_date.date()} to {end_date.date()}")
    print(f"Accounts: {', '.join(args.account_ids)}")
    print(f"Regions: {', '.join(args.regions)}")
    print(f"Metrics granularity: Endpoint + Method level")
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
            all_api_metrics = {}
            
            for region in args.regions:
                try:
                    print(f"Processing region: {region}")
                    
                    # Create metrics collector for this account/region
                    metrics_collector = ApiGatewayMetrics(
                        account_id=account_id,
                        start_date=start_date,
                        end_date=end_date,
                        region=region
                    )
                    
                    # Get APIs and their metrics for this region
                    apis = metrics_collector.get_all_apis()
                    print(f"Found {len(apis)} API Gateway APIs in {region}")
                    
                    for i, api in enumerate(apis, 1):
                        api_id = api['ApiId']
                        api_name = api['Name']
                        
                        # Initialize API structure: API -> Stage -> Endpoint -> Month -> Metrics
                        if api_name not in all_api_metrics:
                            all_api_metrics[api_name] = {}
                        
                        print(f"Processing API {i}/{len(apis)}: {api_name}")
                        try:
                            # Always group by stage for endpoint-level granularity
                            stages = metrics_collector.get_api_stages(api_id)
                            if not stages:
                                stages = [None]
                            
                            for stage in stages:
                                stage_key = stage if stage else 'all-stages'
                                
                                if stage_key not in all_api_metrics[api_name]:
                                    all_api_metrics[api_name][stage_key] = {}
                                
                                # Discover all endpoint+method combinations for this API/stage
                                endpoint_combinations = metrics_collector.get_endpoint_method_combinations(api_name, stage)
                                
                                if not endpoint_combinations:
                                    # Fallback: try aggregated metrics if no endpoints found
                                    metrics = metrics_collector.get_api_metrics(api_name, stage, None, None)
                                    if metrics:
                                        endpoint_key = 'ALL'
                                        if endpoint_key not in all_api_metrics[api_name][stage_key]:
                                            all_api_metrics[api_name][stage_key][endpoint_key] = {}
                                        # Merge metrics from different regions
                                        for period_key, period_data in metrics.items():
                                            if period_key not in all_api_metrics[api_name][stage_key][endpoint_key]:
                                                all_api_metrics[api_name][stage_key][endpoint_key][period_key] = period_data.copy()
                                            else:
                                                # Combine metrics from different regions
                                                existing = all_api_metrics[api_name][stage_key][endpoint_key][period_key]
                                                existing['count'] += period_data['count']
                                                existing['error_4xx'] += period_data['error_4xx']
                                                existing['error_5xx'] += period_data['error_5xx']
                                                
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
                                                
                                                existing_integration_latency = existing['integration_latency_stats']
                                                new_integration_latency = period_data['integration_latency_stats']
                                                existing['integration_latency_stats'] = {
                                                    'average': (existing_integration_latency['average'] + new_integration_latency['average']) / 2,
                                                    'max': max(existing_integration_latency['max'], new_integration_latency['max']),
                                                    'min': min(existing_integration_latency['min'], new_integration_latency['min']),
                                                    'p50': max(existing_integration_latency['p50'], new_integration_latency['p50']),
                                                    'p95': max(existing_integration_latency['p95'], new_integration_latency['p95']),
                                                    'p99': max(existing_integration_latency['p99'], new_integration_latency['p99'])
                                                }
                                    continue
                                
                                # Process each endpoint+method combination
                                for method, resource in endpoint_combinations:
                                    endpoint_key = f"{method} {resource}"
                                    
                                    if endpoint_key not in all_api_metrics[api_name][stage_key]:
                                        all_api_metrics[api_name][stage_key][endpoint_key] = {}
                                    
                                    metrics = metrics_collector.get_api_metrics(api_name, stage, method, resource)
                                    
                                    # Merge metrics from different regions
                                    for period_key, period_data in metrics.items():
                                        if period_key not in all_api_metrics[api_name][stage_key][endpoint_key]:
                                            all_api_metrics[api_name][stage_key][endpoint_key][period_key] = period_data.copy()
                                        else:
                                            # Combine metrics from different regions
                                            existing = all_api_metrics[api_name][stage_key][endpoint_key][period_key]
                                            existing['count'] += period_data['count']
                                            existing['error_4xx'] += period_data['error_4xx']
                                            existing['error_5xx'] += period_data['error_5xx']
                                            
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
                                            
                                            existing_integration_latency = existing['integration_latency_stats']
                                            new_integration_latency = period_data['integration_latency_stats']
                                            existing['integration_latency_stats'] = {
                                                'average': (existing_integration_latency['average'] + new_integration_latency['average']) / 2,
                                                'max': max(existing_integration_latency['max'], new_integration_latency['max']),
                                                'min': min(existing_integration_latency['min'], new_integration_latency['min']),
                                                'p50': max(existing_integration_latency['p50'], new_integration_latency['p50']),
                                                'p95': max(existing_integration_latency['p95'], new_integration_latency['p95']),
                                                'p99': max(existing_integration_latency['p99'], new_integration_latency['p99'])
                                            }
                        except Exception as e:
                            print(f"Error processing {api_name} in {region}: {e}")
                            continue
                
                except Exception as e:
                    print(f"Error processing region {region} for account {account_id}: {e}")
                    continue
            
            # Generate output filename
            filename = f"ApiGateway-Performance-{start_date_str}-{end_date_str}.md"
            
            # If multiple accounts, add account ID to filename to avoid collisions
            if len(args.account_ids) > 1:
                filename = f"ApiGateway-Performance-{start_date_str}-{end_date_str}-{account_id}.md"
            
            output_path = output_dir / filename
            
            # Generate report using the first collector's method
            if all_api_metrics:
                temp_collector = ApiGatewayMetrics(
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date,
                    region=args.regions[0]
                )
                
                # Write the report
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                
                markdown_content = temp_collector.generate_markdown_report(all_api_metrics)
                
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

