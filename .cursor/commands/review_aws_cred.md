**CRITICAL** 
When you need information from the user, ask one by one. DO NOT ask all at once.

# Task
Perform the following steps:
1. Ask the user to select the accounts for which the engineering metrics needs to be gathered.
2. Ask the user to select the accounts for which the deployment metrics needs to be gathered.
3. For the unique list of accounts gathered above, check if AWS credentials is configured for all the accounts. The profile name follows the standard AWS format. The available roles are 'PowerUserAccess' and 'ReadOnlyAccessNew'.
4. If the AWS credentials are not configured, ask users if they'd like to sign in with SSO or provide credentials.
5. If they choose SSO, use `aws login sso`. Do not ask for credentials from the user.
6. Otherwise, open the terminal and run `aws configure` with profile name for the accounts that are missing and ask user to enter the necessary data. Do not configure SSO. Once the configuration is done for all selected accounts, continue. 
