**CRITICAL** 
As you perform your tasks, keep giving updates so the user can understand that the process is progressing.

**CRITICAL** 
When you need information from the user, ask one by one. DO NOT ask all at once.

**CRICIAL**
Gather metrics only for the list of accounts selected for engineering metrics

# Task
Perform the following steps:
1. Ask the user which services need to be included in this exercise? Show the list of Lambda and API Gateway
2. If the user chooses Lambda, run src/helpers/lambda_performance_metrics.py for each of the accounts in parallel.
3. Place the generated file in aws folder data/domains under the right domain and BC with the file name as LambdaPerformance-{env}-{Account-ID}-{start-date}-{end-date}.md
4. If the user chooses API Gateway, run src/helpers/api_gateway_performance_metrics.py for each of the accounts in parallel.
5. Place the generated file in aws folder data/domains under the right domain and BC with the file name as APIGatewayPerformance-{env}-{Account-ID}-{start-date}-{end-date}.md
4. If the user chooses both, run both in parallel.
5. Place the generated file in aws folder data/domains under the right domain and BC.