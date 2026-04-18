# Geospatial AWS Server 

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-FF9900?logo=aws-lambda&logoColor=white)

## 1. Overview & Architecture

The **Geospatial AWS Server** is a serverless, domain-agnostic microservice designed to act as a bridge between cloud storage and web map visualizations. It dynamically fetches heavy GIS files stored in AWS S3 and converts them on-the-fly into web-friendly formats. 

Instead of pre-processing all your data or running a heavy, expensive GIS server (like QGIS/GeoServer) 24/7, this API processes data precisely when a frontend (like Streamlit or Leaflet) requests it.

**Core Capabilities:**
* **Folder-Agnostic File Discovery:** Point it at any S3 folder prefix, and it returns a fully nested JSON file tree representing your storage architecture.
* **On-the-Fly Raster Conversion:** Reads raw `.tif` satellite imagery, scales the data values, masks NoData pixels, and returns a Base64-encoded `.png` ready for web overlay.
* **Dynamic Vector Reprojection:** Reads `.geojson`, `.shp`, and `.gpkg` files and forces conversion to WGS84 (`EPSG:4326`), serving them as web-standard GeoJSON payloads.

**Architecture Flow:**
`Frontend Request` ➡️ `AWS API Gateway (Proxy)` ➡️ `AWS Lambda (This Container)` ➡️ `Fetch from S3` ➡️ `Process (Rasterio/GeoPandas)` ➡️ `Return Web Payload`

---

## 2. Repository Structure

```text
geospatial_aws_server/
├── Dockerfile              # The AWS Lambda Python 3.11 container definition
├── requirements.txt        # Pre-compiled binary dependencies (GDAL/PROJ)
├── README.md               # Documentation
├── src/
│   └── main.py             # The core API routing and processing logic
└── tests/
    ├── __init__.py
    └── test_main.py        # Comprehensive unittest suite with boto3 mocking
```

---

## 3. Prerequisites

Before you can run or deploy this application, ensure you have the following installed and configured:
* **Docker:** Installed and running on your local machine.
* **AWS CLI:** Authenticated with an IAM User that has read access to your target S3 bucket and permissions to push to ECR/Lambda.
* **Python 3.11+:** (Optional, but required if you want to run the test suite locally).

---

## 4. Local Development & Testing

You can build and test the entire Lambda function locally using the built-in AWS Runtime Interface Emulator (RIE). This allows you to simulate API Gateway requests without deploying to the cloud.

### Step 1: Build the Docker Image
Navigate to the root of this repository and build the container. We use the `--only-binary=:all:` flag in the Dockerfile to ensure complex C-libraries install smoothly.

```bash
docker build -t geospatial_aws_server .
```

### Step 2: Run the Container locally
Start the container. You must pass your AWS credentials and target S3 bucket name as environment variables so the local container can access your data.

