# ai_in_ee
An exploration of AI usage in Engineering Excellence.
This repo contains to extract data and metrics from various tools used by Engineering teams including AWS, Github, Jira and Confluence.

# Pre-requisites
1. [Python](https://www.python.org/downloads/)
2. [Pip](https://pip.pypa.io/en/stable/installation/)
3. [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
4. [Githib CLI](https://cli.github.com/)

# Setup
1. Setup virtual environment by running the following command in the terminal
`python -m venv myenv`
2. Run `pip install -r requirements.txt` to install the libraries required for executing the code.
3. If you are gathering data from Atlassian tools, generate an [API token](https://id.atlassian.com/manage-profile/security/api-tokens). Configure the API token and your email address in .env file
4. Setup your AWS credentials with profile name in the form of owerUserAccess-{account_id} or ReadOnlyAccessNew-{account_id}.
Alternately, you could run the code via the Cursor Agent and it will connect to AWS using your SSO.

# Context
The context in terms of AWS accounts, Github repos and Atlasstian config is present in their respective yaml files in context folder. Please feel free to add/update details for your workload and raise a PR.

# Gathering data
The helpers in src/helpers will help in gathering data from AWS, Github and Atlassian. Run the following commands in the termina to gather data. Alternatively you could use the Cursor Agent to issue a command.

## Lambda performance metrics

** Command to run in terminal **
```
python src/helpers/aws/lambda_performance_metrics.py \
  --start-date 2025-10-01 \
  --end-date 2025-10-31 \
  --account-ids 205382370719 \
  --output-dir data/domains/hr/worker/aws/
```

** Cursor agent command **
> Gather lambda metrics for the work load HR/worker prod account only for the month of Oct 2025