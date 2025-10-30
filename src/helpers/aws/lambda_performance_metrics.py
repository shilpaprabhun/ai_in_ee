import boto3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
import statistics
import calendar

class LambdaPerformanceMetrics:
    def __init__(self, region: str = 'us-east-1', target_month: int = None, target_year: int = None):
        """
        Initialize the Lambda Performance Metrics class.
        
        Args:
            region: AWS region to scan (default: us-east-1)
            target_month: Target month (1-12, default: current month)
            target_year: Target year (default: current year)
        """
        self.region = region
        
        # Set target month and year
        now = datetime.now()
        self.target_month = target_month if target_month else now.month
        self.target_year = target_year if target_year else now.year
        
        # Initialize AWS clients
        self.lambda_client = boto3.client('lambda', region_name=region)
        self.cloudwatch_client = boto3.client('cloudwatch', region_name=region)
        
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
        Retrieve all metrics for a specific Lambda function for the target month.
        
        Args:
            function_name: Name of the Lambda function
            
        Returns:
            Dictionary with monthly metrics
        """
        metrics_by_month = {}
        allocated_memory = self.get_function_memory_size(function_name)
        
        # Calculate start and end dates for the target month
        start_date = datetime(self.target_year, self.target_month, 1)
        if self.target_month == 12:
            end_date = datetime(self.target_year + 1, 1, 1)
        else:
            end_date = datetime(self.target_year, self.target_month + 1, 1)
        
        month_key = start_date.strftime('%b-%y')
        
        # Initialize month data
        month_data = {
            'invocations': 0,
            'errors': 0,
            'duration_values': [],
            'throttles': 0,
            'memory_values': [],
            'allocated_memory': allocated_memory
        }
        
        # Get metrics
        metrics_config = [
            ('Invocations', 'Sum', 'invocations'),
            ('Errors', 'Sum', 'errors'),
            ('Duration', 'Average', 'duration_values'),
            ('Throttles', 'Sum', 'throttles'),
            ('MemoryUtilization', 'Average', 'memory_values')
        ]
        
        for metric_name, statistic, key in metrics_config:
            datapoints = self.get_cloudwatch_metric_data(
                function_name, metric_name, start_date, end_date, statistic
            )
            
            if key == 'duration_values' or key == 'memory_values':
                month_data[key] = [dp[statistic] for dp in datapoints if statistic in dp]
            else:
                month_data[key] = sum(dp[statistic] for dp in datapoints if statistic in dp)
        
        # Calculate duration statistics
        if month_data['duration_values']:
            duration_stats = {
                'average': statistics.mean(month_data['duration_values']),
                'max': max(month_data['duration_values']),
                'min': min(month_data['duration_values'])
            }
            percentiles = self.calculate_percentiles(month_data['duration_values'])
            duration_stats.update(percentiles)
        else:
            duration_stats = {
                'average': 0, 'max': 0, 'min': 0,
                'p50': 0, 'p95': 0, 'p99': 0
            }
        
        # Calculate memory utilization
        if month_data['memory_values']:
            avg_memory_percent = statistics.mean(month_data['memory_values'])
            used_memory = (avg_memory_percent / 100) * allocated_memory
        else:
            avg_memory_percent = 0
            used_memory = 0
        
        # Get cold start data (approximated from duration spikes)
        cold_starts = self.estimate_cold_starts(month_data['duration_values'])
        
        metrics_by_month[month_key] = {
            'invocations': int(month_data['invocations']),
            'errors': int(month_data['errors']),
            'duration_stats': duration_stats,
            'throttles': int(month_data['throttles']),
            'cold_starts': cold_starts,
            'memory_util_percent': round(avg_memory_percent, 1),
            'used_memory': int(used_memory),
            'allocated_memory': allocated_memory
        }
        
        return metrics_by_month
    
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
        
        for function_name, metrics in function_metrics.items():
            markdown += f"## {function_name}\n\n"
            
            # Table header
            markdown += "| Month  | Invocations | Errors | Average (ms) | Max (ms) | Min (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throttles | Cold Starts | Memory Util (%) |\n"
            markdown += "|--------|-------------|--------|--------------|----------|----------|----------|----------|----------|-----------|-------------|----------------|\n"
            
            # Sort months chronologically
            sorted_months = sorted(metrics.keys(), key=lambda x: datetime.strptime(x, '%b-%y'))
            
            for month in sorted_months:
                data = metrics[month]
                stats = data['duration_stats']
                
                # Format numbers with commas
                invocations = f"{data['invocations']:,}"
                memory_util = f"{data['memory_util_percent']} ({data['used_memory']}/{data['allocated_memory']})"
                
                markdown += f"| {month} | {invocations} | {data['errors']} | {stats['average']:.1f} | {stats['max']:.0f} | {stats['min']:.0f} | {stats['p50']:.0f} | {stats['p95']:.0f} | {stats['p99']:.0f} | {data['throttles']} | {data['cold_starts']} | {memory_util} |\n"
            
            markdown += "\n"
        
        return markdown
    
    def generate_performance_report(self, output_dir: str = 'extracted/worker/'):
        """
        Generate the complete performance report for all Lambda functions.
        
        Args:
            output_dir: Directory for the output markdown file
        """
        # Generate filename in MMM-YY format
        target_date = datetime(self.target_year, self.target_month, 1)
        filename = f"Lambda-Performance-{target_date.strftime('%b-%y')}.md"
        output_file = f"{output_dir}{filename}"
        
        print("Retrieving Lambda functions...")
        function_names = self.get_all_lambda_functions()
        print(f"Found {len(function_names)} Lambda functions")
        
        all_metrics = {}
        
        for i, function_name in enumerate(function_names, 1):
            print(f"Processing function {i}/{len(function_names)}: {function_name}")
            metrics = self.get_function_metrics(function_name)
            all_metrics[function_name] = metrics
        
        print("Generating markdown report...")
        markdown_content = self.generate_markdown_report(all_metrics)
        
        with open(output_file, 'w') as f:
            f.write(markdown_content)
        
        print(f"Performance report generated: {output_file}")

if __name__ == "__main__":
    import sys
    
    # Default to current month and year
    target_month = None
    target_year = None
    output_dir = None
    
    # Parse command line arguments
    if len(sys.argv) >= 2:
        target_month = int(sys.argv[1])
    if len(sys.argv) >= 3:
        target_year = int(sys.argv[2])
    if len(sys.argv) >= 4:
        output_dir = sys.argv[3]
    

    # Example usage
    metrics_collector = LambdaPerformanceMetrics(region='us-east-1', target_month=target_month, target_year=target_year)
    metrics_collector.generate_performance_report(output_dir=output_dir)