```cmd
docker run -p 9000:8080 ^
  -e AWS_ACCESS_KEY_ID="YOUR_ACCESS_KEY" ^
  -e AWS_SECRET_ACCESS_KEY="YOUR_SECRET_KEY" ^
  -e AWS_DEFAULT_REGION="eu-north-1" ^
  -e S3_BUCKET_NAME="your-s3-bucket-name" ^
  geospatial_aws_server
```
*(Note: If using Linux/macOS, replace the `^` with `\` for line breaks).* Leave this terminal window open. The server will start and wait for incoming connections.

### Step 3: Send a Test Request
Open a **new terminal window**. Because this is an AWS Lambda Emulator, you must send a POST request formatted exactly how API Gateway would construct it. 

Use this `curl` command to test the file structure route (assuming you have a folder named `portfolio_data` in your bucket):

```bash
curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" \
     -H "Content-Type: application/json" \
     -d "{\"pathParameters\": {\"proxy\": \"api/get-file-structure/portfolio_data\"}}"
```

If successful, you will receive a `200 OK` response containing a nested JSON dictionary of your bucket's contents.

---

## 5. API Reference

Once deployed (or running locally via the emulator), the server acts as a RESTful API. It uses a wildcard proxy structure, meaning you can pass S3 paths of any depth directly into the URL.

### Supported Formats
* **Rasters:** `.tif` (Note: Single-value uniform rasters will return an error).
* **Vectors:** `.geojson`, `.gpkg`, `.shp`.

### Endpoints

#### 1. File Structure Discovery
* **Path:** `GET /api/get-file-structure/{target_folder_name}`
* **Description:** Scans the provided root folder in your S3 bucket and returns its exact layout.
* **Returns:** A nested JSON dictionary where keys are folder/file names, and values are either sub-dictionaries (folders) or full S3 path strings (files).

#### 2. Vector & Raster Data Retrieval
* **Path:** `GET /api/get-data/{full_s3_file_path}`
* **Description:** Dynamically fetches and converts the requested file for web map rendering.
* **Returns (Vectors):** A web-standard `GeoJSON` dictionary. Native files are automatically reprojected to WGS84 (`EPSG:4326`).
* **Returns (Rasters):** A Base64 encoded 8-bit `image/png`. Data values are scaled 0-255, and `NoData` pixels are made transparent.

#### 3. Raster Metadata Retrieval
* **Path:** `GET /api/metadata/{full_s3_file_path}`
* **Description:** Extracts spatial bounding boxes for raster images so the frontend map knows exactly where to overlay the PNG.
* **Returns:** A JSON object containing the `bounds` mapped to `EPSG:4326`.

---

## 6. Running the Test Suite

The repository includes a comprehensive test suite designed to verify API routing, data reprojection logic, and error handling. 

Crucially, **the tests do not require an internet connection or AWS credentials.** They use Python's `unittest.mock` library to simulate `boto3` interactions, ensuring you are never charged for AWS API calls during testing.

To run the tests locally:
```bash
python -m unittest tests/test_main.py
```

---

## 7. AWS Deployment Guide

To take this from your local machine to the cloud, you will push the Docker container to the Amazon Elastic Container Registry (ECR) and attach it to a Lambda function.

### Step 1: Create an ECR Repository
Using the AWS CLI, create a place to store your container images:
```bash
aws ecr create-repository --repository-name geospatial-aws-server --region eu-north-1
```

### Step 2: Authenticate Docker to AWS
Log in your local Docker client to your AWS account (replace `YOUR_ACCOUNT_ID` with your actual 12-digit AWS ID):
```bash
aws ecr get-login-password --region eu-north-1 | docker login --username AWS --password-stdin YOUR_ACCOUNT_ID.dkr.ecr.eu-north-1.amazonaws.com
```

### Step 3: Tag and Push the Image
Tag the image you built locally and push it to the cloud registry:
```bash
docker tag geospatial_aws_server:latest YOUR_ACCOUNT_[ID.dkr.ecr.eu-north-1.amazonaws.com/geospatial-aws-server:latest](https://ID.dkr.ecr.eu-north-1.amazonaws.com/geospatial-aws-server:latest)
docker push YOUR_ACCOUNT_[ID.dkr.ecr.eu-north-1.amazonaws.com/geospatial-aws-server:latest](https://ID.dkr.ecr.eu-north-1.amazonaws.com/geospatial-aws-server:latest)
```

### Step 4: Configure Lambda & API Gateway
1. Open the **AWS Lambda Console** and click **Create Function**.
2. Select **Container Image**, give it a name, and select the image you just pushed to ECR.
3. In the Lambda **Configuration > Environment variables** tab, add `S3_BUCKET_NAME` with your target bucket.
4. In the Lambda **Configuration > Permissions** tab, ensure the Execution Role has `AmazonS3ReadOnlyAccess` so it can fetch your files.
5. Finally, add an **API Gateway** trigger. Set up a **REST API** with a **Proxy Integration** (`/{proxy+}`) so all paths route dynamically to your container.

---