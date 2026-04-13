# Use the official AWS Lambda Python 3.11 image
FROM public.ecr.aws/lambda/python:3.11

# Install system-level dependencies for geospatial libraries 🛠️
RUN yum install -y gcc gcc-c++ make

# Upgrade pip to the latest version to better handle modern wheels 🛰️
RUN pip install --upgrade pip

# Copy requirements.txt to the task root
COPY requirements.txt ${LAMBDA_TASK_ROOT}

# Install dependencies, forcing binary versions to avoid the GCC error 🚫🏗️
RUN pip install --only-binary=:all: -r requirements.txt

# Copy your code into the task root
COPY src/* ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler
CMD [ "main.lambda_handler" ]