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
`python -m venv venv`
2. Run `pip install -r requirements.txt` to install the libraries required for executing the code.
3. If you are gathering data from Atlassian tools, generate an [API token](https://id.atlassian.com/manage-profile/security/api-tokens). Configure the API token and your email address in .env file
4. Update [atlassian.yaml](/context/infra/atlassian.yaml), [aws-accounts.yaml](/context/infra/aws-accounts.yaml), [code-repo.yaml](/context/infra/code-repo.yaml) with info related to your workload.

# Context
The context in terms of AWS accounts, Github repos and Atlasstian config is present in their respective yaml files in context folder. Please feel free to add/update details for your workload and raise a PR.

# Metrics Extractor Agent
The Metrics Extractor Agent streamlines data extraction across AWS, Github, Jira and Confluence. It gathers input from the user, establishes connections with the services, extracts data and aggregates across time periods.